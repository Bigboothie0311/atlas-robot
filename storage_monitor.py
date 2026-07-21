"""Root-filesystem capacity monitoring and safe temp-file/log retention.

Detects the actual mounted root device/filesystem at runtime rather than
trusting a recorded card size, since the SD card can be re-imaged, expanded,
or swapped for the 32GB rollback copy without this module's knowledge.
Large media (recordings, renders, models, archives) belongs on the Windows
PC; this module only reports Pi root-disk pressure and bounds small local
artifacts (temp render files, JSONL logs) that A.T.L.A.S. itself creates.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

import robot_config

BYTES_PER_GB = 1024 ** 3

DEFAULT_WARN_PERCENT = 75.0
DEFAULT_HIGH_PERCENT = 85.0
DEFAULT_CRITICAL_PERCENT = 92.0

DEFAULT_LOG_MAX_BYTES = 5_000_000
DEFAULT_LOG_KEEP_LINES = 5_000


def _threshold(key: str, default: float) -> float:
    return robot_config.get_float(key, default)


def get_root_mount(root: str = "/") -> dict[str, Any]:
    """Detect the actual device and filesystem type backing `root` right
    now, instead of assuming a previously recorded device path."""
    for partition in psutil.disk_partitions(all=False):
        if partition.mountpoint == root:
            return {
                "device": partition.device,
                "fstype": partition.fstype,
            }

    return {"device": None, "fstype": None}


def get_storage_report(root: str = "/") -> dict[str, Any]:
    """Capacity, usage, and threshold state for the given mountpoint."""
    usage = psutil.disk_usage(root)
    mount = get_root_mount(root)

    warn_percent = _threshold("STORAGE_WARN_PERCENT", DEFAULT_WARN_PERCENT)
    high_percent = _threshold("STORAGE_HIGH_PERCENT", DEFAULT_HIGH_PERCENT)
    critical_percent = _threshold(
        "STORAGE_CRITICAL_PERCENT", DEFAULT_CRITICAL_PERCENT
    )

    if usage.percent >= critical_percent:
        level = "critical"
    elif usage.percent >= high_percent:
        level = "high"
    elif usage.percent >= warn_percent:
        level = "warning"
    else:
        level = "ok"

    return {
        "root": root,
        "device": mount["device"],
        "fstype": mount["fstype"],
        "total_gb": round(usage.total / BYTES_PER_GB, 1),
        "used_gb": round(usage.used / BYTES_PER_GB, 1),
        "available_gb": round(usage.free / BYTES_PER_GB, 1),
        "percent": round(usage.percent, 1),
        "warn_percent": warn_percent,
        "high_percent": high_percent,
        "critical_percent": critical_percent,
        "level": level,
        "block_large_writes": usage.percent >= high_percent,
    }


def should_block_large_write(root: str = "/") -> bool:
    """True once usage is at/above the 'high' threshold. Callers placing a
    new large recording/render should route it to the Windows PC instead of
    writing it to the Pi root disk."""
    return get_storage_report(root)["block_large_writes"]


def spoken_storage_warning(report: dict[str, Any] | None = None) -> str | None:
    """A short spoken warning, or None when storage is below the warn
    threshold. Callers decide when/whether to actually speak it."""
    report = report if report is not None else get_storage_report()

    if report["level"] == "ok":
        return None

    return (
        f"Storage is at {report['percent']:.0f} percent on the Pi's root "
        f"drive, with {report['available_gb']:.1f} gigabytes free. Large "
        "new recordings should go to the Windows PC."
    )


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    size_bytes: int


def find_cleanup_candidates(
    directory: str,
    max_age_seconds: int,
    suffixes: tuple[str, ...] = (".tmp", ".partial"),
) -> list[CleanupCandidate]:
    """Locate verified temporary render/export files eligible for cleanup.
    Only files directly inside `directory` with an allowed temp suffix and
    older than max_age_seconds qualify — source media, final exports,
    mission evidence, unreviewed intruder photos, and rollback data never
    carry these suffixes, so they are never candidates."""
    base = Path(directory).expanduser().resolve()

    if not base.is_dir():
        return []

    now = time.time()
    candidates: list[CleanupCandidate] = []

    for entry in sorted(base.iterdir(), key=lambda item: item.name):
        if entry.is_symlink() or not entry.is_file():
            continue

        if entry.suffix.casefold() not in suffixes:
            continue

        try:
            stat = entry.stat()
        except OSError:
            continue

        if now - stat.st_mtime < max_age_seconds:
            continue

        candidates.append(
            CleanupCandidate(path=entry, size_bytes=stat.st_size)
        )

    return candidates


def cleanup_verified_temp_files(
    directory: str,
    max_age_seconds: int = 3600,
    suffixes: tuple[str, ...] = (".tmp", ".partial"),
) -> dict[str, Any]:
    """Delete only bounded, verified temp-render files older than
    max_age_seconds. Never touches source media, final exports, mission
    evidence, unreviewed intruder photos, or rollback data — none of those
    use a temp suffix, so none of them are ever candidates here."""
    candidates = find_cleanup_candidates(directory, max_age_seconds, suffixes)
    removed: list[str] = []
    freed_bytes = 0

    for candidate in candidates:
        try:
            candidate.path.unlink()
        except OSError:
            continue

        removed.append(str(candidate.path))
        freed_bytes += candidate.size_bytes

    return {
        "directory": str(Path(directory).expanduser().resolve()),
        "removed": removed,
        "removed_count": len(removed),
        "freed_bytes": freed_bytes,
        "freed_gb": round(freed_bytes / BYTES_PER_GB, 3),
    }


def rotate_bounded_jsonl(
    path: str,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    keep_lines: int = DEFAULT_LOG_KEEP_LINES,
) -> dict[str, Any]:
    """Bound a JSONL log's size by keeping only its most recent
    `keep_lines` lines once it exceeds `max_bytes`. No-op when the file is
    missing or already under the size cap."""
    log_path = Path(path).expanduser().resolve()

    if not log_path.is_file():
        return {"path": str(log_path), "rotated": False, "reason": "missing"}

    size_before = log_path.stat().st_size

    if size_before <= max_bytes:
        return {
            "path": str(log_path),
            "rotated": False,
            "size_bytes": size_before,
        }

    lines = log_path.read_text().splitlines()
    trimmed = lines[-keep_lines:]

    temporary_path = log_path.with_suffix(log_path.suffix + ".tmp")
    temporary_path.write_text(
        "\n".join(trimmed) + ("\n" if trimmed else "")
    )
    temporary_path.replace(log_path)

    return {
        "path": str(log_path),
        "rotated": True,
        "size_bytes_before": size_before,
        "size_bytes_after": log_path.stat().st_size,
        "lines_kept": len(trimmed),
    }

"""Build a complete local distribution package from one finished Atlas Reel."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import content_pipeline
from atlas_growth import build_collaboration_kit


PLATFORMS = {
    "instagram": {"title_limit": 0, "caption_filename": "caption.txt"},
    "facebook": {"title_limit": 0, "caption_filename": "caption.txt"},
    "youtube_shorts": {"title_limit": 100, "caption_filename": "description.txt"},
    "tiktok": {"title_limit": 0, "caption_filename": "caption.txt"},
}


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(str(text), encoding="utf-8")
    temporary.replace(path)


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(float(seconds) * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, fraction = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{fraction:03d}"


def _write_srt(path: Path, cues: list[dict[str, Any]]) -> None:
    blocks = []
    for index, cue in enumerate(cues, 1):
        blocks.extend(
            [
                str(index),
                f"{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}",
                str(cue.get("text") or "").strip(),
                "",
            ]
        )
    _write_text(path, "\n".join(blocks).rstrip() + "\n")


def create_distribution_package(
    *,
    master_video: str | Path,
    package_directory: str | Path,
    plan: dict[str, Any],
    caption: str,
    cues: list[dict[str, Any]],
    translations: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create Trial candidates, cover, subtitles, and four platform bundles.

    This is deliberately preparation-only.  There is no network or publish
    operation in this module.
    """
    source = Path(master_video).resolve()
    package = Path(package_directory).resolve()
    package.mkdir(parents=True, exist_ok=True)

    master = package / "atlas_master.mp4"
    _link_or_copy(source, master)
    cover = Path(
        content_pipeline.create_reel_cover(
            master,
            package / "cover.png",
            str(plan.get("title") or "A.T.L.A.S. Raspberry Pi Project"),
        )
    )
    _write_srt(package / "subtitles" / "en.srt", cues)

    trial_paths = []
    candidates = plan.get("hook_candidates") or []
    for index, candidate in enumerate(candidates[:2]):
        hook = str(candidate.get("text") if isinstance(candidate, dict) else candidate)
        name = chr(ord("A") + index)
        variant = Path(
            content_pipeline.create_hook_variant(
                master,
                package / "trials" / f"trial_{name.lower()}.mp4",
                hook,
            )
        )
        trial_paths.append(
            {"name": name, "hook": hook, "video_path": str(variant)}
        )

    platform_exports = {}
    platform_caption = content_pipeline.ensure_raspberry_pi_hashtags(caption)
    _write_text(package / "caption.txt", platform_caption + "\n")
    for platform, settings in PLATFORMS.items():
        platform_path = package / "platforms" / platform
        video = platform_path / "reel.mp4"
        _link_or_copy(master, video)
        _write_text(platform_path / settings["caption_filename"], platform_caption + "\n")
        if settings["title_limit"]:
            _write_text(
                platform_path / "title.txt",
                str(plan.get("title") or "A.T.L.A.S. Raspberry Pi Project")[: settings["title_limit"]] + "\n",
            )
        _write_json(
            platform_path / "metadata.json",
            {
                "platform": platform,
                "publish_status": "requires_owner_approval",
                "video_path": str(video),
                "cover_path": str(cover),
                "series": plan.get("series"),
                "hook": plan.get("hook"),
                "cta": plan.get("cta"),
            },
        )
        platform_exports[platform] = str(platform_path)

    translation_outputs = {}
    for language, translated in (translations or {}).items():
        safe_language = re.sub(r"[^a-z0-9_-]", "", language.casefold())[:20]
        if not safe_language or not isinstance(translated, dict):
            continue
        translated_cues = translated.get("cues")
        if isinstance(translated_cues, list) and len(translated_cues) == len(cues):
            localized = [
                {**cue, "text": str(translated_cues[index])}
                for index, cue in enumerate(cues)
            ]
            _write_srt(package / "subtitles" / f"{safe_language}.srt", localized)
        translated_caption = str(translated.get("caption") or "").strip()
        if translated_caption:
            _write_text(
                package / "translations" / f"{safe_language}_caption.txt",
                translated_caption + "\n",
            )
        translation_outputs[safe_language] = {
            "subtitle_path": str(package / "subtitles" / f"{safe_language}.srt"),
            "caption_path": str(package / "translations" / f"{safe_language}_caption.txt"),
        }

    collaboration = build_collaboration_kit(
        title=str(plan.get("title") or "Raspberry Pi project"),
        series=str(plan.get("series") or "Building Atlas"),
        hook=str(plan.get("hook") or "Watch Atlas build this."),
        cta=str(plan.get("cta") or "What should I build next?"),
        package_path=str(package),
    )
    _write_json(package / "collaboration_kit.json", collaboration)
    _write_text(
        package / "collaboration_pitch.txt",
        collaboration["draft_message"] + "\n",
    )

    manifest = {
        "version": 1,
        "status": "prepared_not_published",
        "master_video": str(master),
        "cover": str(cover),
        "series": plan.get("series"),
        "title": plan.get("title"),
        "selected_hook": plan.get("hook"),
        "hook_score": plan.get("hook_score"),
        "cta": plan.get("cta"),
        "trial_variants": trial_paths,
        "platform_exports": platform_exports,
        "translations": translation_outputs,
        "collaboration_kit": str(package / "collaboration_kit.json"),
        "external_actions_taken": [],
    }
    _write_json(package / "manifest.json", manifest)
    return manifest

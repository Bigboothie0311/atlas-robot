import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import storage_monitor


class GetRootMountTests(unittest.TestCase):
    def test_returns_matching_partition_device_and_fstype(self):
        partitions = [
            SimpleNamespace(mountpoint="/boot", device="/dev/x1", fstype="vfat"),
            SimpleNamespace(mountpoint="/", device="/dev/mmcblk0p2", fstype="ext4"),
        ]

        with mock.patch.object(
            storage_monitor.psutil, "disk_partitions", return_value=partitions
        ):
            mount = storage_monitor.get_root_mount("/")

        self.assertEqual(mount, {"device": "/dev/mmcblk0p2", "fstype": "ext4"})

    def test_returns_none_when_no_partition_matches(self):
        with mock.patch.object(
            storage_monitor.psutil, "disk_partitions", return_value=[]
        ):
            mount = storage_monitor.get_root_mount("/")

        self.assertEqual(mount, {"device": None, "fstype": None})


class GetStorageReportTests(unittest.TestCase):
    def _usage(self, percent):
        total = 64 * storage_monitor.BYTES_PER_GB
        used = int(total * percent / 100)
        free = total - used
        return SimpleNamespace(total=total, used=used, free=free, percent=percent)

    def test_reports_ok_below_warn_threshold(self):
        with (
            mock.patch.object(
                storage_monitor.psutil, "disk_usage", return_value=self._usage(28.0)
            ),
            mock.patch.object(
                storage_monitor,
                "get_root_mount",
                return_value={"device": "/dev/mmcblk0p2", "fstype": "ext4"},
            ),
        ):
            report = storage_monitor.get_storage_report("/")

        self.assertEqual(report["level"], "ok")
        self.assertFalse(report["block_large_writes"])
        self.assertEqual(report["device"], "/dev/mmcblk0p2")
        self.assertAlmostEqual(report["percent"], 28.0)

    def test_reports_warning_between_warn_and_high(self):
        with (
            mock.patch.object(
                storage_monitor.psutil, "disk_usage", return_value=self._usage(80.0)
            ),
            mock.patch.object(
                storage_monitor, "get_root_mount", return_value={"device": None, "fstype": None}
            ),
        ):
            report = storage_monitor.get_storage_report("/")

        self.assertEqual(report["level"], "warning")
        self.assertFalse(report["block_large_writes"])

    def test_reports_high_and_blocks_large_writes(self):
        with (
            mock.patch.object(
                storage_monitor.psutil, "disk_usage", return_value=self._usage(88.0)
            ),
            mock.patch.object(
                storage_monitor, "get_root_mount", return_value={"device": None, "fstype": None}
            ),
        ):
            report = storage_monitor.get_storage_report("/")

        self.assertEqual(report["level"], "high")
        self.assertTrue(report["block_large_writes"])

    def test_reports_critical_at_configured_threshold(self):
        with (
            mock.patch.object(
                storage_monitor.psutil, "disk_usage", return_value=self._usage(95.0)
            ),
            mock.patch.object(
                storage_monitor, "get_root_mount", return_value={"device": None, "fstype": None}
            ),
        ):
            report = storage_monitor.get_storage_report("/")

        self.assertEqual(report["level"], "critical")
        self.assertTrue(report["block_large_writes"])

    def test_thresholds_are_configurable_via_robot_config(self):
        with (
            mock.patch.object(
                storage_monitor.psutil, "disk_usage", return_value=self._usage(60.0)
            ),
            mock.patch.object(
                storage_monitor, "get_root_mount", return_value={"device": None, "fstype": None}
            ),
            mock.patch.object(
                storage_monitor.robot_config,
                "get_float",
                side_effect=lambda key, default: {
                    "STORAGE_WARN_PERCENT": 50.0,
                }.get(key, default),
            ),
        ):
            report = storage_monitor.get_storage_report("/")

        self.assertEqual(report["level"], "warning")


class ShouldBlockLargeWriteTests(unittest.TestCase):
    def test_delegates_to_storage_report(self):
        with mock.patch.object(
            storage_monitor,
            "get_storage_report",
            return_value={"block_large_writes": True},
        ):
            self.assertTrue(storage_monitor.should_block_large_write())


class SpokenStorageWarningTests(unittest.TestCase):
    def test_returns_none_when_ok(self):
        message = storage_monitor.spoken_storage_warning(
            {"level": "ok", "percent": 20.0, "available_gb": 40.0}
        )
        self.assertIsNone(message)

    def test_mentions_percent_and_available_space_when_warning(self):
        message = storage_monitor.spoken_storage_warning(
            {"level": "warning", "percent": 80.0, "available_gb": 12.3}
        )
        self.assertIn("80", message)
        self.assertIn("12.3", message)
        self.assertIn("Windows PC", message)


def _age_file(path, seconds_old):
    old_time = time.time() - seconds_old
    os.utime(path, (old_time, old_time))


def test_find_cleanup_candidates_only_matches_old_temp_suffixed_files(tmp_path):
    old_temp = tmp_path / "render_1.tmp"
    old_temp.write_text("x" * 10)
    _age_file(old_temp, 7200)

    fresh_temp = tmp_path / "render_2.tmp"
    fresh_temp.write_text("x" * 10)

    source_media = tmp_path / "final_export.mp4"
    source_media.write_text("keep me")
    _age_file(source_media, 7200)

    candidates = storage_monitor.find_cleanup_candidates(
        str(tmp_path), max_age_seconds=3600
    )

    names = {candidate.path.name for candidate in candidates}
    assert names == {"render_1.tmp"}


def test_find_cleanup_candidates_skips_symlinks(tmp_path):
    real_target = tmp_path / "real.tmp"
    real_target.write_text("data")
    _age_file(real_target, 7200)

    link = tmp_path / "linked.tmp"
    link.symlink_to(real_target)

    candidates = storage_monitor.find_cleanup_candidates(
        str(tmp_path), max_age_seconds=3600
    )
    names = {candidate.path.name for candidate in candidates}

    assert "linked.tmp" not in names
    assert "real.tmp" in names


def test_cleanup_verified_temp_files_removes_and_reports_freed_bytes(tmp_path):
    stale = tmp_path / "old.partial"
    stale.write_text("y" * 100)
    _age_file(stale, 7200)

    keep = tmp_path / "keep.partial"
    keep.write_text("z" * 50)

    result = storage_monitor.cleanup_verified_temp_files(
        str(tmp_path), max_age_seconds=3600, suffixes=(".partial",)
    )

    assert result["removed_count"] == 1
    assert result["freed_bytes"] == 100
    assert not stale.exists()
    assert keep.exists()


def test_cleanup_verified_temp_files_never_touches_non_temp_files(tmp_path):
    evidence = tmp_path / "intruder_photo.jpg"
    evidence.write_bytes(b"photo")
    _age_file(evidence, 100_000)

    result = storage_monitor.cleanup_verified_temp_files(str(tmp_path))

    assert result["removed_count"] == 0
    assert evidence.exists()


def test_rotate_bounded_jsonl_is_noop_when_under_size_cap(tmp_path):
    log_path = tmp_path / "small.jsonl"
    log_path.write_text('{"a": 1}\n')

    result = storage_monitor.rotate_bounded_jsonl(str(log_path), max_bytes=1_000_000)

    assert result["rotated"] is False
    assert log_path.read_text() == '{"a": 1}\n'


def test_rotate_bounded_jsonl_is_noop_when_missing(tmp_path):
    missing_path = tmp_path / "missing.jsonl"

    result = storage_monitor.rotate_bounded_jsonl(str(missing_path))

    assert result["rotated"] is False
    assert result["reason"] == "missing"


def test_rotate_bounded_jsonl_keeps_only_the_most_recent_lines(tmp_path):
    log_path = tmp_path / "big.jsonl"
    lines = [f'{{"n": {i}}}' for i in range(100)]
    log_path.write_text("\n".join(lines) + "\n")

    result = storage_monitor.rotate_bounded_jsonl(
        str(log_path), max_bytes=1, keep_lines=10
    )

    remaining = log_path.read_text().splitlines()
    assert result["rotated"] is True
    assert remaining == lines[-10:]


if __name__ == "__main__":
    unittest.main()

"""Structured read-only diagnostics (Phase 2)."""
import json

import pytest

import diagnostics


def test_structured_components_cover_required_systems():
    required = {
        "services",
        "microphone",
        "speaker",
        "camera",
        "pc_companion",
        "direct_ethernet",
        "wifi",
        "disk",
        "temperature",
        "budget",
        "mission_store",
        "instagram_refresher",
        "printer",
        "voice_provider",
    }

    assert required == set(diagnostics.STRUCTURED_COMPONENTS)


def test_run_structured_checks_returns_all_components_in_order(
    monkeypatch,
):
    for name in diagnostics.STRUCTURED_COMPONENTS:
        monkeypatch.setitem(
            diagnostics._STRUCTURED_CHECKS,
            name,
            lambda name=name: diagnostics._finding(
                name, True, f"{name} fine"
            ),
        )

    findings = diagnostics.run_structured_checks()

    assert [f["component"] for f in findings] == list(
        diagnostics.STRUCTURED_COMPONENTS
    )
    assert all(f["ok"] is True for f in findings)
    assert all(
        isinstance(f["detail"], str) and f["detail"]
        for f in findings
    )


def test_run_structured_checks_selects_requested_subset(
    monkeypatch,
):
    for name in diagnostics.STRUCTURED_COMPONENTS:
        monkeypatch.setitem(
            diagnostics._STRUCTURED_CHECKS,
            name,
            lambda name=name: diagnostics._finding(
                name, True, "fine"
            ),
        )

    findings = diagnostics.run_structured_checks(
        ["disk", "wifi", "disk"]
    )

    assert [f["component"] for f in findings] == [
        "disk",
        "wifi",
    ]


def test_run_structured_checks_rejects_unknown_component():
    with pytest.raises(ValueError):
        diagnostics.run_structured_checks(["warp_core"])


def test_run_structured_checks_rejects_empty_list():
    with pytest.raises(ValueError):
        diagnostics.run_structured_checks([])


def test_crashing_check_reports_honestly_instead_of_raising(
    monkeypatch,
):
    def boom():
        raise OSError("probe failed")

    monkeypatch.setitem(
        diagnostics._STRUCTURED_CHECKS, "camera", boom
    )

    findings = diagnostics.run_structured_checks(["camera"])

    assert findings[0]["component"] == "camera"
    assert findings[0]["ok"] is False
    assert "could not run" in findings[0]["detail"]


def test_check_services_reports_down_units(monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "_services_status",
        lambda units=None: (4, 5, ["atlas-hud"]),
    )

    finding = diagnostics._check_services()

    assert finding["ok"] is False
    assert "atlas-hud" in finding["detail"]


def test_check_services_reports_all_active(monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "_services_status",
        lambda units=None: (5, 5, []),
    )

    finding = diagnostics._check_services()

    assert finding["ok"] is True
    assert "5" in finding["detail"]


def test_check_camera_reports_missing_device(monkeypatch):
    monkeypatch.setattr(
        diagnostics, "_camera_device_nodes", lambda: []
    )

    finding = diagnostics._check_camera()

    assert finding["ok"] is False
    assert "no camera" in finding["detail"].lower()


def _write_video_node(sysfs_root, node, name):
    node_dir = sysfs_root / node
    node_dir.mkdir(parents=True)
    (node_dir / "name").write_text(name + "\n")


def test_camera_nodes_ignore_pi_codec_and_isp_devices(
    monkeypatch, tmp_path
):
    _write_video_node(tmp_path, "video19", "rpi-hevc-dec")
    _write_video_node(tmp_path, "video20", "pispbe-input")
    monkeypatch.setattr(
        diagnostics, "_VIDEO4LINUX_SYSFS", tmp_path
    )

    assert diagnostics._camera_device_nodes() == []


def test_camera_nodes_detect_real_usb_capture_device(
    monkeypatch, tmp_path
):
    _write_video_node(
        tmp_path, "video0", "icspring camera"
    )
    _write_video_node(tmp_path, "video19", "rpi-hevc-dec")
    monkeypatch.setattr(
        diagnostics, "_VIDEO4LINUX_SYSFS", tmp_path
    )

    assert diagnostics._camera_device_nodes() == [
        "/dev/video0"
    ]


def test_check_budget_uses_real_ledger_summary(monkeypatch):
    import cost_ledger

    monkeypatch.setattr(
        cost_ledger,
        "budget_summary",
        lambda: {
            "spent_usd": 2.5,
            "limit_usd": 8.0,
            "fallback_active": False,
            "premium_voice": {
                "should_fallback_to_local": False,
            },
        },
    )

    finding = diagnostics._check_budget()

    assert finding["ok"] is True
    assert "2.50" in finding["detail"]


def test_check_budget_flags_exhausted_budget(monkeypatch):
    import cost_ledger

    monkeypatch.setattr(
        cost_ledger,
        "budget_summary",
        lambda: {
            "spent_usd": 8.2,
            "limit_usd": 8.0,
            "fallback_active": True,
            "premium_voice": {
                "should_fallback_to_local": False,
            },
        },
    )

    finding = diagnostics._check_budget()

    assert finding["ok"] is False


def test_check_mission_store_counts_missions(
    monkeypatch, tmp_path
):
    store = tmp_path / "agent_missions.json"
    store.write_text(
        json.dumps(
            {
                "saved_at": "now",
                "version": 1,
                "tasks": [{"goal": "a"}, {"goal": "b"}],
            }
        )
    )
    monkeypatch.setattr(
        diagnostics, "MISSION_STORE_PATH", store
    )

    finding = diagnostics._check_mission_store()

    assert finding["ok"] is True
    assert "2" in finding["detail"]


def test_check_mission_store_flags_corrupt_file(
    monkeypatch, tmp_path
):
    store = tmp_path / "agent_missions.json"
    store.write_text("{not json")
    monkeypatch.setattr(
        diagnostics, "MISSION_STORE_PATH", store
    )

    finding = diagnostics._check_mission_store()

    assert finding["ok"] is False


def test_check_mission_store_missing_file_is_not_a_fault(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        diagnostics,
        "MISSION_STORE_PATH",
        tmp_path / "absent.json",
    )

    finding = diagnostics._check_mission_store()

    assert finding["ok"] is True
    assert "not created" in finding["detail"]


def test_check_direct_ethernet_maps_connection_health(
    monkeypatch,
):
    import connection_health

    monkeypatch.setattr(
        connection_health,
        "check_direct_link",
        lambda: {
            "name": "direct PC link",
            "ok": False,
            "detail": "Ethernet port is down",
            "recovery": "check cable",
        },
    )

    finding = diagnostics._check_direct_ethernet()

    assert finding["component"] == "direct_ethernet"
    assert finding["ok"] is False
    assert finding["detail"] == "Ethernet port is down"


def test_check_instagram_refresher_unconfigured_is_ok(
    monkeypatch,
):
    import instagram_stats

    monkeypatch.setattr(
        instagram_stats,
        "get_stats",
        lambda allow_fetch=True: {
            "configured": False,
        },
    )

    finding = diagnostics._check_instagram_refresher()

    assert finding["ok"] is True
    assert "not configured" in finding["detail"]


def test_check_instagram_refresher_reports_fetch_error(
    monkeypatch,
):
    import instagram_stats

    monkeypatch.setattr(
        instagram_stats,
        "get_stats",
        lambda allow_fetch=True: {
            "configured": True,
            "error": "token expired",
        },
    )

    finding = diagnostics._check_instagram_refresher()

    assert finding["ok"] is False
    assert "token expired" in finding["detail"]


def test_check_voice_provider_reports_local_stack(
    monkeypatch, tmp_path
):
    import self_healing

    binary = tmp_path / "whisper-cli"
    model = tmp_path / "model.bin"
    binary.write_text("x")
    model.write_text("x")
    monkeypatch.setattr(
        self_healing, "WHISPER_CLI", binary
    )
    monkeypatch.setattr(
        self_healing, "WHISPER_MODEL", model
    )

    finding = diagnostics._check_voice_provider()

    assert finding["ok"] is True
    assert "local" in finding["detail"].lower()


def test_check_voice_provider_flags_missing_whisper(
    monkeypatch, tmp_path
):
    import self_healing

    monkeypatch.setattr(
        self_healing,
        "WHISPER_CLI",
        tmp_path / "absent-cli",
    )
    monkeypatch.setattr(
        self_healing,
        "WHISPER_MODEL",
        tmp_path / "absent-model",
    )

    finding = diagnostics._check_voice_provider()

    assert finding["ok"] is False
    assert "vosk" in finding["detail"].lower()


def test_spoken_structured_report_all_nominal():
    findings = [
        diagnostics._finding(name, True, "fine")
        for name in diagnostics.STRUCTURED_COMPONENTS
    ]

    report = diagnostics.spoken_structured_report(findings)

    assert "14" in report
    assert "nominal" in report.lower()
    assert "fine" not in report  # no per-check noise when healthy


def test_spoken_structured_report_lists_problems():
    findings = [
        diagnostics._finding("services", True, "all active"),
        diagnostics._finding(
            "camera", False, "no camera device connected"
        ),
        diagnostics._finding(
            "wifi", False, "Wi-Fi down"
        ),
    ]

    report = diagnostics.spoken_structured_report(findings)

    assert "camera" in report
    assert "no camera device connected" in report
    assert "Wi-Fi down" in report
    assert "2" in report


def test_spoken_structured_report_empty_is_honest():
    report = diagnostics.spoken_structured_report([])

    assert "no diagnostic" in report.lower()

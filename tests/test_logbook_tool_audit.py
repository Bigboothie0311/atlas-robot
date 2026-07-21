"""Durable tool-audit records (Phase 2)."""
import logbook


def test_tool_audit_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(logbook, "LOG_DIR", tmp_path)
    monkeypatch.setattr(
        logbook,
        "TOOL_AUDIT_PATH",
        tmp_path / "tool_audit.jsonl",
    )

    logbook.record_tool_audit(
        {
            "tool_name": "pi.recover_component",
            "status": "success",
            "permission_level": 1,
            "permission_outcome": "allow_logged",
            "duration_ms": 4200.5,
            "error": None,
        }
    )

    records = logbook.read_tool_audit(5)

    assert len(records) == 1
    assert records[0]["tool_name"] == (
        "pi.recover_component"
    )
    assert records[0]["permission_outcome"] == (
        "allow_logged"
    )
    assert "ts" in records[0]


def test_tool_audit_bounds_error_text(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(logbook, "LOG_DIR", tmp_path)
    monkeypatch.setattr(
        logbook,
        "TOOL_AUDIT_PATH",
        tmp_path / "tool_audit.jsonl",
    )

    logbook.record_tool_audit(
        {
            "tool_name": "test.tool",
            "status": "error",
            "permission_level": 0,
            "permission_outcome": "allow",
            "duration_ms": 1.0,
            "error": "x" * 2000,
        }
    )

    records = logbook.read_tool_audit(5)

    assert len(records[0]["error"]) <= 300


def test_tool_audit_read_missing_file_is_empty(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        logbook,
        "TOOL_AUDIT_PATH",
        tmp_path / "absent.jsonl",
    )

    assert logbook.read_tool_audit(5) == []

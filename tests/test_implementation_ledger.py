import unittest
from pathlib import Path

import implementation_ledger as ledger_module


def _ledger_path(tmp_path):
    return Path(tmp_path) / "ledger.json"


def test_load_ledger_seeds_all_default_features_when_missing(tmp_path):
    path = _ledger_path(tmp_path)

    ledger = ledger_module.load_ledger(path)

    assert set(ledger) == {
        feature["feature_id"] for feature in ledger_module.DEFAULT_FEATURES
    }
    assert all(entry["state"] == "not_started" for entry in ledger.values())
    assert not path.exists()


def test_upsert_feature_persists_state_and_evidence(tmp_path):
    path = _ledger_path(tmp_path)

    updated = ledger_module.upsert_feature(
        "phase1a_storage_monitoring",
        path=path,
        state="implemented",
        commits=["abc1234"],
        tests=["tests/test_storage_monitor.py"],
        evidence=["17 focused tests passing"],
    )

    assert updated["state"] == "implemented"
    assert updated["commits"] == ["abc1234"]
    assert path.exists()

    reloaded = ledger_module.load_ledger(path)
    assert reloaded["phase1a_storage_monitoring"]["state"] == "implemented"
    assert reloaded["phase1a_storage_monitoring"]["commits"] == ["abc1234"]


def test_upsert_feature_rejects_unknown_feature_id(tmp_path):
    path = _ledger_path(tmp_path)

    try:
        ledger_module.upsert_feature("not_a_real_feature", path=path, state="implemented")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_upsert_feature_rejects_invalid_state(tmp_path):
    path = _ledger_path(tmp_path)

    try:
        ledger_module.upsert_feature(
            "phase1a_storage_monitoring", path=path, state="finished_forever"
        )
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_upsert_feature_never_marks_live_verified_implicitly(tmp_path):
    """Guards the rule that only an explicit, deliberate state update
    can claim live_verified — upsert never infers it from tests/commits
    being present."""
    path = _ledger_path(tmp_path)

    updated = ledger_module.upsert_feature(
        "phase1b_budget_ledger",
        path=path,
        commits=["def5678"],
        tests=["tests/test_cost_ledger.py"],
    )

    assert updated["state"] == "not_started"


def test_list_by_state_filters_correctly(tmp_path):
    path = _ledger_path(tmp_path)
    ledger_module.upsert_feature(
        "phase1a_storage_monitoring", path=path, state="implemented"
    )
    ledger_module.upsert_feature(
        "phase7_gmail_agent",
        path=path,
        state="blocked_external",
        external_blockers=["no Gmail API credentials configured"],
    )

    implemented = ledger_module.list_by_state("implemented", path=path)
    blocked = ledger_module.list_by_state("blocked_external", path=path)

    assert [entry["feature_id"] for entry in implemented] == [
        "phase1a_storage_monitoring"
    ]
    assert [entry["feature_id"] for entry in blocked] == ["phase7_gmail_agent"]


def test_summarize_reports_finished_remaining_blocked_and_last_updated(tmp_path):
    path = _ledger_path(tmp_path)
    ledger_module.upsert_feature(
        "phase1a_storage_monitoring", path=path, state="implemented"
    )
    ledger_module.upsert_feature(
        "phase1b_budget_ledger", path=path, state="implemented"
    )
    ledger_module.upsert_feature(
        "phase7_gmail_agent", path=path, state="blocked_external"
    )

    summary = ledger_module.summarize(path)

    assert summary["counts"]["finished"] == 2
    assert summary["counts"]["blocked"] == 1
    assert (
        summary["counts"]["remaining"]
        == summary["counts"]["total"] - 2 - 1
    )
    assert summary["last_updated_feature"]["feature_id"] == "phase7_gmail_agent"


def test_spoken_summary_is_bounded_and_mentions_last_finished_item(tmp_path):
    path = _ledger_path(tmp_path)
    ledger_module.upsert_feature(
        "phase1a_storage_monitoring", path=path, state="implemented"
    )

    message = ledger_module.spoken_summary(path)

    assert "implemented" in message
    assert "Storage monitoring" in message
    assert len(message) < 400


if __name__ == "__main__":
    unittest.main()

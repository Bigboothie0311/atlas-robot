"""Regression: capabilities.REGISTRY must list every real device action so
the model's system prompt (capabilities.instruction_summary()) never tells
it to refuse something it can actually do via run_atlas_agent."""
import capabilities


def test_registry_has_no_duplicate_ids():
    ids = [entry["id"] for entry in capabilities.REGISTRY]
    assert len(ids) == len(set(ids))


def test_self_recording_clip_is_not_listed():
    """Confirmed live 2026-07-20/21: the physical camera faces the room,
    not Atlas, so this must not be offered to voice until that's fixed --
    see the comment above self_record_clip's old spot in capabilities.py."""
    ids = {entry["id"] for entry in capabilities.REGISTRY}
    assert "self_record_clip" not in ids


def test_pc_screen_recording_is_listed():
    ids = {entry["id"] for entry in capabilities.REGISTRY}
    assert "pc_screen_recording" in ids


def test_instruction_summary_mentions_recording():
    summary = capabilities.instruction_summary()
    assert "self-recording clip" not in summary
    assert "PC screen recording" in summary


def test_self_showcase_and_instagram_publish_are_listed():
    ids = {entry["id"] for entry in capabilities.REGISTRY}
    assert "self_showcase_record" in ids
    assert "instagram_publish" in ids


def test_instagram_publish_requires_confirmation():
    entry = next(
        entry for entry in capabilities.REGISTRY
        if entry["id"] == "instagram_publish"
    )
    assert entry["confirm"] is True

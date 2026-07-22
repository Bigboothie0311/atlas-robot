import wave
import json
from pathlib import Path
from unittest import mock

from atlas_agent.content_tools import (
    DIAGNOSTICS_LINES,
    HUD_FEATURE_BEATS,
    INTRO_LINES,
    MAX_FEATURE_BEATS,
    OUTRO_LINES,
    SHOWCASE_HISTORY_FILENAME,
    SHOWCASE_TOOL_TIMEOUT_SECONDS,
    TYPING_LEAD_SECONDS,
    PcDemoCaptureError,
    WEATHER_LINES,
    _build_default_tour,
    _ensure_required_pc_scene,
    _export_reel_to_desktop,
    _live_context,
    _perform_pc_action,
    _resolve_tour,
    register_content_tools,
)
from atlas_agent.executor import ToolExecutor
from atlas_agent.results import ResultStatus
from atlas_agent.tasks import ToolCall
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.verifier import ResultVerifier

import content_pipeline
import diagnostics
import facebook_publish
import hud_capture
import instagram_publish
import social_publish
import youtube_publish
import atlas_agent.content_tools as content_tools_module


def _write_wav(path: Path, seconds: float = 0.2) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * int(16000 * seconds))


def build_tools(
    tmp_path,
    *,
    enable_facebook_publish=False,
    enable_youtube_publish=False,
    enable_combined_social_publish=False,
):
    registry = ToolRegistry()
    verifier = ResultVerifier()

    tools = register_content_tools(
        registry,
        verifier,
        staging_directory=tmp_path,
        enable_facebook_publish=enable_facebook_publish,
        enable_youtube_publish=enable_youtube_publish,
        enable_combined_social_publish=enable_combined_social_publish,
    )

    return registry, verifier, tools


def execute(registry: ToolRegistry, call: ToolCall, confirmed: bool = False):
    with ToolExecutor(registry) as executor:
        return executor.execute(call, confirmed=confirmed)


def test_content_tools_are_registered(tmp_path):
    registry, _verifier, tools = build_tools(tmp_path)

    assert len(tools) == 4
    names = {tool.name for tool in registry.list_tools()}
    assert names == {
        "content.record_self_showcase",
        "content.save_showcase",
        "content.delete_showcase",
        "content.publish_to_instagram",
    }
    assert registry.get("content.record_self_showcase").permission_level == 0
    assert registry.get("content.record_self_showcase").timeout_seconds == (
        SHOWCASE_TOOL_TIMEOUT_SECONDS
    )
    assert registry.get("content.publish_to_instagram").permission_level == 2
    assert registry.get("content.save_showcase").permission_level == 0
    assert registry.get("content.delete_showcase").permission_level == 2
    assert "content.publish_to_youtube" not in registry
    assert "content.publish_to_facebook" not in registry


def test_growth_tools_are_read_only_and_internal_when_enabled(tmp_path):
    registry = ToolRegistry()
    verifier = ResultVerifier()
    tools = register_content_tools(
        registry,
        verifier,
        staging_directory=tmp_path,
        enable_growth_package=True,
        growth_database_path=tmp_path / "growth.sqlite3",
    )

    assert {tool.name for tool in tools} >= {
        "content.get_growth_report",
        "content.list_viewer_missions",
    }
    report_call = ToolCall(
        tool_name="content.get_growth_report",
        arguments={},
    )
    result = execute(registry, report_call)
    assert result.output["ok"] is True
    assert result.output["next_plan"]["series"]
    assert verifier.verify(report_call, result).verified is True


def test_growth_recording_builds_branded_package_and_local_memory(
    tmp_path, monkeypatch
):
    registry = ToolRegistry()
    verifier = ResultVerifier()
    branded = {}

    register_content_tools(
        registry,
        verifier,
        staging_directory=tmp_path,
        script_writer=lambda **kwargs: (
            {"narration": "Opening line.", "action": "focus_core"},
            {"narration": "Evidence line.", "action": "focus_system"},
            {"narration": "Closing line.", "action": "idle"},
        ),
        growth_writer=lambda **kwargs: {
            "title": "A Better Cover",
            "hook_candidates": ["Hook A?", "Hook B.", "Hook C."],
            "cta": "What next?",
            "collaboration_pitch": "Draft only.",
            "translations": {},
        },
        enable_growth_package=True,
        growth_database_path=tmp_path / "growth.sqlite3",
    )

    def fake_render(text):
        path = tmp_path / f"{abs(hash(text))}.wav"
        _write_wav(path, seconds=1)
        return str(path)

    monkeypatch.setattr(content_pipeline, "render_narration", fake_render)
    monkeypatch.setattr(
        hud_capture,
        "record_hud_clip",
        lambda seconds, out: Path(out).write_bytes(b"raw"),
    )
    monkeypatch.setattr(
        content_pipeline,
        "edit_reel",
        lambda video, wav, out: Path(out).write_bytes(b"edited"),
    )
    monkeypatch.setattr(
        content_pipeline,
        "concat_clips",
        lambda clips, out: Path(out).write_bytes(b"concat"),
    )

    def brand(video, out, **kwargs):
        branded.update(kwargs)
        Path(out).write_bytes(b"branded")
        return str(out)

    monkeypatch.setattr(content_pipeline, "brand_reel", brand)
    monkeypatch.setattr(
        content_tools_module.reel_package,
        "create_distribution_package",
        lambda **kwargs: {
            "status": "prepared_not_published",
            "trial_variants": [
                {"name": "A", "hook": "Hook A?", "video_path": "/tmp/a.mp4"},
                {"name": "B", "hook": "Hook B.", "video_path": "/tmp/b.mp4"},
            ],
            "external_actions_taken": [],
        },
    )
    monkeypatch.setattr(
        content_tools_module, "_preview_reel", lambda _path: (True, None)
    )
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: None)

    result = execute(
        registry,
        ToolCall(
            tool_name="content.record_self_showcase",
            arguments={"mission": "show a Pi capability"},
        ),
    )

    assert result.output["ok"] is True
    assert result.output["growth_package"]["status"] == "prepared_not_published"
    assert result.output["beats"][0]["narration"].startswith("Can a Raspberry Pi")
    assert result.output["beats"][-1]["narration"].endswith("What should I attempt next?")
    assert len(branded["cues"]) == 3
    assert Path(result.output["video_path"]).read_bytes() == b"branded"
    report = execute(
        registry,
        ToolCall(tool_name="content.get_growth_report", arguments={}),
    )
    assert report.output["drafts"] == 1


def test_growth_direction_does_not_repeat_a_paraphrased_opening_hook():
    tour = ({
        "narration": (
            "Can a Raspberry Pi find and repair a real problem in its own "
            "system? I'm Atlas, and today I am checking the evidence."
        ),
        "action": "idle",
    },)
    plan = {
        "hook": (
            "Can a Raspberry Pi really find, explain, and repair one real "
            "problem in Atlas's own system?"
        ),
        "cta": "What should I investigate next?",
    }

    directed = content_tools_module._apply_growth_direction(tour, plan)

    assert directed[0]["narration"].count("Can a Raspberry Pi") == 1


def test_build_default_tour_uses_distinct_features_without_legacy_pair():
    tour = _build_default_tour()

    actions = [beat["action"] for beat in tour]
    assert tour[0]["narration"] in INTRO_LINES
    assert tour[-1]["narration"] in OUTRO_LINES
    features = [
        beat for beat in tour if beat["action"] not in {"idle", "weather_close"}
    ]
    assert len(features) == MAX_FEATURE_BEATS
    assert len({beat["action"] for beat in features}) == MAX_FEATURE_BEATS
    assert not {"weather_open", "diagnostics"}.issubset(actions)
    assert all(beat in HUD_FEATURE_BEATS for beat in features)


def test_build_default_tour_varies_across_calls():
    tours = [_build_default_tour() for _ in range(30)]

    narrations = {tuple(beat["narration"] for beat in tour) for tour in tours}
    assert len(narrations) > 1, (
        "30 calls produced the exact same script every time -- "
        "randomization isn't actually varying anything"
    )


def test_connected_fallback_does_not_force_a_canned_pc_clip():
    for _ in range(20):
        tour = _build_default_tour(pc_demo_available=True)
        assert all(beat.get("source", "hud") == "hud" for beat in tour)


def test_connected_default_tour_handles_mixed_hud_pc_history():
    recent = ({
        "beats": [
            {"action": "focus_system", "recorded_source": "hud"},
            {
                "source": "pc",
                "recorded_source": "pc",
                "pc_action": {
                    "type": "youtube_search",
                    "query": "robotics project builds",
                },
            },
        ]
    },)

    tour = _build_default_tour(
        pc_demo_available=True, recent_tours=recent
    )

    assert all(beat.get("source", "hud") == "hud" for beat in tour)
    assert len(tour) == MAX_FEATURE_BEATS + 2


def test_record_self_showcase_runs_default_tour_and_drives_hud(tmp_path, monkeypatch):
    registry, verifier, _tools = build_tools(tmp_path)

    narration_counter = {"n": 0}

    def fake_render_narration(text):
        narration_counter["n"] += 1
        wav_path = tmp_path / f"narration_{narration_counter['n']}.wav"
        _write_wav(wav_path)
        return str(wav_path)

    monkeypatch.setattr(
        content_pipeline, "render_narration", fake_render_narration
    )

    def fake_record_hud_clip(duration, out_path, **kwargs):
        Path(out_path).write_bytes(b"raw hud clip bytes")
        return str(out_path)

    monkeypatch.setattr(hud_capture, "record_hud_clip", fake_record_hud_clip)

    def fake_edit_reel(video_path, narration_wav_path, out_path):
        Path(out_path).write_bytes(b"edited beat clip")
        return str(out_path)

    monkeypatch.setattr(content_pipeline, "edit_reel", fake_edit_reel)

    def fake_concat_clips(clip_paths, out_path):
        Path(out_path).write_bytes(b"final concatenated reel")
        return str(out_path)

    monkeypatch.setattr(content_pipeline, "concat_clips", fake_concat_clips)

    import atlas_agent.content_tools as content_tools_module

    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)

    hud_posts = []
    real_post = content_tools_module.requests.post

    def fake_post(url, **kwargs):
        hud_posts.append((url, kwargs.get("json")))
        return mock.Mock(ok=True)

    monkeypatch.setattr(content_tools_module.requests, "post", fake_post)
    monkeypatch.setattr(diagnostics, "run_structured_checks", lambda: [])

    call = ToolCall(
        tool_name="content.record_self_showcase",
        arguments={"mission": "promo-reel", "beats": None},
    )

    result = execute(registry, call)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["ok"] is True
    assert Path(result.output["video_path"]).is_file()
    assert result.output["caption"]
    assert verification.verified is True

    # Recording indicator flipped on then off, and weather overlay was
    # driven open then closed as part of the default tour.
    urls = [url for url, _body in hud_posts]
    assert f"{content_tools_module.HUB}/hud/recording" in urls
    assert f"{content_tools_module.HUB}/hud/weather_overlay" in urls

    recording_bodies = [
        body for url, body in hud_posts if url.endswith("/hud/recording")
    ]
    assert recording_bodies[0] == {"active": True}
    assert recording_bodies[-1] == {"active": False}


def test_record_self_showcase_accepts_custom_beats(tmp_path, monkeypatch):
    registry, _verifier, _tools = build_tools(tmp_path)

    narration_wav = tmp_path / "narration.wav"
    _write_wav(narration_wav)
    monkeypatch.setattr(
        content_pipeline, "render_narration", lambda text: str(narration_wav)
    )

    def fake_record_hud_clip(duration, out_path, **kwargs):
        Path(out_path).write_bytes(b"raw clip")
        return str(out_path)

    monkeypatch.setattr(hud_capture, "record_hud_clip", fake_record_hud_clip)

    def fake_edit_reel(video_path, narration_wav_path, out_path):
        Path(out_path).write_bytes(b"beat")
        return str(out_path)

    monkeypatch.setattr(content_pipeline, "edit_reel", fake_edit_reel)

    def fake_concat_clips(clip_paths, out_path):
        Path(out_path).write_bytes(b"final")
        return str(out_path)

    monkeypatch.setattr(content_pipeline, "concat_clips", fake_concat_clips)

    import atlas_agent.content_tools as content_tools_module

    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        content_tools_module.requests, "post", lambda *a, **k: mock.Mock(ok=True)
    )

    call = ToolCall(
        tool_name="content.record_self_showcase",
        arguments={
            "mission": None,
            "beats": [{"narration": "Just one custom line.", "action": "idle"}],
        },
    )

    result = execute(registry, call)

    assert result.output["ok"] is True
    assert "Just one custom line." in result.output["caption"]


def test_record_self_showcase_rejects_invalid_beats(tmp_path):
    registry, _verifier, _tools = build_tools(tmp_path)

    call = ToolCall(
        tool_name="content.record_self_showcase",
        arguments={"mission": None, "beats": [{"action": "idle"}]},
    )

    result = execute(registry, call)

    assert result.status is ResultStatus.ERROR


def test_record_self_showcase_reports_hud_capture_failure(tmp_path, monkeypatch):
    registry, _verifier, _tools = build_tools(tmp_path)

    narration_wav = tmp_path / "narration.wav"
    _write_wav(narration_wav)
    monkeypatch.setattr(
        content_pipeline, "render_narration", lambda text: str(narration_wav)
    )

    def raise_capture_error(duration, out_path, **kwargs):
        raise hud_capture.HudCaptureError("no HUD frames were captured")

    monkeypatch.setattr(hud_capture, "record_hud_clip", raise_capture_error)

    import atlas_agent.content_tools as content_tools_module

    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        content_tools_module.requests, "post", lambda *a, **k: mock.Mock(ok=True)
    )

    call = ToolCall(
        tool_name="content.record_self_showcase",
        arguments={"mission": None, "beats": None},
    )

    result = execute(registry, call)

    assert result.output["ok"] is False
    assert "no HUD frames" in result.output["error"]
    assert not narration_wav.exists()


class FakePCActionResult:
    def __init__(self, ok, data=None, error=None):
        self.ok = ok
        self.data = data
        self.error = error


class FakeDownloadResult:
    def __init__(self, local_path, verified=True):
        self.local_path = local_path
        self.verified = verified


def test_record_pc_demo_clip_orchestrates_start_action_stop_download(
    tmp_path, monkeypatch
):
    from atlas_agent.content_tools import _record_pc_demo_clip
    import atlas_agent.content_tools as content_tools_module

    calls = []

    class FakePCClient:
        def execute(self, action, arguments=None):
            calls.append((action, arguments))
            if action == "start_recording":
                return FakePCActionResult(True, {"ok": True})
            if action == "stop_recording":
                return FakePCActionResult(
                    True,
                    {
                        "ok": True,
                        "path": r"C:\Users\wesle\Videos\AtlasRecordings\r.mp4",
                    },
                )
            if action == "open_app":
                return FakePCActionResult(True, {"ok": True})
            raise AssertionError(f"unexpected action {action}")

    class FakeSFTPClient:
        def download(self, remote_path):
            calls.append(("download", remote_path))
            local = tmp_path / "downloaded.mp4"
            local.write_bytes(b"pc clip bytes")
            return FakeDownloadResult(str(local))

    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)

    result_path = _record_pc_demo_clip(
        FakePCClient(),
        FakeSFTPClient(),
        {"type": "open_app", "app": "notepad"},
        2.0,
        "promo-reel",
    )

    assert Path(result_path).is_file()
    action_calls = [c for c in calls if c[0] != "download"]
    assert action_calls[0][0] == "start_recording"
    assert action_calls[1] == ("open_app", {"app": "notepad"})
    assert action_calls[2][0] == "stop_recording"
    assert calls[-1][0] == "download"
    assert calls[-1][1] == r"C:\Users\wesle\Videos\AtlasRecordings\r.mp4"


def test_record_pc_demo_clip_uses_youtube_search_action(tmp_path, monkeypatch):
    from atlas_agent.content_tools import _record_pc_demo_clip
    import atlas_agent.content_tools as content_tools_module
    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)
    actions = []

    class FakePCClient:
        def execute(self, action, arguments=None):
            actions.append((action, arguments))
            if action == "youtube_search":
                return FakePCActionResult(True, {"ok": True})
            if action == "start_recording":
                return FakePCActionResult(True, {"ok": True})
            if action == "stop_recording":
                return FakePCActionResult(
                    True, {"ok": True, "path": r"C:\path\r.mp4"}
                )
            raise AssertionError(f"unexpected action {action}")

    class FakeSFTPClient:
        def download(self, remote_path):
            local = tmp_path / "downloaded.mp4"
            local.write_bytes(b"pc clip bytes")
            return FakeDownloadResult(str(local))

    _record_pc_demo_clip(
        FakePCClient(),
        FakeSFTPClient(),
        {"type": "youtube_search", "query": "raspberry pi robots"},
        2.0,
        None,
    )

    youtube_actions = [item for item in actions if item[0] == "youtube_search"]
    assert len(youtube_actions) == 2  # safe preflight, then captured action
    assert all(item[1]["query"] == "raspberry pi robots" for item in youtube_actions)
    assert all(item[1]["private"] is True for item in youtube_actions)


def test_record_pc_demo_clip_prepares_notepad_before_typing(tmp_path, monkeypatch):
    from atlas_agent.content_tools import _record_pc_demo_clip
    import atlas_agent.content_tools as content_tools_module

    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)
    actions = []

    class FakePCClient:
        def execute(self, action, arguments=None):
            actions.append((action, arguments))
            if action == "stop_recording":
                return FakePCActionResult(
                    True, {"ok": True, "path": r"C:\path\r.mp4"}
                )
            return FakePCActionResult(True, {"ok": True})

    class FakeSFTPClient:
        def download(self, remote_path):
            local = tmp_path / "downloaded.mp4"
            local.write_bytes(b"pc clip bytes")
            return FakeDownloadResult(str(local))

    _record_pc_demo_clip(
        FakePCClient(),
        FakeSFTPClient(),
        {"type": "type_text", "app": "notepad", "text": "hello"},
        2.0,
        None,
    )

    assert actions[0] == ("focus_or_open_app", {"app": "notepad"})
    assert actions[1][0] == "start_recording"
    assert actions[2][0] == "type_text"


def test_record_pc_demo_clip_raises_on_start_failure(monkeypatch):
    from atlas_agent.content_tools import (
        PcDemoCaptureError,
        _record_pc_demo_clip,
    )
    import atlas_agent.content_tools as content_tools_module

    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)

    class FakePCClient:
        def execute(self, action, arguments=None):
            return FakePCActionResult(
                False, {}, error="a recording is already in progress"
            )

    import pytest

    with pytest.raises(PcDemoCaptureError, match="already in progress"):
        _record_pc_demo_clip(FakePCClient(), object(), None, 2.0, None)


def test_build_default_tour_never_includes_pc_beat_when_unavailable():
    tours = [_build_default_tour(pc_demo_available=False) for _ in range(30)]

    for tour in tours:
        assert all(beat.get("source", "hud") == "hud" for beat in tour)


def test_build_default_tour_avoids_repetitive_pc_beat_when_available():
    tours = [_build_default_tour(pc_demo_available=True) for _ in range(60)]

    has_pc_beat = [
        any(beat.get("source") == "pc" for beat in tour) for tour in tours
    ]
    assert not any(has_pc_beat)


def test_record_self_showcase_stitches_hud_and_pc_clips(tmp_path, monkeypatch):
    from atlas_agent.content_tools import register_content_tools
    import atlas_agent.content_tools as content_tools_module

    registry = ToolRegistry()
    verifier = ResultVerifier()

    class FakePCClient:
        def execute(self, action, arguments=None):
            if action == "start_recording":
                return FakePCActionResult(True, {"ok": True})
            if action == "stop_recording":
                return FakePCActionResult(
                    True, {"ok": True, "path": r"C:\path\r.mp4"}
                )
            return FakePCActionResult(True, {"ok": True})

    class FakeSFTPClient:
        def download(self, remote_path):
            local = tmp_path / "pc_downloaded.mp4"
            local.write_bytes(b"pc clip bytes")
            return FakeDownloadResult(str(local))

    register_content_tools(
        registry,
        verifier,
        staging_directory=tmp_path,
        pc_client=FakePCClient(),
        sftp_client=FakeSFTPClient(),
    )

    narration_counter = {"n": 0}

    def fake_render_narration(text):
        narration_counter["n"] += 1
        wav_path = tmp_path / f"narration_{narration_counter['n']}.wav"
        _write_wav(wav_path)
        return str(wav_path)

    monkeypatch.setattr(
        content_pipeline, "render_narration", fake_render_narration
    )

    def fake_record_hud_clip(duration, out_path, **kwargs):
        Path(out_path).write_bytes(b"raw hud clip")
        return str(out_path)

    monkeypatch.setattr(hud_capture, "record_hud_clip", fake_record_hud_clip)

    def fake_edit_reel(video_path, narration_wav_path, out_path):
        Path(out_path).write_bytes(b"edited beat")
        return str(out_path)

    monkeypatch.setattr(content_pipeline, "edit_reel", fake_edit_reel)

    concat_inputs = []

    def fake_concat_clips(clip_paths, out_path):
        concat_inputs.extend(clip_paths)
        Path(out_path).write_bytes(b"final reel")
        return str(out_path)

    monkeypatch.setattr(content_pipeline, "concat_clips", fake_concat_clips)
    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        content_tools_module.requests, "post", lambda *a, **k: mock.Mock(ok=True)
    )

    call = ToolCall(
        tool_name="content.record_self_showcase",
        arguments={
            "mission": "promo-reel",
            "beats": [
                {"narration": "Here's my HUD.", "action": "idle"},
                {
                    "narration": "Now watch me drive the PC.",
                    "source": "pc",
                    "pc_action": {"type": "open_app", "app": "notepad"},
                },
                {"narration": "And back to my own screen.", "action": "idle"},
            ],
        },
    )

    result = execute(registry, call)

    assert result.output["ok"] is True
    assert len(concat_inputs) == 3


def test_record_self_showcase_rejects_pc_beat_without_pc_connection(tmp_path):
    registry, _verifier, _tools = build_tools(tmp_path)

    call = ToolCall(
        tool_name="content.record_self_showcase",
        arguments={
            "mission": None,
            "beats": [
                {
                    "narration": "Try to show the PC.",
                    "source": "pc",
                }
            ],
        },
    )

    result = execute(registry, call)

    assert result.output["ok"] is False
    assert "PC connection" in result.output["error"]


def test_publish_to_instagram_requires_confirmation(tmp_path):
    registry, _verifier, _tools = build_tools(tmp_path)
    call = ToolCall(
        tool_name="content.publish_to_instagram",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "caption": "caption text",
            "mission": None,
        },
    )

    result = execute(registry, call, confirmed=False)

    assert result.status is ResultStatus.CONFIRMATION_REQUIRED


def test_publish_to_instagram_publishes_when_confirmed(tmp_path, monkeypatch):
    registry, verifier, _tools = build_tools(tmp_path)

    monkeypatch.setattr(
        instagram_publish,
        "publish_reel",
        lambda video_path, caption, dry_run, mission: {
            "dry_run": False,
            "media_id": "media-1",
            "permalink": "https://instagram.com/reel/media-1",
            "caption": caption,
            "mission": mission,
        },
    )

    call = ToolCall(
        tool_name="content.publish_to_instagram",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "caption": "caption text",
            "mission": "promo-reel",
        },
    )

    result = execute(registry, call, confirmed=True)
    verification = verifier.verify(call, result)

    assert result.status is ResultStatus.SUCCESS
    assert result.output["ok"] is True
    assert result.output["permalink"] == "https://instagram.com/reel/media-1"
    assert len(
        content_pipeline.HASHTAG_PATTERN.findall(result.output["caption"])
    ) == 30
    assert verification.verified is True


def test_publish_to_instagram_surfaces_publish_error(tmp_path, monkeypatch):
    registry, verifier, _tools = build_tools(tmp_path)

    def raise_error(video_path, caption, dry_run, mission):
        raise instagram_publish.InstagramPublishError(
            "container creation failed: missing instagram_content_publish permission"
        )

    monkeypatch.setattr(instagram_publish, "publish_reel", raise_error)

    call = ToolCall(
        tool_name="content.publish_to_instagram",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "caption": "caption text",
            "mission": None,
        },
    )

    result = execute(registry, call, confirmed=True)
    verification = verifier.verify(call, result)

    assert result.output["ok"] is False
    assert "instagram_content_publish" in result.output["error"]
    assert verification.verified is False


def test_publish_to_youtube_requires_confirmation(tmp_path):
    registry, _verifier, _tools = build_tools(
        tmp_path, enable_youtube_publish=True
    )
    call = ToolCall(
        tool_name="content.publish_to_youtube",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "title": "Atlas Short",
            "description": "caption text",
            "privacy_status": "private",
            "mission": None,
        },
    )

    result = execute(registry, call, confirmed=False)

    assert result.status is ResultStatus.CONFIRMATION_REQUIRED


def test_publish_to_facebook_requires_confirmation(tmp_path):
    registry, _verifier, _tools = build_tools(
        tmp_path, enable_facebook_publish=True
    )
    call = ToolCall(
        tool_name="content.publish_to_facebook",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "title": "Atlas Reel",
            "description": "caption text",
            "mission": None,
        },
    )

    result = execute(registry, call, confirmed=False)

    assert result.status is ResultStatus.CONFIRMATION_REQUIRED


def test_publish_to_facebook_publishes_when_confirmed(tmp_path, monkeypatch):
    registry, verifier, _tools = build_tools(
        tmp_path, enable_facebook_publish=True
    )
    captured = {}

    def publish(video_path, title, description, **kwargs):
        captured.update({
            "video_path": video_path,
            "title": title,
            "description": description,
            **kwargs,
        })
        return {
            "video_id": "facebook-video-1",
            "permalink": "https://facebook.example/reel/facebook-video-1",
            "publishing_status": "complete",
        }

    monkeypatch.setattr(facebook_publish, "publish_reel", publish)
    call = ToolCall(
        tool_name="content.publish_to_facebook",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "title": "Atlas Reel",
            "description": "caption #random",
            "mission": "show Atlas",
        },
    )

    result = execute(registry, call, confirmed=True)
    verification = verifier.verify(call, result)

    assert result.output["ok"] is True
    assert len(
        content_pipeline.HASHTAG_PATTERN.findall(captured["description"])
    ) == 30
    assert verification.verified is True


def test_combined_social_publish_replaces_individual_publish_tools(tmp_path):
    registry, _verifier, tools = build_tools(
        tmp_path,
        enable_facebook_publish=True,
        enable_combined_social_publish=True,
    )

    names = {tool.name for tool in tools}
    assert "content.publish_to_socials" in names
    assert "content.publish_to_instagram" not in names
    assert "content.publish_to_facebook" not in names
    assert registry.get("content.publish_to_socials").permission_level == 2


def test_combined_social_publish_uses_one_confirmation(tmp_path, monkeypatch):
    registry, verifier, _tools = build_tools(
        tmp_path,
        enable_facebook_publish=True,
        enable_combined_social_publish=True,
    )
    monkeypatch.setattr(
        social_publish,
        "publish_reel",
        lambda video_path, title, caption, mission: {
            "ok": True,
            "video_path": video_path,
            "title": title,
            "caption": caption,
            "mission": mission,
            "instagram": {
                "verified": True,
                "media_id": "ig-1",
                "permalink": "https://instagram.example/reel/ig-1",
            },
            "facebook": {
                "verified": True,
                "video_id": "fb-1",
                "permalink": "https://facebook.example/reel/fb-1",
            },
        },
    )
    call = ToolCall(
        tool_name="content.publish_to_socials",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "title": "Atlas Reel",
            "caption": "caption text",
            "mission": None,
        },
    )

    waiting = execute(registry, call, confirmed=False)
    assert waiting.status is ResultStatus.CONFIRMATION_REQUIRED

    result = execute(registry, call, confirmed=True)
    assert result.output["ok"] is True
    assert len(
        content_pipeline.HASHTAG_PATTERN.findall(result.output["caption"])
    ) == 30
    assert verifier.verify(call, result).verified is True


def test_desktop_export_copies_curated_verified_package(tmp_path):
    video = tmp_path / "reel_123.mp4"
    video.write_bytes(b"video")
    package = tmp_path / "reel_123_package"
    (package / "subtitles").mkdir(parents=True)
    (package / "platforms" / "instagram").mkdir(parents=True)
    (package / "caption.txt").write_text("caption", encoding="utf-8")
    (package / "cover.png").write_bytes(b"cover")
    (package / "subtitles" / "en.srt").write_text("srt", encoding="utf-8")
    (package / "platforms" / "instagram" / "reel.mp4").write_bytes(
        b"duplicate"
    )

    class Connection:
        success = True

    class PC:
        def ensure_online(self, **kwargs):
            return Connection()

    class Transfer:
        ok = True
        verified = True
        error = None

    class SFTP:
        def __init__(self):
            self.directories = []
            self.uploads = []

        def make_directory(self, path):
            self.directories.append(str(path))

        def upload(self, local, remote):
            self.uploads.append((str(local), str(remote)))
            return Transfer()

    sftp = SFTP()
    result = _export_reel_to_desktop(
        video_path=video,
        package_path=package,
        pc_client=PC(),
        sftp_client=sftp,
        remote_root=r"C:\Users\wesle\Desktop\Atlas Reels",
    )

    assert result["ok"] is True
    assert result["file_count"] == 4
    assert any(remote.endswith(r"\reel.mp4") for _, remote in sftp.uploads)
    assert not any("platforms" in remote for _, remote in sftp.uploads)


def test_ms_paint_request_inserts_mandatory_recorded_desktop_scene():
    tour = (
        {"narration": "Hook.", "action": "focus_core"},
        {"narration": "Evidence.", "action": "focus_system"},
        {"narration": "What next?", "action": "idle"},
    )

    directed = _ensure_required_pc_scene(
        tour,
        "Record a Reel and include MS Paint so viewers can watch you paint.",
    )

    paint_beats = [
        beat
        for beat in directed
        if beat.get("source") == "pc"
        and isinstance(beat.get("pc_action"), dict)
        and beat["pc_action"].get("type") == "desktop_goal"
        and "Microsoft Paint" in beat["pc_action"].get("goal", "")
    ]
    assert len(paint_beats) == 1
    assert "do not open a terminal" in paint_beats[0]["pc_action"]["goal"]


def test_save_and_delete_showcase_tools_are_verified(tmp_path):
    video = tmp_path / "reel_456.mp4"
    video.write_bytes(b"video")
    sidecar = tmp_path / "reel_456.mp4.json"
    sidecar.write_text("{}", encoding="utf-8")
    package = tmp_path / "reel_456_package"
    package.mkdir()
    (package / "caption.txt").write_text("caption", encoding="utf-8")

    class Connection:
        success = True

    class PC:
        def ensure_online(self, **kwargs):
            return Connection()

    class Transfer:
        ok = True
        verified = True
        error = None

    class SFTP:
        def make_directory(self, path):
            pass

        def upload(self, local, remote):
            return Transfer()

    registry = ToolRegistry()
    verifier = ResultVerifier()
    register_content_tools(
        registry,
        verifier,
        staging_directory=tmp_path,
        pc_client=PC(),
        sftp_client=SFTP(),
        desktop_reels_remote_root=r"C:\Users\wesle\Desktop\Atlas Reels",
    )

    save_call = ToolCall(
        tool_name="content.save_showcase",
        arguments={
            "video_path": str(video),
            "package_path": str(package),
        },
    )
    saved = execute(registry, save_call)
    assert saved.output["ok"] is True
    assert verifier.verify(save_call, saved).verified is True

    delete_call = ToolCall(
        tool_name="content.delete_showcase",
        arguments={
            "video_path": str(video),
            "package_path": str(package),
        },
    )
    waiting = execute(registry, delete_call)
    assert waiting.status is ResultStatus.CONFIRMATION_REQUIRED
    deleted = execute(registry, delete_call, confirmed=True)
    assert verifier.verify(delete_call, deleted).verified is True
    assert not video.exists()
    assert not sidecar.exists()
    assert not package.exists()


def test_publish_to_youtube_uploads_when_confirmed(tmp_path, monkeypatch):
    registry, verifier, _tools = build_tools(
        tmp_path, enable_youtube_publish=True
    )
    captured = {}

    def publish(video_path, title, description, **kwargs):
        captured.update({
            "video_path": video_path,
            "title": title,
            "description": description,
            **kwargs,
        })
        return {
            "video_id": "video-1",
            "permalink": "https://www.youtube.com/shorts/video-1",
            "privacy_status": "private",
            "processing_status": "processing",
        }

    monkeypatch.setattr(youtube_publish, "publish_short", publish)
    call = ToolCall(
        tool_name="content.publish_to_youtube",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "title": "Atlas Short",
            "description": "caption text #random",
            "privacy_status": "private",
            "mission": "show Atlas",
        },
    )

    result = execute(registry, call, confirmed=True)
    verification = verifier.verify(call, result)

    assert result.output["ok"] is True
    assert len(
        content_pipeline.HASHTAG_PATTERN.findall(captured["description"])
    ) == 30
    assert len(captured["tags"]) == 30
    assert captured["privacy_status"] == "private"
    assert verification.verified is True


def test_resolve_tour_prefers_the_unscripted_writer(tmp_path):
    written = (
        {"narration": "System view.", "action": "focus_system"},
        {"narration": "Core view.", "action": "focus_core"},
    )

    tour = _resolve_tour(
        lambda **kwargs: written, pc_demo_available=False
    )

    assert tour is written


def test_resolve_tour_tells_the_writer_whether_a_pc_is_connected():
    seen = {}

    def writer(**kwargs):
        seen.update(kwargs)
        return ({"narration": "hi", "action": "idle"},)

    recent = ({
        "beats": [
            {"narration": "Old radar.", "action": "weather_open"}
        ]
    },)

    _resolve_tour(
        writer,
        pc_demo_available=True,
        recent_tours=recent,
    )

    assert seen["pc_demo_available"] is True
    assert isinstance(seen["context"], dict)
    assert seen["context"]["recent_showcase_tours"] == [{
        "beats": [{
            "narration": "Old radar.",
            "action": "weather_open",
            "source": "hud",
            "pc_action": None,
        }]
    }]


def test_resolve_tour_falls_back_to_the_canned_tour_on_failure(capsys):
    """A dead API key or a budget stop should cost variety, not the
    video -- the recording still has to produce something postable."""
    def writer(**kwargs):
        raise RuntimeError("no api key")

    tour = _resolve_tour(writer, pc_demo_available=False)

    actions = [beat["action"] for beat in tour]
    assert not {"weather_open", "diagnostics"}.issubset(actions)
    assert len(set(actions) - {"idle", "weather_close"}) >= 2
    assert "no api key" in capsys.readouterr().out


def test_resolve_tour_uses_the_canned_tour_when_no_writer_is_configured():
    tour = _resolve_tour(None, pc_demo_available=False)

    assert tour[0]["narration"] in INTRO_LINES


def test_fallback_rotates_away_from_the_latest_visual_features():
    recent = ({
        "beats": [
            {"narration": "System.", "action": "focus_system"},
            {"narration": "Core.", "action": "focus_core"},
        ]
    },)

    tour = _build_default_tour(recent_tours=recent)
    actions = {beat["action"] for beat in tour}

    assert "focus_system" not in actions
    assert "focus_core" not in actions


def test_planner_schema_cannot_inject_a_canned_beat_list(tmp_path):
    registry, _verifier, _tools = build_tools(tmp_path)
    parameters = registry.get(
        "content.record_self_showcase"
    ).metadata["parameters"]

    assert set(parameters["properties"]) == {"mission"}
    assert parameters["required"] == ["mission"]


def test_record_self_showcase_uses_the_unscripted_tour(tmp_path, monkeypatch):
    """End to end: with a script writer configured, the recorded video's
    narration comes from the writer, not the canned line pools."""
    registry = ToolRegistry()
    verifier = ResultVerifier()
    register_content_tools(
        registry,
        verifier,
        staging_directory=tmp_path,
        script_writer=lambda **kwargs: (
            {"narration": "Unscripted opener.", "action": "idle"},
            {"narration": "Unscripted closer.", "action": "idle"},
        ),
    )

    def fake_render(text):
        wav_path = tmp_path / f"narration_{len(text)}_{text[:4]}.wav"
        _write_wav(wav_path)
        return str(wav_path)

    monkeypatch.setattr(content_pipeline, "render_narration", fake_render)
    monkeypatch.setattr(
        hud_capture,
        "record_hud_clip",
        lambda seconds, out_path: Path(out_path).write_bytes(b"clip"),
    )
    monkeypatch.setattr(
        content_pipeline,
        "edit_reel",
        lambda video, wav, out: Path(out).write_bytes(b"edited"),
    )
    monkeypatch.setattr(
        content_pipeline,
        "concat_clips",
        lambda clips, out: Path(out).write_bytes(b"reel"),
    )
    monkeypatch.setattr(
        content_pipeline, "build_caption", lambda text: text
    )
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: None)

    result = execute(
        registry,
        ToolCall(
            tool_name="content.record_self_showcase",
            arguments={"mission": None, "beats": None},
        ),
    )

    assert result.output["ok"] is True
    assert result.output["caption"].startswith(
        "Unscripted opener. Unscripted closer."
    )
    assert len(
        content_pipeline.HASHTAG_PATTERN.findall(result.output["caption"])
    ) == 30
    history = json.loads(
        (tmp_path / SHOWCASE_HISTORY_FILENAME).read_text()
    )
    assert history["tours"][-1]["video_path"] == result.output["video_path"]
    assert history["tours"][-1]["caption"] == result.output["caption"]
    assert [
        beat["narration"] for beat in history["tours"][-1]["beats"]
    ] == ["Unscripted opener.", "Unscripted closer."]


def test_perform_pc_action_types_text_paced_to_the_beat(monkeypatch):
    """edit_reel trims each clip back to its narration length, so the
    typing has to finish just inside that cut -- not after it, which
    would show a half-typed message."""
    calls = []
    pc_client = mock.Mock()
    pc_client.execute.side_effect = lambda action, body=None: calls.append(
        (action, body)
    )

    _perform_pc_action(
        pc_client,
        {"type": "type_text", "app": "notepad", "text": "hi viewers"},
        clip_seconds=12.0,
    )

    action, body = calls[0]
    assert action == "type_text"
    assert body["app"] == "notepad"
    assert body["text"] == "hi viewers"
    assert body["duration_seconds"] == 12.0 - TYPING_LEAD_SECONDS


def test_perform_pc_action_keeps_a_positive_duration_on_short_beats(
    monkeypatch,
):
    calls = []
    pc_client = mock.Mock()
    pc_client.execute.side_effect = lambda action, body=None: calls.append(
        (action, body)
    )

    _perform_pc_action(
        pc_client,
        {"type": "type_text", "text": "hi"},
        clip_seconds=0.5,
    )

    assert calls[0][1]["duration_seconds"] == 1.0
    assert calls[0][1]["app"] == "notepad"


def test_perform_pc_action_survives_a_failed_type_text(monkeypatch):
    """Best-effort, same as every other beat action: a typing failure
    means that beat shows an untyped Notepad, not an aborted recording."""
    pc_client = mock.Mock()
    pc_client.execute.side_effect = RuntimeError("companion offline")

    _perform_pc_action(
        pc_client, {"type": "type_text", "text": "hi"}, clip_seconds=5.0
    )


def _stub_pipeline(tmp_path, monkeypatch):
    """Stubs out narration/capture/edit so a recording runs instantly."""
    def fake_render(text):
        wav_path = tmp_path / f"n_{abs(hash(text)) % 10000}.wav"
        _write_wav(wav_path)
        return str(wav_path)

    monkeypatch.setattr(content_pipeline, "render_narration", fake_render)
    monkeypatch.setattr(
        content_pipeline,
        "edit_reel",
        lambda video, wav, out: Path(out).write_bytes(b"edited"),
    )
    monkeypatch.setattr(
        content_pipeline,
        "concat_clips",
        lambda clips, out: Path(out).write_bytes(b"reel"),
    )
    monkeypatch.setattr(
        content_pipeline, "build_caption", lambda text: text
    )
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: None)


def test_generated_pc_beat_fails_closed_instead_of_substituting_hud(
    tmp_path, monkeypatch, capsys
):
    """A promised PC segment must never become an undisclosed HUD clip."""
    registry = ToolRegistry()
    verifier = ResultVerifier()
    pc_client = mock.Mock()
    sftp_client = mock.Mock()

    register_content_tools(
        registry,
        verifier,
        staging_directory=tmp_path,
        pc_client=pc_client,
        sftp_client=sftp_client,
        script_writer=lambda **kwargs: (
            {"narration": "On my screen.", "action": "idle"},
            {
                "narration": "Now the PC.",
                "action": "idle",
                "source": "pc",
                "pc_action": {"type": "type_text", "text": "hi"},
            },
        ),
    )
    _stub_pipeline(tmp_path, monkeypatch)

    hud_clips = []
    monkeypatch.setattr(
        hud_capture,
        "record_hud_clip",
        lambda seconds, out_path: hud_clips.append(out_path)
        or Path(out_path).write_bytes(b"clip"),
    )
    monkeypatch.setattr(
        "atlas_agent.content_tools._record_pc_demo_clip",
        mock.Mock(side_effect=PcDemoCaptureError("PC is asleep")),
    )

    result = execute(
        registry,
        ToolCall(
            tool_name="content.record_self_showcase",
            arguments={"mission": None, "beats": None},
        ),
    )

    assert result.output["ok"] is False
    assert "PC is asleep" in result.output["error"]
    assert "No incomplete HUD-only Reel" in result.output["error"]
    assert len(hud_clips) == 1
    assert not list(tmp_path.glob("reel_*.mp4"))


def test_generated_desktop_goal_is_not_replayed_after_partial_failure(
    tmp_path, monkeypatch
):
    registry = ToolRegistry()
    verifier = ResultVerifier()
    register_content_tools(
        registry,
        verifier,
        staging_directory=tmp_path,
        pc_client=mock.Mock(),
        sftp_client=mock.Mock(),
        script_writer=lambda **kwargs: (
            {
                "narration": "Watch me make one mark in Paint.",
                "source": "pc",
                "action": None,
                "pc_action": {
                    "type": "desktop_goal",
                    "goal": "Open Paint and draw one mark.",
                    "max_steps": 5,
                },
            },
        ),
    )
    _stub_pipeline(tmp_path, monkeypatch)
    capture = mock.Mock(
        side_effect=PcDemoCaptureError(
            "desktop goal did not verify completion"
        )
    )
    monkeypatch.setattr(
        "atlas_agent.content_tools._record_pc_demo_clip", capture
    )

    result = execute(
        registry,
        ToolCall(
            tool_name="content.record_self_showcase",
            arguments={"mission": None, "beats": None},
        ),
    )

    assert result.output["ok"] is False
    assert capture.call_count == 1


def test_pc_download_retries_hash_race_before_succeeding(tmp_path, monkeypatch):
    from atlas_agent.content_tools import _record_pc_demo_clip
    import atlas_agent.content_tools as content_tools_module

    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)

    class FakePCClient:
        def execute(self, action, arguments=None):
            if action == "start_recording":
                return FakePCActionResult(True, {"ok": True})
            if action == "stop_recording":
                return FakePCActionResult(
                    True, {"ok": True, "path": r"C:\path\r.mp4"}
                )
            if action == "open_app":
                return FakePCActionResult(True, {"ok": True})
            raise AssertionError(action)

    class FlakySFTP:
        attempts = 0

        def download(self, remote_path):
            self.attempts += 1
            local = tmp_path / "pc.mp4"
            local.write_bytes(b"pc")
            return FakeDownloadResult(
                str(local), verified=self.attempts >= 3
            )

    sftp = FlakySFTP()
    result = _record_pc_demo_clip(
        FakePCClient(),
        sftp,
        {"type": "open_app", "app": "browser"},
        2.0,
        None,
    )

    assert Path(result).is_file()
    assert sftp.attempts == 3


def test_live_showcase_context_omits_location_network_and_diagnostic_details(
    monkeypatch,
):
    import hud_stats

    monkeypatch.setattr(hud_stats, "get_hud_stats", lambda: {
        "station_name": "PRIVATE-STATION",
        "network": {"ip": "192.168.1.20"},
        "weather": {"city": "Oceanside", "temp_f": 70, "condition": "clear"},
        "cpu": {"percent": 12, "temp_c": 50},
        "memory": {"percent": 30},
        "disk": {"percent": 40, "used_gb": 99},
        "gaming_pc": {"online": True, "hostname": "private-pc"},
        "uptime_seconds": 100,
    })
    monkeypatch.setattr(diagnostics, "run_structured_checks", lambda: [{
        "component": "services",
        "ok": True,
        "detail": "/home/atlas/private",
    }])

    context = _live_context()
    serialized = json.dumps(context)

    assert "Oceanside" not in serialized
    assert "PRIVATE-STATION" not in serialized
    assert "192.168" not in serialized
    assert "/home/atlas" not in serialized
    assert context["hud"]["weather"] == {"temp_f": 70, "condition": "clear"}
    assert context["diagnostics"] == [{"component": "services", "ok": True}]


def test_private_context_redaction_uses_whole_terms(monkeypatch):
    from atlas_agent.content_tools import _redact_private_text
    import robot_config

    monkeypatch.setattr(
        robot_config,
        "get",
        lambda name, default="": {
            "HOME_CITY": "Oceanside, CA",
            "STATION_NAME": "ATLAS-LAB",
        }.get(name, default),
    )

    redacted = _redact_private_text(
        "Oceanside capabilities at ATLAS-LAB are online."
    )

    assert "Oceanside" not in redacted
    assert "ATLAS-LAB" not in redacted
    assert "capabilities" in redacted


def test_explicit_pc_beat_still_fails_loudly_when_the_pc_fails(
    tmp_path, monkeypatch
):
    """The opposite case: a caller who named a specific PC clip must be
    told it didn't happen, not handed a HUD clip pretending it did."""
    registry = ToolRegistry()
    verifier = ResultVerifier()

    register_content_tools(
        registry,
        verifier,
        staging_directory=tmp_path,
        pc_client=mock.Mock(),
        sftp_client=mock.Mock(),
    )
    _stub_pipeline(tmp_path, monkeypatch)
    monkeypatch.setattr(
        hud_capture,
        "record_hud_clip",
        lambda seconds, out_path: Path(out_path).write_bytes(b"clip"),
    )
    monkeypatch.setattr(
        "atlas_agent.content_tools._record_pc_demo_clip",
        mock.Mock(side_effect=PcDemoCaptureError("PC is asleep")),
    )

    result = execute(
        registry,
        ToolCall(
            tool_name="content.record_self_showcase",
            arguments={
                "mission": None,
                "beats": [
                    {
                        "narration": "Watch the PC.",
                        "action": "idle",
                        "source": "pc",
                        "pc_action": None,
                    }
                ],
            },
        ),
    )

    assert result.output["ok"] is False
    assert "PC is asleep" in result.output["error"]


# --- Regression: the Paint demo was cut off before it ever drew --------
#
# A desktop_goal beat runs a vision loop (observe -> decide -> act) that
# takes far longer than the beat's narration. ffmpeg's -t is a hard
# self-terminating cap, so a 20s cap ended the recording during the
# launch phase and the finished Reel showed Paint opening and nothing else.


def test_desktop_goal_recording_cap_covers_the_whole_vision_loop(
    tmp_path, monkeypatch
):
    from atlas_agent.content_tools import _record_pc_demo_clip
    import atlas_agent.content_tools as content_tools_module

    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)
    starts = []

    class FakePCClient:
        def execute(self, action, arguments=None, timeout_seconds=None):
            if action == "start_recording":
                starts.append(arguments)
                return FakePCActionResult(True, {"ok": True})
            if action == "stop_recording":
                return FakePCActionResult(
                    True, {"ok": True, "path": r"C:\path\r.mp4"}
                )
            raise AssertionError(f"unexpected action {action}")

    class FakeSFTPClient:
        def download(self, remote_path):
            local = tmp_path / "downloaded.mp4"
            local.write_bytes(b"pc clip bytes")
            return FakeDownloadResult(str(local))

    _record_pc_demo_clip(
        FakePCClient(),
        FakeSFTPClient(),
        {"type": "desktop_goal", "goal": "Draw in Paint", "max_steps": 10},
        12.0,
        None,
        pc_demo_director=lambda goal, max_steps: {"ok": True},
    )

    assert len(starts) == 1
    # Ten vision steps plus a verification turn cannot fit in 20 seconds.
    assert starts[0]["max_seconds"] >= 10 * 12


def test_desktop_goal_beat_stops_recording_as_soon_as_the_goal_finishes(
    tmp_path, monkeypatch
):
    """The generous ffmpeg cap must not become dead air: once the goal is
    done, the beat should stop rather than idle out to the full cap."""
    from atlas_agent.content_tools import _record_pc_demo_clip
    import atlas_agent.content_tools as content_tools_module

    slept = []
    monkeypatch.setattr(
        content_tools_module.time, "sleep", lambda s: slept.append(s)
    )

    class FakePCClient:
        def execute(self, action, arguments=None, timeout_seconds=None):
            if action == "start_recording":
                return FakePCActionResult(True, {"ok": True})
            if action == "stop_recording":
                return FakePCActionResult(
                    True, {"ok": True, "path": r"C:\path\r.mp4"}
                )
            raise AssertionError(f"unexpected action {action}")

    class FakeSFTPClient:
        def download(self, remote_path):
            local = tmp_path / "downloaded.mp4"
            local.write_bytes(b"pc clip bytes")
            return FakeDownloadResult(str(local))

    _record_pc_demo_clip(
        FakePCClient(),
        FakeSFTPClient(),
        {"type": "desktop_goal", "goal": "Draw in Paint", "max_steps": 10},
        12.0,
        None,
        pc_demo_director=lambda goal, max_steps: {"ok": True},
    )

    # It must never pad out to the 120s+ ffmpeg cap after the goal returns.
    assert max(slept) < 30


def test_paint_scene_budgets_enough_steps_to_actually_draw():
    """Opening Paint, picking a tool and drawing strokes cannot happen in
    the five steps the scene originally allowed."""
    from atlas_agent.content_tools import _required_pc_scene

    scene = _required_pc_scene("Record a Reel and include MS Paint.")

    assert scene["pc_action"]["max_steps"] >= 10

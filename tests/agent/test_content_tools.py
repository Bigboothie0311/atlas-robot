import wave
from pathlib import Path
from unittest import mock

from atlas_agent.content_tools import (
    DIAGNOSTICS_LINES,
    EXTRA_BEATS,
    INTRO_LINES,
    MAX_EXTRA_BEATS,
    OUTRO_LINES,
    WEATHER_LINES,
    _build_default_tour,
    register_content_tools,
)
from atlas_agent.executor import ToolExecutor
from atlas_agent.results import ResultStatus
from atlas_agent.tasks import ToolCall
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.verifier import ResultVerifier

import content_pipeline
import diagnostics
import hud_capture
import instagram_publish


def _write_wav(path: Path, seconds: float = 0.2) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * int(16000 * seconds))


def build_tools(tmp_path):
    registry = ToolRegistry()
    verifier = ResultVerifier()

    tools = register_content_tools(
        registry, verifier, staging_directory=tmp_path
    )

    return registry, verifier, tools


def execute(registry: ToolRegistry, call: ToolCall, confirmed: bool = False):
    with ToolExecutor(registry) as executor:
        return executor.execute(call, confirmed=confirmed)


def test_content_tools_are_registered(tmp_path):
    registry, _verifier, tools = build_tools(tmp_path)

    assert len(tools) == 2
    names = {tool.name for tool in registry.list_tools()}
    assert names == {
        "content.record_self_showcase",
        "content.publish_to_instagram",
    }
    assert registry.get("content.record_self_showcase").permission_level == 0
    assert registry.get("content.publish_to_instagram").permission_level == 2


def test_build_default_tour_always_includes_weather_and_diagnostics():
    tour = _build_default_tour()

    actions = [beat["action"] for beat in tour]
    assert "weather_open" in actions
    assert "diagnostics" in actions
    assert tour[0]["narration"] in INTRO_LINES
    assert tour[-1]["narration"] in OUTRO_LINES

    weather_beat = next(b for b in tour if b["action"] == "weather_open")
    diagnostics_beat = next(b for b in tour if b["action"] == "diagnostics")
    assert weather_beat["narration"] in WEATHER_LINES
    assert diagnostics_beat["narration"] in DIAGNOSTICS_LINES

    core_count = 4  # intro, weather, diagnostics, outro
    extra_beats_used = [
        beat for beat in tour if beat["narration"] in {
            b["narration"] for b in EXTRA_BEATS
        }
    ]
    assert len(tour) - core_count == len(extra_beats_used)
    assert len(extra_beats_used) <= MAX_EXTRA_BEATS
    assert len(extra_beats_used) == len({b["narration"] for b in extra_beats_used})


def test_build_default_tour_varies_across_calls():
    tours = [_build_default_tour() for _ in range(30)]

    narrations = {tuple(beat["narration"] for beat in tour) for tour in tours}
    assert len(narrations) > 1, (
        "30 calls produced the exact same script every time -- "
        "randomization isn't actually varying anything"
    )


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
    assert "weather radar" in result.output["caption"]
    assert "diagnostics" in result.output["caption"]
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
    import pc_control

    searched = {}

    def fake_youtube_search(query):
        searched["query"] = query
        return "opened"

    monkeypatch.setattr(pc_control, "youtube_search", fake_youtube_search)
    monkeypatch.setattr(content_tools_module.time, "sleep", lambda _s: None)

    class FakePCClient:
        def execute(self, action, arguments=None):
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

    assert searched["query"] == "raspberry pi robots"


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


def test_build_default_tour_sometimes_includes_pc_beat_when_available():
    tours = [_build_default_tour(pc_demo_available=True) for _ in range(60)]

    has_pc_beat = [
        any(beat.get("source") == "pc" for beat in tour) for tour in tours
    ]
    assert any(has_pc_beat), "never once included a PC demo beat in 60 tries"
    assert not all(has_pc_beat), "always included a PC demo beat -- not random"


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

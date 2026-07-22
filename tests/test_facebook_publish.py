import json
import stat
from pathlib import Path
from unittest import mock

import pytest

import facebook_publish


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


def configured(tmp_path, monkeypatch):
    config = tmp_path / "facebook.env"
    ledger = tmp_path / "facebook_posts.json"
    config.write_text(
        "FACEBOOK_PAGE_ID=123456\n"
        "FACEBOOK_PAGE_ACCESS_TOKEN=page-token\n"
    )
    monkeypatch.setattr(facebook_publish, "CONFIG_PATH", config)
    monkeypatch.setattr(facebook_publish, "LEDGER_PATH", ledger)
    return config, ledger


def test_get_page_checks_exact_configured_page(tmp_path, monkeypatch):
    configured(tmp_path, monkeypatch)
    get = mock.Mock(return_value=FakeResponse({
        "id": "123456",
        "name": "ATLAS AI Robot",
        "category": "Digital creator",
        "link": "https://facebook.example/atlas",
    }))
    monkeypatch.setattr(facebook_publish.requests, "get", get)

    page = facebook_publish.get_page()

    assert page["name"] == "ATLAS AI Robot"
    assert get.call_args.kwargs["headers"] == {
        "Authorization": "Bearer page-token"
    }
    assert "access_token" not in get.call_args.kwargs["params"]


def test_start_upload_returns_video_id_and_upload_url(monkeypatch):
    post = mock.Mock(return_value=FakeResponse({
        "video_id": "video-1",
        "upload_url": "https://rupload.facebook.com/session",
    }))
    monkeypatch.setattr(facebook_publish.requests, "post", post)

    result = facebook_publish.start_upload("123", "token")

    assert result == ("video-1", "https://rupload.facebook.com/session")
    assert post.call_args.kwargs["data"] == {"upload_phase": "start"}
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer token"


def test_upload_local_reel_uses_binary_body_and_required_headers(
    tmp_path, monkeypatch
):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"video-bytes")
    post = mock.Mock(return_value=FakeResponse({"success": True}))
    monkeypatch.setattr(facebook_publish.requests, "post", post)

    facebook_publish.upload_local_reel(
        "https://rupload.facebook.com/session", "page-token", video
    )

    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "OAuth page-token"
    assert headers["offset"] == "0"
    assert headers["file_size"] == str(len(b"video-bytes"))
    assert Path(post.call_args.kwargs["data"].name) == video


def test_finish_publish_explicitly_requests_published_state(monkeypatch):
    post = mock.Mock(return_value=FakeResponse({"success": True}))
    monkeypatch.setattr(facebook_publish.requests, "post", post)

    facebook_publish.finish_publish(
        "123", "token", "video-1", "Atlas Reel", "Description"
    )

    assert post.call_args.kwargs["data"] == {
        "video_id": "video-1",
        "upload_phase": "finish",
        "video_state": "PUBLISHED",
        "title": "Atlas Reel",
        "description": "Description",
    }


def test_wait_until_published_polls_processing_then_returns(monkeypatch):
    statuses = iter([
        {
            "video_id": "video-1",
            "video_status": "processing",
            "uploading_status": "complete",
            "processing_status": "in_progress",
            "publishing_status": "not_started",
            "permalink": None,
        },
        {
            "video_id": "video-1",
            "video_status": "ready",
            "uploading_status": "complete",
            "processing_status": "complete",
            "publishing_status": "complete",
            "permalink": "https://facebook.example/reel/video-1",
        },
    ])
    monkeypatch.setattr(
        facebook_publish, "get_reel_status", lambda *_args: next(statuses)
    )
    monkeypatch.setattr(facebook_publish.time, "sleep", lambda _seconds: None)

    result = facebook_publish.wait_until_published("video-1", "token")

    assert result["publishing_status"] == "complete"
    assert result["permalink"].endswith("video-1")


def test_publish_reel_orchestrates_and_writes_private_ledger(
    tmp_path, monkeypatch
):
    _config, ledger = configured(tmp_path, monkeypatch)
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(
        facebook_publish,
        "start_upload",
        lambda *_args: ("video-1", "https://upload/session"),
    )
    monkeypatch.setattr(
        facebook_publish, "upload_local_reel", lambda *_args: None
    )
    monkeypatch.setattr(
        facebook_publish, "finish_publish", lambda *_args: None
    )
    monkeypatch.setattr(
        facebook_publish,
        "wait_until_published",
        lambda *_args: {
            "video_id": "video-1",
            "video_status": "ready",
            "publishing_status": "complete",
            "permalink": "https://facebook.example/reel/video-1",
        },
    )

    result = facebook_publish.publish_reel(
        video, "Atlas Reel", "Description", mission="show Atlas"
    )

    assert result["video_id"] == "video-1"
    assert json.loads(ledger.read_text())[0]["mission"] == "show Atlas"
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


def test_publish_reel_rejects_missing_video(tmp_path):
    with pytest.raises(facebook_publish.FacebookPublishError, match="not found"):
        facebook_publish.publish_reel(
            tmp_path / "missing.mp4", "Atlas Reel", "Description"
        )

import json
import stat
from pathlib import Path
from unittest import mock

import pytest
import requests

import youtube_publish


class FakeResponse:
    def __init__(
        self,
        payload=None,
        *,
        status_code=200,
        headers=None,
        text="",
    ):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


def _configured_paths(tmp_path, monkeypatch):
    client_path = tmp_path / "client.json"
    token_path = tmp_path / "token.json"
    ledger_path = tmp_path / "posts.json"
    client_path.write_text(json.dumps({
        "installed": {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }))
    token_path.write_text(json.dumps({
        "access_token": "old-access",
        "refresh_token": "refresh-token",
        "expires_in": 3600,
    }))
    monkeypatch.setattr(youtube_publish, "CLIENT_SECRET_PATH", client_path)
    monkeypatch.setattr(youtube_publish, "TOKEN_PATH", token_path)
    monkeypatch.setattr(youtube_publish, "LEDGER_PATH", ledger_path)
    return client_path, token_path, ledger_path


def test_refresh_access_token_preserves_refresh_token_and_private_mode(
    tmp_path, monkeypatch
):
    _client, token_path, _ledger = _configured_paths(tmp_path, monkeypatch)
    post = mock.Mock(return_value=FakeResponse({
        "access_token": "fresh-access",
        "expires_in": 3599,
        "refresh_token_expires_in": 604800,
        "scope": "youtube.upload youtube.readonly",
        "token_type": "Bearer",
    }))
    monkeypatch.setattr(youtube_publish.requests, "post", post)

    assert youtube_publish.refresh_access_token() == "fresh-access"

    saved = json.loads(token_path.read_text())
    assert saved["refresh_token"] == "refresh-token"
    assert saved["access_token"] == "fresh-access"
    assert saved["expires_at"] > saved["created_at"]
    assert saved["refresh_token_expires_at"] > saved["expires_at"]
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert post.call_args.kwargs["data"]["grant_type"] == "refresh_token"


def test_get_channel_returns_only_safe_identity_fields(tmp_path, monkeypatch):
    _configured_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(youtube_publish, "access_token", lambda: "access")
    monkeypatch.setattr(
        youtube_publish.requests,
        "get",
        mock.Mock(return_value=FakeResponse({
            "items": [{
                "id": "channel-1",
                "snippet": {
                    "title": "A.T.L.A.S.",
                    "customUrl": "@atlas",
                },
                "status": {"privacyStatus": "public"},
            }]
        })),
    )

    assert youtube_publish.get_channel() == {
        "channel_id": "channel-1",
        "title": "A.T.L.A.S.",
        "custom_url": "@atlas",
        "privacy_status": "public",
    }


def test_publish_short_uses_resumable_upload_and_verifies(
    tmp_path, monkeypatch
):
    _configured_paths(tmp_path, monkeypatch)
    video = tmp_path / "short.mp4"
    video.write_bytes(b"video-bytes")
    monkeypatch.setattr(youtube_publish, "access_token", lambda: "access")
    post = mock.Mock(return_value=FakeResponse(
        status_code=200,
        headers={"Location": "https://upload.example/session"},
    ))
    put = mock.Mock(return_value=FakeResponse(
        {"id": "video-1"}, status_code=201
    ))
    get = mock.Mock(return_value=FakeResponse({
        "items": [{
            "id": "video-1",
            "snippet": {"title": "Atlas Short"},
            "status": {
                "privacyStatus": "private",
                "uploadStatus": "uploaded",
            },
            "processingDetails": {"processingStatus": "processing"},
        }]
    }))
    monkeypatch.setattr(youtube_publish.requests, "post", post)
    monkeypatch.setattr(youtube_publish.requests, "put", put)
    monkeypatch.setattr(youtube_publish.requests, "get", get)

    result = youtube_publish.publish_short(
        video,
        "Atlas Short",
        "Raspberry Pi project #raspberrypi",
        privacy_status="private",
        mission="show Atlas",
        tags=["raspberrypi", "piprojects"],
    )

    assert result["video_id"] == "video-1"
    assert result["privacy_status"] == "private"
    assert result["permalink"].endswith("/video-1")
    assert json.loads((tmp_path / "posts.json").read_text())[0][
        "video_id"
    ] == "video-1"
    assert post.call_args.kwargs["params"] == {
        "uploadType": "resumable",
        "part": "snippet,status",
    }
    metadata = post.call_args.kwargs["json"]
    assert metadata["snippet"]["categoryId"] == "28"
    assert metadata["snippet"]["tags"] == ["raspberrypi", "piprojects"]
    assert metadata["status"]["privacyStatus"] == "private"
    assert put.call_args.args[0] == "https://upload.example/session"


def test_publish_short_rejects_missing_video_before_network(tmp_path):
    with pytest.raises(youtube_publish.YouTubePublishError, match="not found"):
        youtube_publish.publish_short(
            tmp_path / "missing.mp4",
            "Atlas Short",
            "Raspberry Pi project",
        )


def test_publish_short_rejects_invalid_privacy(tmp_path):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"video")
    with pytest.raises(youtube_publish.YouTubePublishError, match="privacy_status"):
        youtube_publish.publish_short(
            video,
            "Atlas Short",
            "Raspberry Pi project",
            privacy_status="friends-only",
        )


def test_upload_resumes_after_transport_failure(tmp_path, monkeypatch):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"video-bytes")
    put = mock.Mock(side_effect=[
        requests.ConnectionError("link dropped"),
        FakeResponse(status_code=308),
        FakeResponse({"id": "video-recovered"}, status_code=201),
    ])
    monkeypatch.setattr(youtube_publish.requests, "put", put)
    monkeypatch.setattr(youtube_publish.time, "sleep", lambda _seconds: None)

    result = youtube_publish._upload_file(
        "https://upload.example/session", "access", video
    )

    assert result["id"] == "video-recovered"
    assert put.call_count == 3
    status_headers = put.call_args_list[1].kwargs["headers"]
    assert status_headers["Content-Range"] == "bytes */11"

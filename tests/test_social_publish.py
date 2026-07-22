from pathlib import Path

import pytest

import facebook_publish
import instagram_publish
import social_publish


def _stub_ledgers(monkeypatch):
    monkeypatch.setattr(social_publish, "_record_batch", lambda result: None)
    monkeypatch.setattr(
        instagram_publish, "_record_ledger_entry", lambda result: None
    )
    monkeypatch.setattr(
        facebook_publish, "_record_publish", lambda result: None
    )


def test_publish_prepares_both_then_releases_and_verifies(tmp_path, monkeypatch):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"video")
    calls = []
    _stub_ledgers(monkeypatch)
    monkeypatch.setattr(
        facebook_publish,
        "_validate_inputs",
        lambda video_path, title, caption: Path(video_path),
    )
    monkeypatch.setattr(
        facebook_publish, "_load_config", lambda: ("page-1", "token")
    )
    monkeypatch.setattr(
        social_publish,
        "_prepare_instagram",
        lambda path, caption: calls.append("prepare-instagram") or "container-1",
    )
    monkeypatch.setattr(
        facebook_publish,
        "start_upload",
        lambda page, token: calls.append("prepare-facebook")
        or ("video-1", "https://upload.example"),
    )
    monkeypatch.setattr(
        facebook_publish,
        "upload_local_reel",
        lambda url, token, path: calls.append("upload-facebook"),
    )
    monkeypatch.setattr(
        instagram_publish,
        "publish",
        lambda container: calls.append("publish-instagram") or "media-1",
    )
    monkeypatch.setattr(
        facebook_publish,
        "finish_publish",
        lambda *args: calls.append("publish-facebook"),
    )
    monkeypatch.setattr(
        instagram_publish,
        "verify",
        lambda media_id: {
            "permalink": "https://instagram.example/reel/media-1",
            "timestamp": "2026-07-21T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        facebook_publish,
        "wait_until_published",
        lambda video_id, token: {
            "video_id": video_id,
            "publishing_status": "complete",
            "permalink": "https://facebook.example/reel/video-1",
        },
    )

    result = social_publish.publish_reel(
        video, "Atlas Reel", "caption", mission="demo"
    )

    assert result["ok"] is True
    assert calls[:3] == [
        "prepare-instagram", "prepare-facebook", "upload-facebook"
    ]
    assert set(calls[3:5]) == {"publish-instagram", "publish-facebook"}
    assert result["instagram"]["permalink"].startswith("https://instagram")
    assert result["facebook"]["permalink"].startswith("https://facebook")


def test_preparation_failure_posts_neither_platform(tmp_path, monkeypatch):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(
        facebook_publish,
        "_validate_inputs",
        lambda video_path, title, caption: Path(video_path),
    )
    monkeypatch.setattr(
        facebook_publish, "_load_config", lambda: ("page-1", "token")
    )
    monkeypatch.setattr(
        social_publish,
        "_prepare_instagram",
        lambda path, caption: "container-1",
    )
    monkeypatch.setattr(
        facebook_publish,
        "start_upload",
        lambda page, token: (_ for _ in ()).throw(
            facebook_publish.FacebookPublishError("token rejected")
        ),
    )
    monkeypatch.setattr(
        instagram_publish,
        "publish",
        lambda container: pytest.fail("Instagram must not publish"),
    )
    monkeypatch.setattr(
        facebook_publish,
        "finish_publish",
        lambda *args: pytest.fail("Facebook must not publish"),
    )

    with pytest.raises(social_publish.SocialPublishError, match="neither|Both"):
        social_publish.publish_reel(video, "Atlas Reel", "caption")


def test_partial_final_failure_is_reported_without_hiding_success(
    tmp_path, monkeypatch
):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"video")
    _stub_ledgers(monkeypatch)
    monkeypatch.setattr(
        facebook_publish,
        "_validate_inputs",
        lambda video_path, title, caption: Path(video_path),
    )
    monkeypatch.setattr(
        facebook_publish, "_load_config", lambda: ("page-1", "token")
    )
    monkeypatch.setattr(
        social_publish, "_prepare_instagram", lambda path, caption: "container-1"
    )
    monkeypatch.setattr(
        facebook_publish,
        "start_upload",
        lambda page, token: ("video-1", "https://upload.example"),
    )
    monkeypatch.setattr(
        facebook_publish, "upload_local_reel", lambda *args: None
    )
    monkeypatch.setattr(
        instagram_publish, "publish", lambda container: "media-1"
    )
    monkeypatch.setattr(
        facebook_publish,
        "finish_publish",
        lambda *args: (_ for _ in ()).throw(
            facebook_publish.FacebookPublishError("publish rejected")
        ),
    )
    monkeypatch.setattr(
        instagram_publish,
        "verify",
        lambda media_id: {
            "permalink": "https://instagram.example/reel/media-1",
            "timestamp": "2026-07-21T00:00:00Z",
        },
    )

    result = social_publish.publish_reel(video, "Atlas Reel", "caption")

    assert result["ok"] is False
    assert result["instagram"]["verified"] is True
    assert result["facebook"]["accepted"] is False
    assert "publish rejected" in result["facebook"]["error"]

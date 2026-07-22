"""YouTube Shorts publishing for Atlas's owner-approved content pipeline.

This module deliberately has no device-authorization UI.  OAuth setup is a
one-time owner action; runtime code only refreshes the stored access token,
uploads one exact MP4 with YouTube's resumable protocol, and verifies the
result through ``videos.list``.

Uploads from YouTube API projects that have not completed Google's API audit
are restricted to private viewing by Google.  Atlas records the *actual*
privacy returned by the API instead of claiming that such an upload is public.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests


CONFIG_DIRECTORY = Path("/home/atlas/.config/atlas")
CLIENT_SECRET_PATH = CONFIG_DIRECTORY / "youtube_client_secret.json"
TOKEN_PATH = CONFIG_DIRECTORY / "youtube_token.json"
LEDGER_PATH = CONFIG_DIRECTORY / "youtube_posts.json"

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
REQUEST_TIMEOUT_SECONDS = 30
UPLOAD_TIMEOUT_SECONDS = 600
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024  # multiple of YouTube's 256 KiB unit
UPLOAD_MAX_RETRIES = 5
UPLOAD_RETRY_BASE_SECONDS = 1

ALLOWED_PRIVACY_STATUSES = frozenset({"private", "unlisted", "public"})
YOUTUBE_TITLE_MAX_LENGTH = 100
YOUTUBE_DESCRIPTION_MAX_LENGTH = 5000


class YouTubePublishError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise YouTubePublishError(f"YouTube credential file is missing: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise YouTubePublishError(f"Could not read YouTube credentials: {error}") from error

    if not isinstance(payload, dict):
        raise YouTubePublishError(f"YouTube credential file is invalid: {path}")
    return payload


def _write_private_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _client_credentials() -> tuple[str, str, str]:
    payload = _read_json(CLIENT_SECRET_PATH)
    client = payload.get("installed") or payload.get("web")
    if not isinstance(client, dict):
        raise YouTubePublishError(
            "YouTube OAuth client file contains neither installed nor web credentials"
        )
    client_id = client.get("client_id")
    client_secret = client.get("client_secret")
    token_uri = client.get("token_uri") or TOKEN_ENDPOINT
    if not all(isinstance(value, str) and value for value in (
        client_id, client_secret, token_uri
    )):
        raise YouTubePublishError("YouTube OAuth client credentials are incomplete")
    return client_id, client_secret, token_uri


def _api_error(response: requests.Response, action: str) -> YouTubePublishError:
    detail = response.text[:1000]
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or detail)
        elif isinstance(error, str):
            detail = str(payload.get("error_description") or error)
    except (ValueError, TypeError):
        pass
    return YouTubePublishError(
        f"YouTube {action} failed with HTTP {response.status_code}: {detail}"
    )


def refresh_access_token() -> str:
    """Refresh and durably store an access token without exposing secrets."""
    token = _read_json(TOKEN_PATH)
    refresh_token = token.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise YouTubePublishError(
            "YouTube refresh token is missing; owner authorization must be repeated"
        )
    client_id, client_secret, token_uri = _client_credentials()
    try:
        response = requests.post(
            token_uri,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise YouTubePublishError(f"YouTube token refresh failed: {error}") from error
    if not response.ok:
        raise _api_error(response, "token refresh")
    try:
        refreshed = response.json()
    except ValueError as error:
        raise YouTubePublishError("YouTube token refresh returned invalid JSON") from error
    access_token = refreshed.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise YouTubePublishError("YouTube token refresh returned no access token")

    now = time.time()
    updated = {
        **token,
        **refreshed,
        "refresh_token": refresh_token,
        "created_at": now,
        "expires_at": now + int(refreshed.get("expires_in") or 3600),
    }
    refresh_lifetime = refreshed.get("refresh_token_expires_in")
    if isinstance(refresh_lifetime, (int, float)):
        updated["refresh_token_expires_at"] = now + float(refresh_lifetime)
    _write_private_json(TOKEN_PATH, updated)
    return access_token


def access_token() -> str:
    token = _read_json(TOKEN_PATH)
    current = token.get("access_token")
    expires_at = token.get("expires_at")
    if (
        isinstance(current, str)
        and current
        and isinstance(expires_at, (int, float))
        and float(expires_at) > time.time() + 60
    ):
        return current
    return refresh_access_token()


def _authorized_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_channel() -> dict[str, Any]:
    """Return safe identity fields for the currently authorized channel."""
    token = access_token()
    try:
        response = requests.get(
            f"{API_BASE}/channels",
            params={"part": "snippet,status", "mine": "true"},
            headers=_authorized_headers(token),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise YouTubePublishError(f"YouTube channel check failed: {error}") from error
    if not response.ok:
        raise _api_error(response, "channel check")
    items = response.json().get("items") or []
    if len(items) != 1:
        raise YouTubePublishError(
            "YouTube authorization did not resolve to exactly one channel"
        )
    item = items[0]
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    return {
        "channel_id": item.get("id"),
        "title": snippet.get("title"),
        "custom_url": snippet.get("customUrl"),
        "privacy_status": status.get("privacyStatus"),
    }


def _validate_publish_inputs(
    video_path: str | Path,
    title: str,
    description: str,
    privacy_status: str,
) -> Path:
    path = Path(video_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise YouTubePublishError(f"video file not found or empty: {path}")
    if path.suffix.casefold() != ".mp4":
        raise YouTubePublishError("YouTube Shorts uploader accepts MP4 files only")
    if not isinstance(title, str) or not title.strip():
        raise YouTubePublishError("YouTube title must not be empty")
    if len(title) > YOUTUBE_TITLE_MAX_LENGTH:
        raise YouTubePublishError(
            f"YouTube title exceeds {YOUTUBE_TITLE_MAX_LENGTH} characters"
        )
    if not isinstance(description, str) or not description.strip():
        raise YouTubePublishError("YouTube description must not be empty")
    if len(description) > YOUTUBE_DESCRIPTION_MAX_LENGTH:
        raise YouTubePublishError(
            f"YouTube description exceeds {YOUTUBE_DESCRIPTION_MAX_LENGTH} characters"
        )
    if privacy_status not in ALLOWED_PRIVACY_STATUSES:
        raise YouTubePublishError(
            "YouTube privacy_status must be private, unlisted, or public"
        )
    return path


def _start_resumable_upload(
    *,
    token: str,
    video_path: Path,
    title: str,
    description: str,
    privacy_status: str,
    tags: list[str],
) -> str:
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "28",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    headers = {
        **_authorized_headers(token),
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(video_path.stat().st_size),
        "X-Upload-Content-Type": "video/mp4",
    }
    try:
        response = requests.post(
            UPLOAD_ENDPOINT,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers=headers,
            json=metadata,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise YouTubePublishError(
            f"YouTube resumable upload could not start: {error}"
        ) from error
    if not response.ok:
        raise _api_error(response, "resumable upload initialization")
    location = response.headers.get("Location")
    if not location:
        raise YouTubePublishError("YouTube returned no resumable upload URL")
    return location


def _completed_upload_payload(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise YouTubePublishError("YouTube upload returned invalid JSON") from error
    if not payload.get("id"):
        raise YouTubePublishError("YouTube upload returned no video id")
    return payload


def _offset_from_range(response: requests.Response) -> int:
    value = response.headers.get("Range")
    if not value:
        return 0
    try:
        return int(value.rsplit("-", 1)[1]) + 1
    except (IndexError, ValueError) as error:
        raise YouTubePublishError(
            f"YouTube returned an invalid upload Range header: {value!r}"
        ) from error


def _query_upload_status(
    upload_url: str,
    token: str,
    total_size: int,
) -> tuple[int, dict[str, Any] | None]:
    try:
        response = requests.put(
            upload_url,
            headers={
                **_authorized_headers(token),
                "Content-Length": "0",
                "Content-Range": f"bytes */{total_size}",
            },
            data=b"",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise YouTubePublishError(
            f"YouTube upload status check failed: {error}"
        ) from error
    if response.status_code in {200, 201}:
        return total_size, _completed_upload_payload(response)
    if response.status_code == 308:
        return _offset_from_range(response), None
    raise _api_error(response, "upload status check")


def _upload_file(upload_url: str, token: str, video_path: Path) -> dict[str, Any]:
    """Upload in resumable chunks and recover from transport/5xx failures."""
    total_size = video_path.stat().st_size
    offset = 0
    failures = 0
    with video_path.open("rb") as video:
        while offset < total_size:
            video.seek(offset)
            chunk = video.read(min(UPLOAD_CHUNK_BYTES, total_size - offset))
            end = offset + len(chunk) - 1
            try:
                response = requests.put(
                    upload_url,
                    headers={
                        **_authorized_headers(token),
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{total_size}",
                    },
                    data=chunk,
                    timeout=UPLOAD_TIMEOUT_SECONDS,
                )
            except requests.RequestException:
                response = None

            if response is not None and response.status_code in {200, 201}:
                return _completed_upload_payload(response)
            if response is not None and response.status_code == 308:
                offset = _offset_from_range(response)
                failures = 0
                continue
            if response is not None and not 500 <= response.status_code < 600:
                raise _api_error(response, "video upload")

            failures += 1
            if failures > UPLOAD_MAX_RETRIES:
                raise YouTubePublishError(
                    "YouTube video upload could not recover after "
                    f"{UPLOAD_MAX_RETRIES} retries"
                )
            time.sleep(UPLOAD_RETRY_BASE_SECONDS * (2 ** (failures - 1)))
            try:
                offset, completed = _query_upload_status(
                    upload_url,
                    token,
                    total_size,
                )
            except YouTubePublishError:
                continue
            if completed is not None:
                return completed

    raise YouTubePublishError("YouTube upload ended before completion")


def verify_video(video_id: str, *, token: str | None = None) -> dict[str, Any]:
    token = token or access_token()
    try:
        response = requests.get(
            f"{API_BASE}/videos",
            params={"part": "snippet,status,processingDetails", "id": video_id},
            headers=_authorized_headers(token),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise YouTubePublishError(f"YouTube upload verification failed: {error}") from error
    if not response.ok:
        raise _api_error(response, "upload verification")
    items = response.json().get("items") or []
    if len(items) != 1:
        raise YouTubePublishError("Uploaded YouTube video could not be read back")
    item = items[0]
    return {
        "video_id": item.get("id"),
        "title": (item.get("snippet") or {}).get("title"),
        "privacy_status": (item.get("status") or {}).get("privacyStatus"),
        "upload_status": (item.get("status") or {}).get("uploadStatus"),
        "processing_status": (
            item.get("processingDetails") or {}
        ).get("processingStatus"),
        "permalink": f"https://www.youtube.com/shorts/{item.get('id')}",
    }


def _record_publish(result: dict[str, Any]) -> None:
    try:
        ledger = _read_json(LEDGER_PATH) if LEDGER_PATH.is_file() else []
    except YouTubePublishError:
        ledger = []
    if not isinstance(ledger, list):
        ledger = []
    ledger.append(result)
    _write_private_json(LEDGER_PATH, ledger[-500:])


def publish_short(
    video_path: str | Path,
    title: str,
    description: str,
    *,
    privacy_status: str = "private",
    mission: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Upload one exact owner-approved vertical MP4 and verify it live."""
    path = _validate_publish_inputs(video_path, title, description, privacy_status)
    clean_tags = [
        str(tag).strip().lstrip("#")[:30]
        for tag in (tags or [])
        if str(tag).strip()
    ][:30]
    token = access_token()
    upload_url = _start_resumable_upload(
        token=token,
        video_path=path,
        title=title.strip(),
        description=description.strip(),
        privacy_status=privacy_status,
        tags=clean_tags,
    )
    uploaded = _upload_file(upload_url, token, path)
    verified = verify_video(str(uploaded["id"]), token=token)
    result = {
        **verified,
        "video_path": str(path),
        "description": description.strip(),
        "mission": mission,
        "requested_privacy_status": privacy_status,
        "created_at": time.time(),
    }
    _record_publish(result)
    return result

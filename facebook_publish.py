"""Owner-confirmed Facebook Page Reel publishing for Atlas."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests


CONFIG_PATH = Path("/home/atlas/.config/atlas/facebook.env")
LEDGER_PATH = CONFIG_PATH.with_name("facebook_posts.json")
API_VERSION = "v24.0"
API_BASE = f"https://graph.facebook.com/{API_VERSION}"
REQUEST_TIMEOUT_SECONDS = 30
UPLOAD_TIMEOUT_SECONDS = 600
STATUS_POLL_INTERVAL_SECONDS = 3
STATUS_POLL_MAX_ATTEMPTS = 60
MIN_REEL_SECONDS = 4.0
MAX_REEL_SECONDS = 60.0
MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 5000

TERMINAL_FAILURE_STATUSES = frozenset({"error", "failed", "expired"})
TERMINAL_SUCCESS_STATUSES = frozenset({"complete", "completed", "published", "ready"})


class FacebookPublishError(RuntimeError):
    pass


def _load_config() -> tuple[str, str]:
    try:
        lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise FacebookPublishError(
            f"Facebook credential file could not be read: {error}"
        ) from error
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    page_id = values.get("FACEBOOK_PAGE_ID")
    token = values.get("FACEBOOK_PAGE_ACCESS_TOKEN")
    if not page_id or not token:
        raise FacebookPublishError(
            "Facebook is not configured with a Page ID and Page access token"
        )
    return page_id, token


def _private_json_write(path: Path, payload: Any) -> None:
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


def _error(response: requests.Response, action: str) -> FacebookPublishError:
    detail = response.text[:1000]
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or detail)
    except (ValueError, TypeError):
        pass
    return FacebookPublishError(
        f"Facebook {action} failed with HTTP {response.status_code}: {detail}"
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_page() -> dict[str, Any]:
    page_id, token = _load_config()
    try:
        response = requests.get(
            f"{API_BASE}/{page_id}",
            params={"fields": "id,name,category,link"},
            headers=_bearer(token),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise FacebookPublishError(f"Facebook Page check failed: {error}") from error
    if not response.ok:
        raise _error(response, "Page check")
    payload = response.json()
    if str(payload.get("id")) != page_id:
        raise FacebookPublishError("Facebook token resolved to the wrong Page")
    return {
        "page_id": payload.get("id"),
        "name": payload.get("name"),
        "category": payload.get("category"),
        "link": payload.get("link"),
    }


def _validate_inputs(
    video_path: str | Path,
    title: str,
    description: str,
) -> Path:
    path = Path(video_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise FacebookPublishError(f"video file not found or empty: {path}")
    if path.suffix.casefold() != ".mp4":
        raise FacebookPublishError("Facebook Reel publisher accepts MP4 files only")
    if not isinstance(title, str) or not title.strip():
        raise FacebookPublishError("Facebook Reel title must not be empty")
    if len(title) > MAX_TITLE_LENGTH:
        raise FacebookPublishError(
            f"Facebook Reel title exceeds {MAX_TITLE_LENGTH} characters"
        )
    if not isinstance(description, str) or not description.strip():
        raise FacebookPublishError("Facebook Reel description must not be empty")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise FacebookPublishError(
            f"Facebook Reel description exceeds {MAX_DESCRIPTION_LENGTH} characters"
        )
    return path


def start_upload(page_id: str, token: str) -> tuple[str, str]:
    try:
        response = requests.post(
            f"{API_BASE}/{page_id}/video_reels",
            data={"upload_phase": "start"},
            headers=_bearer(token),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise FacebookPublishError(
            f"Facebook Reel upload could not start: {error}"
        ) from error
    if not response.ok:
        raise _error(response, "Reel upload initialization")
    payload = response.json()
    video_id = payload.get("video_id")
    upload_url = payload.get("upload_url")
    if not video_id or not upload_url:
        raise FacebookPublishError(
            "Facebook returned no video id or upload URL"
        )
    return str(video_id), str(upload_url)


def upload_local_reel(upload_url: str, token: str, video_path: Path) -> None:
    size = video_path.stat().st_size
    try:
        with video_path.open("rb") as video:
            response = requests.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(size),
                    "Content-Type": "application/octet-stream",
                },
                data=video,
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
    except requests.RequestException as error:
        raise FacebookPublishError(f"Facebook Reel upload failed: {error}") from error
    if not response.ok:
        raise _error(response, "Reel file upload")
    try:
        success = response.json().get("success") is True
    except ValueError:
        success = False
    if not success:
        raise FacebookPublishError("Facebook did not confirm the Reel file upload")


def finish_publish(
    page_id: str,
    token: str,
    video_id: str,
    title: str,
    description: str,
) -> None:
    try:
        response = requests.post(
            f"{API_BASE}/{page_id}/video_reels",
            data={
                "video_id": video_id,
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "title": title,
                "description": description,
            },
            headers=_bearer(token),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise FacebookPublishError(f"Facebook Reel publish failed: {error}") from error
    if not response.ok:
        raise _error(response, "Reel publish")
    try:
        success = response.json().get("success") is True
    except ValueError:
        success = False
    if not success:
        raise FacebookPublishError("Facebook did not accept the Reel publish request")


def _phase_status(status: dict[str, Any], phase: str) -> str:
    value = status.get(phase)
    if isinstance(value, dict):
        value = value.get("status")
    return str(value or "").casefold()


def get_reel_status(video_id: str, token: str) -> dict[str, Any]:
    try:
        response = requests.get(
            f"{API_BASE}/{video_id}",
            params={"fields": "status,permalink_url"},
            headers=_bearer(token),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise FacebookPublishError(f"Facebook Reel status check failed: {error}") from error
    if not response.ok:
        raise _error(response, "Reel status check")
    payload = response.json()
    status = payload.get("status") or {}
    return {
        "video_id": str(payload.get("id") or video_id),
        "video_status": str(status.get("video_status") or "").casefold(),
        "uploading_status": _phase_status(status, "uploading_phase"),
        "processing_status": _phase_status(status, "processing_phase"),
        "publishing_status": _phase_status(status, "publishing_phase"),
        "permalink": payload.get("permalink_url"),
    }


def wait_until_published(video_id: str, token: str) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for attempt in range(STATUS_POLL_MAX_ATTEMPTS):
        last = get_reel_status(video_id, token)
        statuses = {
            last.get("video_status"),
            last.get("uploading_status"),
            last.get("processing_status"),
            last.get("publishing_status"),
        }
        failed = sorted(
            value for value in statuses if value in TERMINAL_FAILURE_STATUSES
        )
        if failed:
            raise FacebookPublishError(
                "Facebook Reel processing failed with status " + failed[0]
            )
        if (
            last.get("publishing_status") in TERMINAL_SUCCESS_STATUSES
            or last.get("video_status") in {"published", "ready"}
        ):
            if not last.get("permalink"):
                last["permalink"] = f"https://www.facebook.com/reel/{video_id}"
            return last
        if attempt + 1 < STATUS_POLL_MAX_ATTEMPTS:
            time.sleep(STATUS_POLL_INTERVAL_SECONDS)
    raise FacebookPublishError(
        "Facebook Reel did not finish processing before the timeout"
    )


def _record_publish(result: dict[str, Any]) -> None:
    try:
        ledger = (
            json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
            if LEDGER_PATH.is_file()
            else []
        )
    except (OSError, json.JSONDecodeError):
        ledger = []
    if not isinstance(ledger, list):
        ledger = []
    ledger.append(result)
    _private_json_write(LEDGER_PATH, ledger[-500:])


def publish_reel(
    video_path: str | Path,
    title: str,
    description: str,
    *,
    mission: str | None = None,
) -> dict[str, Any]:
    path = _validate_inputs(video_path, title, description)
    page_id, token = _load_config()
    video_id, upload_url = start_upload(page_id, token)
    upload_local_reel(upload_url, token, path)
    finish_publish(
        page_id,
        token,
        video_id,
        title.strip(),
        description.strip(),
    )
    verified = wait_until_published(video_id, token)
    result = {
        **verified,
        "page_id": page_id,
        "video_path": str(path),
        "title": title.strip(),
        "description": description.strip(),
        "mission": mission,
        "posted_at": time.time(),
    }
    _record_publish(result)
    return result

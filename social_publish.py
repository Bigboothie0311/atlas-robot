"""Coordinated Instagram + Facebook Reel publishing.

Both uploads are prepared before either irreversible publish request is
sent.  The two final requests are then released through a barrier so one
owner confirmation represents one coordinated two-platform action.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import facebook_publish
import instagram_publish


LEDGER_PATH = Path("/home/atlas/.config/atlas/social_posts.json")


class SocialPublishError(RuntimeError):
    pass


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


def _record_batch(result: dict[str, Any]) -> None:
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


def _prepare_instagram(path: Path, caption: str) -> str:
    with instagram_publish._funnel_video_url(path) as video_url:
        for attempt in range(
            1, instagram_publish.CONTAINER_PROCESSING_ATTEMPTS + 1
        ):
            container_id = instagram_publish.create_container_with_video_url(
                video_url, caption
            )
            try:
                instagram_publish.poll_container_status(container_id)
                return container_id
            except instagram_publish.ContainerProcessingError as error:
                if (
                    error.status != "ERROR"
                    or attempt
                    == instagram_publish.CONTAINER_PROCESSING_ATTEMPTS
                ):
                    raise
                time.sleep(instagram_publish.CONTAINER_RETRY_SECONDS)
    raise SocialPublishError("Instagram Reel preparation did not finish")


def _release_together(
    instagram_container_id: str,
    *,
    facebook_page_id: str,
    facebook_token: str,
    facebook_video_id: str,
    title: str,
    description: str,
) -> dict[str, dict[str, Any]]:
    """Release both final calls together and retain partial-failure facts."""
    barrier = threading.Barrier(2)

    def run(name: str, action: Callable[[], Any]) -> tuple[str, Any]:
        barrier.wait()
        return name, action()

    actions = {
        "instagram": lambda: instagram_publish.publish(
            instagram_container_id
        ),
        "facebook": lambda: facebook_publish.finish_publish(
            facebook_page_id,
            facebook_token,
            facebook_video_id,
            title,
            description,
        ),
    }
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            name: pool.submit(run, name, action)
            for name, action in actions.items()
        }
        for name, future in futures.items():
            try:
                _returned_name, value = future.result()
                results[name] = {"accepted": True, "value": value}
            except Exception as error:  # retain the other platform's result
                results[name] = {
                    "accepted": False,
                    "error": f"{type(error).__name__}: {error}",
                }
    return results


def publish_reel(
    video_path: str | Path,
    title: str,
    caption: str,
    *,
    mission: str | None = None,
) -> dict[str, Any]:
    """Prepare both platforms, then concurrently publish and verify them."""
    try:
        path = facebook_publish._validate_inputs(video_path, title, caption)
        facebook_page_id, facebook_token = facebook_publish._load_config()
        instagram_container_id = _prepare_instagram(path, caption)
        facebook_video_id, upload_url = facebook_publish.start_upload(
            facebook_page_id, facebook_token
        )
        facebook_publish.upload_local_reel(
            upload_url, facebook_token, path
        )
    except (
        facebook_publish.FacebookPublishError,
        instagram_publish.InstagramPublishError,
        OSError,
    ) as error:
        raise SocialPublishError(
            "Both posts were stopped before publishing because preparation "
            f"failed: {error}"
        ) from error

    released_at = time.time()
    released = _release_together(
        instagram_container_id,
        facebook_page_id=facebook_page_id,
        facebook_token=facebook_token,
        facebook_video_id=facebook_video_id,
        title=title.strip(),
        description=caption.strip(),
    )

    instagram_result: dict[str, Any] = {
        "accepted": released["instagram"]["accepted"]
    }
    facebook_result: dict[str, Any] = {
        "accepted": released["facebook"]["accepted"]
    }

    if instagram_result["accepted"]:
        media_id = str(released["instagram"]["value"])
        try:
            details = instagram_publish.verify(media_id)
            instagram_result.update(
                {
                    "verified": True,
                    "media_id": media_id,
                    "permalink": details.get("permalink"),
                    "timestamp": details.get("timestamp"),
                }
            )
            instagram_publish._record_ledger_entry(
                {
                    **instagram_result,
                    "caption": caption,
                    "mission": mission,
                    "video_path": str(path),
                    "posted_at": released_at,
                }
            )
        except instagram_publish.InstagramPublishError as error:
            instagram_result.update(
                {"verified": False, "error": str(error)}
            )
    else:
        instagram_result.update(
            {"verified": False, "error": released["instagram"]["error"]}
        )

    if facebook_result["accepted"]:
        try:
            details = facebook_publish.wait_until_published(
                facebook_video_id, facebook_token
            )
            facebook_result.update({"verified": True, **details})
            facebook_publish._record_publish(
                {
                    **facebook_result,
                    "page_id": facebook_page_id,
                    "video_path": str(path),
                    "title": title.strip(),
                    "description": caption.strip(),
                    "mission": mission,
                    "posted_at": released_at,
                }
            )
        except facebook_publish.FacebookPublishError as error:
            facebook_result.update(
                {"verified": False, "error": str(error)}
            )
    else:
        facebook_result.update(
            {"verified": False, "error": released["facebook"]["error"]}
        )

    result = {
        "ok": bool(
            instagram_result.get("verified")
            and instagram_result.get("permalink")
            and facebook_result.get("verified")
            and facebook_result.get("permalink")
        ),
        "video_path": str(path),
        "title": title.strip(),
        "caption": caption.strip(),
        "mission": mission,
        "released_at": released_at,
        "instagram": instagram_result,
        "facebook": facebook_result,
    }
    if not result["ok"]:
        result["error"] = (
            "The coordinated publish was only partially verified; see the "
            "per-platform results. A platform that accepted its final request "
            "may already be public."
        )
    _record_batch(result)
    return result

"""Small, read-only Instagram Insights client for A.T.L.A.S.

The token lives outside the repository at ~/.config/atlas/instagram.env.
This module only performs GET requests and caches results so voice commands
and the HUD never turn a question into a burst of API traffic.
"""

from __future__ import annotations

from datetime import datetime
import time
from pathlib import Path
from typing import Any

import requests

from atlas_growth import GrowthStore


CONFIG_PATH = Path("/home/atlas/.config/atlas/instagram.env")
API_BASE = "https://graph.instagram.com/v24.0"
CACHE_SECONDS = 15 * 60
REQUEST_TIMEOUT_SECONDS = 12

_cache = {"data": None, "fetched_at": 0.0}
_growth_fetched_at = 0.0
GROWTH_REFRESH_SECONDS = 60 * 60
GROWTH_MEDIA_LIMIT = 25
GROWTH_COMMENTS_PER_MEDIA = 50
GROWTH_METRICS = (
    "views", "reach", "shares", "saved", "total_interactions",
    "ig_reels_avg_watch_time", "ig_reels_video_view_total_time",
    "replays", "follows",
)


def _load_config():
    """Read the private token file without ever logging its contents."""
    if not CONFIG_PATH.exists():
        return {}

    values = {}
    try:
        for raw_line in CONFIG_PATH.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}

    return values


def _unconfigured():
    return {
        "configured": False,
        "available": False,
        "stale": False,
        "username": None,
        "followers_count": None,
        "media_count": None,
        "latest": None,
        "updated_at": None,
        "error": None,
    }


def _request(path, token, **params):
    response = requests.get(
        f"{API_BASE}/{path.lstrip('/')}",
        params={**params, "access_token": token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, dict) and payload.get("error"):
        raise requests.RequestException("Instagram returned an API error")

    return payload


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_media_insights(media_id, token):
    """Fetch current Reel/post metrics; unavailable metrics are normal."""
    metrics = "views,reach,shares,saved,total_interactions"

    try:
        payload = _request(f"{media_id}/insights", token, metric=metrics)
    except requests.RequestException:
        return {}

    values = {}
    for item in payload.get("data", []):
        name = item.get("name")
        data = item.get("values") or []
        if name and data:
            values[name] = _as_int(data[0].get("value"))
    return values


def _read_growth_insights(media_id: str, token: str) -> dict[str, int | None]:
    """Ask for richer Reel metrics, then degrade to the proven basic set."""
    try:
        payload = _request(
            f"{media_id}/insights",
            token,
            metric=",".join(GROWTH_METRICS),
        )
    except requests.RequestException:
        return _read_media_insights(media_id, token)
    values: dict[str, int | None] = {}
    for item in payload.get("data", []):
        name = item.get("name")
        data = item.get("values") or []
        if name and data:
            values[str(name)] = _as_int(data[0].get("value"))
    return values


def _timestamp_epoch(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_growth_snapshot(
    *,
    media_limit: int = GROWTH_MEDIA_LIMIT,
    comments_per_media: int = GROWTH_COMMENTS_PER_MEDIA,
) -> dict[str, Any]:
    """Fetch read-only history, per-Reel insights, and public comments."""
    config = _load_config()
    token = config.get("INSTAGRAM_ACCESS_TOKEN")
    account_id = config.get("INSTAGRAM_ACCOUNT_ID")
    if not token or not account_id:
        return {"configured": False, "media": [], "captured_at": time.time()}

    media_payload = _request(
        f"{account_id}/media",
        token,
        fields=(
            "id,media_type,media_product_type,timestamp,permalink,"
            "like_count,comments_count"
        ),
        limit=max(1, min(int(media_limit), 50)),
    )
    media_items = []
    for media in media_payload.get("data") or []:
        media_id = str(media.get("id") or "")
        if not media_id:
            continue
        insights = _read_growth_insights(media_id, token)
        comments = []
        try:
            comment_payload = _request(
                f"{media_id}/comments",
                token,
                fields="id,text,username,timestamp,like_count",
                limit=max(1, min(int(comments_per_media), 100)),
            )
            comments = comment_payload.get("data") or []
        except requests.RequestException:
            # Comment access can be permission-limited independently of insights.
            comments = []
        media_items.append(
            {
                "id": media_id,
                "timestamp": media.get("timestamp"),
                "posted_at_epoch": _timestamp_epoch(media.get("timestamp")),
                "permalink": media.get("permalink"),
                "media_type": media.get("media_type"),
                "product_type": media.get("media_product_type"),
                "likes": _as_int(media.get("like_count")),
                "comments": _as_int(media.get("comments_count")),
                "insights": insights,
                "public_comments": comments,
            }
        )
    return {
        "configured": True,
        "media": media_items,
        "captured_at": time.time(),
    }


def refresh_growth_memory(
    *,
    store: GrowthStore | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Refresh the local growth database at most hourly unless forced."""
    global _growth_fetched_at
    now = time.time()
    if not force and now - _growth_fetched_at < GROWTH_REFRESH_SECONDS:
        return {"refreshed": False, "reason": "not_due"}
    snapshot = fetch_growth_snapshot()
    if not snapshot.get("configured"):
        return {"refreshed": False, "reason": "not_configured"}
    memory = store or GrowthStore()
    for media in snapshot.get("media") or []:
        memory.ensure_instagram_media(media)
        memory.record_insights(media, captured_at=snapshot["captured_at"])
        memory.record_comments(str(media["id"]), media.get("public_comments") or [])
    missions = memory.draft_comment_missions()
    _growth_fetched_at = now
    return {
        "refreshed": True,
        "media_count": len(snapshot.get("media") or []),
        "new_mission_drafts": len(missions),
    }


def _fetch_stats(config):
    token = config["INSTAGRAM_ACCESS_TOKEN"]
    account_id = config["INSTAGRAM_ACCOUNT_ID"]

    profile = _request(
        account_id,
        token,
        fields="id,username,followers_count,media_count",
    )
    media_payload = _request(
        f"{account_id}/media",
        token,
        fields=(
            "id,media_type,media_product_type,timestamp,permalink,"
            "like_count,comments_count"
        ),
        limit=1,
    )
    media = (media_payload.get("data") or [None])[0]
    latest = None

    if media:
        insights = _read_media_insights(media["id"], token)
        latest = {
            "id": media.get("id"),
            "media_type": media.get("media_type"),
            "product_type": media.get("media_product_type"),
            "timestamp": media.get("timestamp"),
            "permalink": media.get("permalink"),
            "likes": _as_int(media.get("like_count")),
            "comments": _as_int(media.get("comments_count")),
            "views": insights.get("views"),
            "reach": insights.get("reach"),
            "shares": insights.get("shares"),
            "saved": insights.get("saved"),
            "interactions": insights.get("total_interactions"),
        }

    return {
        "configured": True,
        "available": True,
        "stale": False,
        "username": profile.get("username"),
        "followers_count": _as_int(profile.get("followers_count")),
        "media_count": _as_int(profile.get("media_count")),
        "latest": latest,
        "updated_at": time.time(),
        "error": None,
    }


def get_stats(allow_fetch=True):
    """Return cached Instagram account + latest-media stats.

    Pass allow_fetch=False from latency-sensitive HTTP paths. They get an
    immediately available cached snapshot while the hub refresher performs
    the actual network call in the background.
    """
    config = _load_config()
    if not config.get("INSTAGRAM_ACCESS_TOKEN") or not config.get("INSTAGRAM_ACCOUNT_ID"):
        return _unconfigured()

    now = time.time()
    cached = _cache["data"]
    if cached is not None and now - _cache["fetched_at"] < CACHE_SECONDS:
        return dict(cached)

    if not allow_fetch:
        if cached is not None:
            stale = dict(cached)
            stale["stale"] = True
            return stale
        pending = _unconfigured()
        pending["configured"] = True
        pending["stale"] = True
        return pending

    try:
        data = _fetch_stats(config)
    except (KeyError, OSError, ValueError, requests.RequestException) as error:
        if cached is not None:
            stale = dict(cached)
            stale["stale"] = True
            return stale

        return {
            "configured": True,
            "available": False,
            "stale": True,
            "username": None,
            "followers_count": None,
            "media_count": None,
            "latest": None,
            "updated_at": None,
            "error": type(error).__name__,
        }

    _cache["data"] = data
    _cache["fetched_at"] = now
    return dict(data)

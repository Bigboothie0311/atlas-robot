"""Small, read-only Instagram Insights client for A.T.L.A.S.

The token lives outside the repository at ~/.config/atlas/instagram.env.
This module only performs GET requests and caches results so voice commands
and the HUD never turn a question into a burst of API traffic.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests


CONFIG_PATH = Path("/home/atlas/.config/atlas/instagram.env")
API_BASE = "https://graph.instagram.com/v24.0"
CACHE_SECONDS = 15 * 60
REQUEST_TIMEOUT_SECONDS = 12

_cache = {"data": None, "fetched_at": 0.0}


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

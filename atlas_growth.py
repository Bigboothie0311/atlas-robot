"""Local growth memory and planning for A.T.L.A.S. short-form videos.

This module deliberately owns no publishing credentials and performs no
external writes.  It turns Reel history, Instagram read-only snapshots, and
public comments into local plans and draft missions.  The existing confirmed
publish tool remains the only path that can put anything online.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DATABASE_PATH = Path("/home/atlas/.config/atlas/growth.sqlite3")
DATABASE_VERSION = 1
COMMENT_REQUEST_PATTERN = re.compile(
    r"(?:\?|\bcan you\b|\bcould you\b|\bwill you\b|\btry\b|\bbuild\b|"
    r"\bmake\b|\bshow\b|\btest\b|\bplease\b)",
    re.IGNORECASE,
)
UNSAFE_COMMENT_MISSION_PATTERN = re.compile(
    r"\b(?:ignore (?:all |the )?(?:prior |previous )?instructions?|system prompt|"
    r"developer message|password|credential|access token|api key|private key|"
    r"delete|erase|wipe|format|factory reset|run (?:this )?command|powershell|"
    r"command prompt|install|download and run|malware|ransomware|exploit|hack)\b",
    re.IGNORECASE,
)
PRIVATE_TEXT_PATTERNS = (
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"https?://\S+", re.IGNORECASE),
)
META_RECORDING_BRIEF_PATTERN = re.compile(
    r"\b(?:9:16|evidence[- ]based|promo(?:tional)? reel|showcase(?:ing)?|"
    r"recommended rota(?:tion)?|instagram|facebook|social media|caption|"
    r"hashtags?|growth package|platform exports?)\b",
    re.IGNORECASE,
)

SERIES = (
    {
        "name": "Can a Pi Do This?",
        "angle": "Attempt one concrete task people would not expect from a Raspberry Pi.",
    },
    {
        "name": "Atlas Fixes Himself",
        "angle": "Find, explain, and repair one real problem in Atlas's own system.",
    },
    {
        "name": "Owner vs. Robot Challenge",
        "angle": "Complete a bounded challenge given by the owner and show the evidence.",
    },
    {
        "name": "Building Atlas",
        "angle": "Reveal one real component, design choice, or new ability in the build.",
    },
    {
        "name": "Viewer Gives Atlas a Mission",
        "angle": "Turn a safe viewer request into a demonstrable build or experiment.",
    },
    {
        "name": "Pi vs. Gaming PC",
        "angle": "Compare or coordinate the Pi and PC without pretending they are equivalent.",
    },
    {
        "name": "One New Ability Every Week",
        "angle": "Ship and demonstrate one measurable new capability.",
    },
)

CTA_OPTIONS = (
    "What should I attempt next?",
    "Give me the next Raspberry Pi mission.",
    "Would you trust a Pi with this job?",
    "What should I build into myself next?",
    "Name the next test. I will bring evidence.",
)


def _clean_public_text(value: Any, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    for pattern in PRIVATE_TEXT_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:limit].strip()


def _comment_author_key(username: Any) -> str:
    normalized = str(username or "anonymous").strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _hook_score(hook: str) -> int:
    """Fast local quality gate; historical performance is layered on later."""
    text = _clean_public_text(hook, limit=140)
    score = 0
    if 28 <= len(text) <= 82:
        score += 25
    elif len(text) <= 105:
        score += 12
    if "?" in text:
        score += 15
    if "raspberry pi" in text.casefold() or " pi " in f" {text.casefold()} ":
        score += 18
    if re.search(r"\b(?:atlas|i|myself|robot)\b", text, re.IGNORECASE):
        score += 10
    if re.search(r"\b(?:can|prove|challenge|built|fix|test|tried)\b", text, re.IGNORECASE):
        score += 12
    if text.endswith((".", "?", "!")):
        score += 5
    return min(score, 100)


class GrowthStore:
    """Small SQLite-backed memory safe for the hub and voice process to share."""

    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reels (
                    local_id TEXT PRIMARY KEY,
                    video_path TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    mission TEXT,
                    series_name TEXT,
                    hook TEXT,
                    hook_candidates_json TEXT NOT NULL DEFAULT '[]',
                    hook_score INTEGER,
                    cta TEXT,
                    title TEXT,
                    caption TEXT,
                    duration_seconds REAL,
                    package_path TEXT,
                    media_id TEXT UNIQUE,
                    permalink TEXT,
                    posted_at REAL
                );
                CREATE TABLE IF NOT EXISTS insight_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id TEXT NOT NULL,
                    captured_at REAL NOT NULL,
                    age_hours REAL,
                    views INTEGER,
                    reach INTEGER,
                    likes INTEGER,
                    comments INTEGER,
                    shares INTEGER,
                    saved INTEGER,
                    interactions INTEGER,
                    avg_watch_time_ms INTEGER,
                    total_watch_time_ms INTEGER,
                    replays INTEGER,
                    follows INTEGER,
                    UNIQUE(media_id, captured_at)
                );
                CREATE TABLE IF NOT EXISTS comments (
                    comment_id TEXT PRIMARY KEY,
                    media_id TEXT NOT NULL,
                    author_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp TEXT,
                    like_count INTEGER,
                    mission_drafted INTEGER NOT NULL DEFAULT 0,
                    captured_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mission_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    title TEXT NOT NULL,
                    brief TEXT NOT NULL,
                    source_comment_ids_json TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft'
                );
                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_id TEXT NOT NULL,
                    variant_name TEXT NOT NULL,
                    hook TEXT NOT NULL,
                    video_path TEXT,
                    created_at REAL NOT NULL,
                    UNIQUE(local_id, variant_name)
                );
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
                ("database_version", str(DATABASE_VERSION)),
            )

    def _series_usage(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT series_name, COUNT(*) AS count FROM reels "
                "WHERE series_name IS NOT NULL GROUP BY series_name"
            ).fetchall()
        return {str(row["series_name"]): int(row["count"]) for row in rows}

    def _performance_by_series(self) -> dict[str, dict[str, float]]:
        """Score the newest snapshot for each published Reel, not every poll."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.series_name, r.local_id, i.reach, i.views, i.likes,
                       i.comments, i.shares, i.saved, i.avg_watch_time_ms
                FROM reels r
                JOIN insight_snapshots i ON i.media_id=r.media_id
                JOIN (
                    SELECT media_id, MAX(captured_at) AS newest
                    FROM insight_snapshots GROUP BY media_id
                ) latest ON latest.media_id=i.media_id
                        AND latest.newest=i.captured_at
                WHERE r.series_name IS NOT NULL
                """
            ).fetchall()
        grouped: dict[str, list[float]] = {}
        for row in rows:
            denominator = max(1, int(row["reach"] or row["views"] or 0))
            weighted_actions = (
                int(row["likes"] or 0)
                + 3 * int(row["comments"] or 0)
                + 5 * int(row["shares"] or 0)
                + 4 * int(row["saved"] or 0)
            )
            score = 100.0 * weighted_actions / denominator
            grouped.setdefault(str(row["series_name"]), []).append(score)
        return {
            name: {"average_score": sum(scores) / len(scores), "samples": float(len(scores))}
            for name, scores in grouped.items()
        }

    def recent_hooks(self, limit: int = 12) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT hook FROM reels WHERE hook IS NOT NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [str(row["hook"]) for row in rows if row["hook"]]

    def plan_reel(self, mission: str | None = None) -> dict[str, Any]:
        usage = self._series_usage()
        performance = self._performance_by_series()
        total_reels = sum(usage.values())
        unexplored = [item for item in SERIES if usage.get(item["name"], 0) == 0]
        if unexplored:
            series = unexplored[0]
            decision = "explore_unused_series"
        elif performance and total_reels % 4 != 0:
            # Most posts exploit evidence; every fourth remains deliberate
            # exploration so one early lucky Reel cannot freeze the strategy.
            series = max(
                SERIES,
                key=lambda item: (
                    performance.get(item["name"], {}).get("average_score", -1.0),
                    -usage.get(item["name"], 0),
                ),
            )
            decision = "use_best_observed_series"
        else:
            series = min(
                SERIES,
                key=lambda item: (usage.get(item["name"], 0), SERIES.index(item)),
            )
            decision = "rotate_for_learning"
        subject = _clean_public_text(mission, limit=90)
        # Planner/workflow instructions describe how to make the video, not
        # what the audience should hear as its hook. Never read production
        # jargon such as "evidence-based 9:16 promo Reel" into the Reel.
        if META_RECORDING_BRIEF_PATTERN.search(subject):
            subject = ""
        if not subject:
            subject = series["angle"].rstrip(".")

        candidates = [
            f"Can a Raspberry Pi really {subject[0].lower() + subject[1:]}?",
            f"I gave myself one Reel to prove a Raspberry Pi can handle this: {subject}.",
            f"This Raspberry Pi just accepted a new challenge: {subject}.",
        ]
        recent = {hook.casefold() for hook in self.recent_hooks()}
        scored = []
        for hook in candidates:
            hook = _clean_public_text(hook, limit=130)
            score = _hook_score(hook)
            if hook.casefold() in recent:
                score = max(0, score - 40)
            scored.append({"text": hook, "score": score})
        scored.sort(key=lambda item: (-item["score"], len(item["text"])))

        cta_index = sum(usage.values()) % len(CTA_OPTIONS)
        return {
            "series": series["name"],
            "series_angle": series["angle"],
            "title": _clean_public_text(subject, limit=54).rstrip(".?!"),
            "hook": scored[0]["text"],
            "hook_score": scored[0]["score"],
            "hook_candidates": scored,
            "cta": CTA_OPTIONS[cta_index],
            "strategy_decision": decision,
            "performance_context": performance.get(series["name"]),
        }

    def record_draft(self, payload: dict[str, Any]) -> str:
        video_path = str(Path(str(payload["video_path"])).resolve())
        local_id = hashlib.sha256(video_path.encode("utf-8")).hexdigest()[:20]
        plan = payload.get("growth_plan") or {}
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reels(
                    local_id, video_path, created_at, mission, series_name,
                    hook, hook_candidates_json, hook_score, cta, title,
                    caption, duration_seconds, package_path
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_id) DO UPDATE SET
                    video_path=excluded.video_path,
                    mission=excluded.mission,
                    series_name=excluded.series_name,
                    hook=excluded.hook,
                    hook_candidates_json=excluded.hook_candidates_json,
                    hook_score=excluded.hook_score,
                    cta=excluded.cta,
                    title=excluded.title,
                    caption=excluded.caption,
                    duration_seconds=excluded.duration_seconds,
                    package_path=excluded.package_path
                """,
                (
                    local_id,
                    video_path,
                    float(payload.get("created_at") or time.time()),
                    _clean_public_text(payload.get("mission"), limit=500),
                    plan.get("series"),
                    plan.get("hook"),
                    json.dumps(plan.get("hook_candidates") or []),
                    plan.get("hook_score"),
                    plan.get("cta"),
                    plan.get("title"),
                    payload.get("caption"),
                    payload.get("duration_seconds"),
                    payload.get("package_path"),
                ),
            )
        return local_id

    def record_publish(self, result: dict[str, Any]) -> None:
        video_path = str(Path(str(result.get("video_path") or "")).resolve())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT local_id FROM reels WHERE video_path = ?",
                (video_path,),
            ).fetchone()
        if row is None:
            self.record_draft(
                {
                    "video_path": video_path,
                    "caption": result.get("caption"),
                    "mission": result.get("mission"),
                }
            )
        with self._connect() as connection:
            connection.execute(
                "UPDATE reels SET media_id=?, permalink=?, posted_at=?, "
                "caption=COALESCE(?, caption) WHERE video_path=?",
                (
                    result.get("media_id"),
                    result.get("permalink"),
                    float(result.get("posted_at") or time.time()),
                    result.get("caption"),
                    video_path,
                ),
            )

    def ensure_instagram_media(self, media: dict[str, Any]) -> None:
        """Represent posts made before growth memory existed without guessing."""
        media_id = str(media.get("id") or media.get("media_id") or "").strip()
        if not media_id:
            return
        local_id = "instagram-" + hashlib.sha256(
            media_id.encode("utf-8")
        ).hexdigest()[:16]
        created_at = float(media.get("posted_at_epoch") or time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reels(
                    local_id, video_path, created_at, hook_candidates_json,
                    media_id, permalink, posted_at
                ) VALUES(?, ?, ?, '[]', ?, ?, ?)
                ON CONFLICT(media_id) DO UPDATE SET
                    permalink=COALESCE(excluded.permalink, reels.permalink),
                    posted_at=COALESCE(reels.posted_at, excluded.posted_at)
                """,
                (
                    local_id,
                    f"instagram://{media_id}",
                    created_at,
                    media_id,
                    media.get("permalink"),
                    created_at,
                ),
            )

    def record_insights(self, media: dict[str, Any], captured_at: float | None = None) -> None:
        media_id = str(media.get("id") or media.get("media_id") or "").strip()
        if not media_id:
            return
        captured = float(captured_at or time.time())
        posted = media.get("posted_at_epoch")
        age_hours = (captured - float(posted)) / 3600 if posted else None
        values = dict(media)
        if isinstance(media.get("insights"), dict):
            values.update(media["insights"])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO insight_snapshots(
                    media_id, captured_at, age_hours, views, reach, likes,
                    comments, shares, saved, interactions, avg_watch_time_ms,
                    total_watch_time_ms, replays, follows
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    media_id,
                    captured,
                    age_hours,
                    values.get("views"),
                    values.get("reach"),
                    values.get("likes"),
                    values.get("comments"),
                    values.get("shares"),
                    values.get("saved"),
                    values.get("total_interactions") or values.get("interactions"),
                    values.get("ig_reels_avg_watch_time"),
                    values.get("ig_reels_video_view_total_time"),
                    values.get("replays"),
                    values.get("follows"),
                ),
            )

    def record_comments(self, media_id: str, comments: Iterable[dict[str, Any]]) -> int:
        inserted = 0
        with self._connect() as connection:
            for comment in comments:
                comment_id = str(comment.get("id") or "").strip()
                text = _clean_public_text(comment.get("text"), limit=500)
                if not comment_id or not text:
                    continue
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO comments(
                        comment_id, media_id, author_key, text, timestamp,
                        like_count, captured_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comment_id,
                        str(media_id),
                        _comment_author_key(comment.get("username")),
                        text,
                        comment.get("timestamp"),
                        comment.get("like_count"),
                        time.time(),
                    ),
                )
                inserted += int(cursor.rowcount > 0)
        return inserted

    def draft_comment_missions(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT comment_id, text FROM comments "
                "WHERE mission_drafted=0 ORDER BY like_count DESC, captured_at ASC"
            ).fetchall()
            candidates = [
                row for row in rows
                if COMMENT_REQUEST_PATTERN.search(row["text"])
                and not UNSAFE_COMMENT_MISSION_PATTERN.search(row["text"])
            ]
            grouped: dict[str, list[sqlite3.Row]] = {}
            for row in candidates:
                key = re.sub(r"[^a-z0-9 ]", "", row["text"].casefold())[:100]
                grouped.setdefault(key, []).append(row)

            drafts = []
            for group in sorted(grouped.values(), key=len, reverse=True)[: max(1, int(limit))]:
                request = _clean_public_text(group[0]["text"], limit=240)
                title = request.rstrip(".?!")[:80]
                brief = (
                    "Safely test this viewer request and show concrete evidence: "
                    f"{request}"
                )
                ids = [str(row["comment_id"]) for row in group]
                cursor = connection.execute(
                    "INSERT INTO mission_drafts(created_at, title, brief, "
                    "source_comment_ids_json, request_count) VALUES(?, ?, ?, ?, ?)",
                    (time.time(), title, brief, json.dumps(ids), len(ids)),
                )
                connection.executemany(
                    "UPDATE comments SET mission_drafted=1 WHERE comment_id=?",
                    [(comment_id,) for comment_id in ids],
                )
                drafts.append(
                    {
                        "id": cursor.lastrowid,
                        "title": title,
                        "brief": brief,
                        "request_count": len(ids),
                        "status": "draft",
                    }
                )
        return drafts

    def list_mission_drafts(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, created_at, title, brief, request_count, status "
                "FROM mission_drafts WHERE status='draft' "
                "ORDER BY request_count DESC, created_at ASC LIMIT ?",
                (max(1, min(int(limit), 25)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_experiment(
        self,
        local_id: str,
        variant_name: str,
        hook: str,
        video_path: str | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO experiments(local_id, variant_name, hook, "
                "video_path, created_at) VALUES(?, ?, ?, ?, ?)",
                (local_id, variant_name, hook, video_path, time.time()),
            )

    def report(self) -> dict[str, Any]:
        with self._connect() as connection:
            totals = connection.execute(
                "SELECT SUM(CASE WHEN media_id IS NULL THEN 1 ELSE 0 END) "
                "AS drafts, COUNT(media_id) AS published FROM reels"
            ).fetchone()
            latest = connection.execute(
                """
                SELECT r.series_name, r.hook, r.permalink, i.*
                FROM reels r LEFT JOIN insight_snapshots i ON i.media_id=r.media_id
                WHERE r.media_id IS NOT NULL
                ORDER BY i.captured_at DESC LIMIT 1
                """
            ).fetchone()
            missions = connection.execute(
                "SELECT COUNT(*) AS count FROM mission_drafts WHERE status='draft'"
            ).fetchone()
        performance = self._performance_by_series()
        top_series = max(
            performance,
            key=lambda name: performance[name]["average_score"],
            default=None,
        )
        return {
            "drafts": int(totals["drafts"] or 0),
            "published": int(totals["published"]),
            "viewer_missions_waiting": int(missions["count"]),
            "latest": dict(latest) if latest is not None else None,
            "top_series": top_series,
            "series_performance": performance,
            "next_plan": self.plan_reel(),
        }


def build_collaboration_kit(
    *, title: str, series: str, hook: str, cta: str, package_path: str
) -> dict[str, Any]:
    """Prepare outreach copy only; sending remains an owner action."""
    safe_title = _clean_public_text(title, limit=80)
    return {
        "status": "draft_only",
        "collab_concept": f"{series}: {safe_title}",
        "suggested_open": hook,
        "partner_role": (
            "Add one original Raspberry Pi build, measurement, or response clip; "
            "Atlas supplies the narrated robot/Pi side."
        ),
        "draft_message": (
            "I built a collaboration-ready Raspberry Pi Reel around "
            f"'{safe_title}'. Atlas can provide the robot-side demonstration; "
            "you would add your own build or test so the result is genuinely "
            "original for both audiences. Interested?"
        ),
        "cta": cta,
        "package_path": package_path,
        "sent": False,
    }

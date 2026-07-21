"""Instagram Reel publishing for A.T.L.A.S.'s self-showcase pipeline.

Companion to the read-only instagram_stats.py -- same config file, same
account, same API host -- but this module is the write side: turning a
finished local video file into a real Instagram post.

Instagram's content-publishing API for this account (graph.instagram.com,
the direct "Instagram API with Instagram Login" product) requires a
publicly reachable video_url to fetch the video from -- confirmed live:
`upload_type=resumable` is rejected outright ("The parameter video_url is
required"), so the byte-upload workaround this module originally used
doesn't apply here. This robot has no permanent public hosting and is
deliberately never port-forwarded (see PHONE_LINK.md / SETUP_GUIDE.md),
so instead this briefly serves just the one video file over Tailscale
Funnel (HTTPS via Tailscale's infrastructure, not a router port-forward)
for exactly as long as container creation + processing takes, then tears
the exposure back down -- see _funnel_video_url() below. Requires Funnel
to be enabled for the tailnet once in the Tailscale admin console, and
`tailscale set --operator=atlas` on the Pi so funnel commands don't need
an interactive sudo prompt.

publish_reel(..., dry_run=True) runs the container-creation and
processing steps (proving the token actually has publish permission and
that the video_url fetch works) but stops before the irreversible
media_publish call -- run that first before ever doing a real publish.
"""

from __future__ import annotations

import http.server
import json
import secrets
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import requests

from instagram_stats import CONFIG_PATH, _load_config

API_BASE = "https://graph.instagram.com/v24.0"
REQUEST_TIMEOUT_SECONDS = 30
STATUS_POLL_INTERVAL_SECONDS = 3
STATUS_POLL_MAX_ATTEMPTS = 40  # ~2 minutes
FUNNEL_STARTUP_TIMEOUT_SECONDS = 20

LEDGER_PATH = CONFIG_PATH.with_name("instagram_posts.json")

TERMINAL_ERROR_STATUSES = {"ERROR", "EXPIRED"}


class InstagramPublishError(RuntimeError):
    pass


def _config():
    config = _load_config()
    token = config.get("INSTAGRAM_ACCESS_TOKEN")
    account_id = config.get("INSTAGRAM_ACCOUNT_ID")

    if not token or not account_id:
        raise InstagramPublishError(
            "Instagram is not configured "
            f"(missing token/account id in {CONFIG_PATH})"
        )

    return token, account_id


class _SingleFileHandler(http.server.BaseHTTPRequestHandler):
    """Serves exactly one file at exactly one unguessable path, nothing
    else -- no directory listing, no other files reachable, even for the
    brief window this is exposed publicly.

    Supports HEAD and byte-range GETs, not just a plain 200 GET: video
    fetchers (Instagram's included, confirmed live -- a HEAD-less server
    here produced a bare, undiagnosable container ERROR) commonly probe
    with HEAD and/or fetch in ranges rather than a single whole-file GET."""

    video_path: Path
    url_path: str

    def _content_headers(self, file_size: int) -> None:
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_size))

    def do_HEAD(self):  # noqa: N802 -- required override name
        if self.path != self.url_path:
            self.send_error(404)
            return

        self.send_response(200)
        self._content_headers(self.video_path.stat().st_size)
        self.end_headers()

    def do_GET(self):  # noqa: N802 -- required override name
        if self.path != self.url_path:
            self.send_error(404)
            return

        file_size = self.video_path.stat().st_size
        byte_range = self._parse_range(file_size)

        if byte_range is None:
            self.send_response(200)
            self._content_headers(file_size)
            self.end_headers()
            with self.video_path.open("rb") as source:
                self.wfile.write(source.read())
            return

        start, end = byte_range
        self.send_response(206)
        self._content_headers(end - start + 1)
        self.send_header(
            "Content-Range", f"bytes {start}-{end}/{file_size}"
        )
        self.end_headers()
        with self.video_path.open("rb") as source:
            source.seek(start)
            self.wfile.write(source.read(end - start + 1))

    def _parse_range(self, file_size: int) -> tuple[int, int] | None:
        header = self.headers.get("Range")

        if not header or not header.startswith("bytes="):
            return None

        try:
            start_text, end_text = header[len("bytes="):].split("-", 1)
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else file_size - 1
        except ValueError:
            return None

        return max(0, start), min(end, file_size - 1)

    def log_message(self, format, *args):  # noqa: A002 -- stdlib signature
        pass  # keep this quiet; nothing here is worth logging


@contextmanager
def _serve_file_locally(video_path: Path):
    """Starts a loopback-only HTTP server for one file on an ephemeral
    port, in a background thread. Yields (port, url_path)."""
    token = secrets.token_urlsafe(24)
    url_path = f"/{token}/{video_path.name}"

    handler = type(
        "_ScopedHandler",
        (_SingleFileHandler,),
        {"video_path": video_path, "url_path": url_path},
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield server.server_port, url_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _own_tailnet_dns_name() -> str:
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        dns_name = json.loads(result.stdout)["Self"]["DNSName"]
    except (
        subprocess.SubprocessError, OSError,
        json.JSONDecodeError, KeyError,
    ) as error:
        raise InstagramPublishError(
            f"could not determine this device's Tailscale DNS name: {error}"
        ) from error

    return dns_name.rstrip(".")


@contextmanager
def _funnel_enabled(port: int):
    """Turns Tailscale Funnel on for exactly this port, for exactly the
    lifetime of this context manager, and always tears it back down --
    even on error. Requires Funnel enabled for the tailnet (one-time
    admin-console approval) and operator permission on this device."""
    try:
        subprocess.run(
            ["tailscale", "funnel", "--bg", str(port)],
            capture_output=True, text=True, check=True,
            timeout=FUNNEL_STARTUP_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as error:
        raise InstagramPublishError(
            "tailscale funnel failed to start "
            f"(is Funnel enabled for this tailnet?): {error.stderr.strip()}"
        ) from error
    except (subprocess.SubprocessError, OSError) as error:
        raise InstagramPublishError(
            f"tailscale funnel failed to start: {error}"
        ) from error

    try:
        yield
    finally:
        subprocess.run(
            ["tailscale", "funnel", "reset"],
            capture_output=True, text=True, timeout=15,
        )


@contextmanager
def _funnel_video_url(video_path: Path):
    """Combines the local file server and Funnel into one guaranteed-
    torn-down public HTTPS URL for the video, for use as Instagram's
    video_url. Total public exposure is bounded to this context's
    lifetime -- container creation plus status polling, nothing else."""
    with _serve_file_locally(video_path) as (port, url_path):
        with _funnel_enabled(port):
            dns_name = _own_tailnet_dns_name()
            yield f"https://{dns_name}{url_path}"


def create_container_with_video_url(
    video_url: str, caption: str
) -> str:
    """Creates a REELS container pointing at a public video_url. Returns
    the container id. Missing content_publish permission surfaces here
    as a clear error, not a crash."""
    token, account_id = _config()

    try:
        response = requests.post(
            f"{API_BASE}/{account_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "access_token": token,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise InstagramPublishError(
            f"container creation request failed: {error}"
        ) from error

    payload = _json_or_error(response, "container creation")
    container_id = payload.get("id")

    if not container_id:
        raise InstagramPublishError(
            f"container creation did not return an id: {payload}"
        )

    return container_id


def poll_container_status(container_id: str) -> str:
    """Polls until the container reaches FINISHED, or raises on ERROR/
    EXPIRED/timeout. Returns the final status_code."""
    token, _account_id = _config()

    for _attempt in range(STATUS_POLL_MAX_ATTEMPTS):
        try:
            response = requests.get(
                f"{API_BASE}/{container_id}",
                params={
                    "fields": "status_code",
                    "access_token": token,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise InstagramPublishError(
                f"status check failed: {error}"
            ) from error

        payload = _json_or_error(response, "status check")
        status = payload.get("status_code")

        if status == "FINISHED":
            return status

        if status in TERMINAL_ERROR_STATUSES:
            raise InstagramPublishError(
                f"container processing failed with status {status}"
            )

        time.sleep(STATUS_POLL_INTERVAL_SECONDS)

    raise InstagramPublishError(
        "container never reached FINISHED after "
        f"{STATUS_POLL_MAX_ATTEMPTS * STATUS_POLL_INTERVAL_SECONDS}s"
    )


def publish(container_id: str) -> str:
    """The one irreversible call: POST media_publish. Returns media_id."""
    token, account_id = _config()

    try:
        response = requests.post(
            f"{API_BASE}/{account_id}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": token,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise InstagramPublishError(
            f"publish request failed: {error}"
        ) from error

    payload = _json_or_error(response, "publish")
    media_id = payload.get("id")

    if not media_id:
        raise InstagramPublishError(
            f"publish did not return a media id: {payload}"
        )

    return media_id


def verify(media_id: str) -> dict:
    """Confirms the post is real rather than trusting media_publish's
    response alone -- fetches the actual permalink and timestamp."""
    token, _account_id = _config()

    try:
        response = requests.get(
            f"{API_BASE}/{media_id}",
            params={
                "fields": "permalink,timestamp,media_type",
                "access_token": token,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise InstagramPublishError(
            f"verification request failed: {error}"
        ) from error

    return _json_or_error(response, "verification")


def _record_ledger_entry(entry: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        existing = json.loads(LEDGER_PATH.read_text())
        if not isinstance(existing, list):
            existing = []
    except (OSError, json.JSONDecodeError):
        existing = []

    existing.append(entry)

    temporary_path = LEDGER_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(existing, indent=2))
    temporary_path.replace(LEDGER_PATH)


def publish_reel(
    video_path,
    caption: str,
    *,
    dry_run: bool = False,
    mission: str | None = None,
) -> dict:
    """Orchestrates: briefly serve the video over Tailscale Funnel ->
    create a REELS container pointing at that URL -> poll until
    Instagram has finished fetching/processing it -> tear the public
    exposure back down (always, even on error) -> (only if not dry_run)
    publish -> verify. dry_run=True proves the token's publish
    permission and the video_url fetch without ever posting -- run it
    before the first real publish_reel(dry_run=False) call."""
    path = Path(video_path)

    if not path.is_file():
        raise InstagramPublishError(f"video file not found: {path}")

    with _funnel_video_url(path) as video_url:
        container_id = create_container_with_video_url(
            video_url, caption
        )
        status = poll_container_status(container_id)

    if dry_run:
        return {
            "dry_run": True,
            "container_id": container_id,
            "status": status,
        }

    media_id = publish(container_id)
    details = verify(media_id)

    entry = {
        "media_id": media_id,
        "permalink": details.get("permalink"),
        "timestamp": details.get("timestamp"),
        "caption": caption,
        "mission": mission,
        "posted_at": time.time(),
    }
    _record_ledger_entry(entry)

    return {"dry_run": False, **entry}


def _json_or_error(response: requests.Response, step: str) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if isinstance(payload, dict) and payload.get("error"):
        error = payload["error"]
        message = (
            error.get("message")
            if isinstance(error, dict)
            else str(error)
        )
        raise InstagramPublishError(f"{step} failed: {message}")

    if response.status_code >= 400:
        raise InstagramPublishError(
            f"{step} failed with HTTP {response.status_code}: "
            f"{payload or response.text}"
        )

    return payload if isinstance(payload, dict) else {}

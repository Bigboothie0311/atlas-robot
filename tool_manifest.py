"""Approved-tool manifest for safe, gated self-upgrades.

A.T.L.A.S. may CHECK the health/version of the approved local tools it
depends on and PROPOSE an upgrade — but it never installs anything on its
own. A proposal creates a rollback point, states the exact command it
would run and how to undo it, and waits for explicit approval.

This is the tool-dependency counterpart to self_upgrade.py (which handles
its own code changes). Neither performs autonomous installs.
"""
import subprocess
import time
from pathlib import Path

REPO = Path("/home/atlas/atlas-robot")
VENV_PIP = str(REPO / "venv" / "bin" / "pip")

# Every tool ATLAS is allowed to reason about. check = how to read the
# installed version; upgrade = the exact command a proposal would run
# (only after approval); undo = how to revert.
APPROVED_TOOLS = {
    "whisper.cpp": {
        "check": ["git", "-C", str(REPO / "tools" / "whisper.cpp"), "rev-parse", "--short", "HEAD"],
        "upgrade": ["git", "-C", str(REPO / "tools" / "whisper.cpp"), "pull"],
        "undo": "git -C tools/whisper.cpp reset --hard <previous-sha>",
        "kind": "git",
    },
    "opencv": {
        "check": [VENV_PIP, "show", "opencv-contrib-python-headless"],
        "upgrade": [VENV_PIP, "install", "--upgrade", "opencv-contrib-python-headless"],
        "undo": "venv/bin/pip install opencv-contrib-python-headless==<previous-version>",
        "kind": "pip",
    },
    "piper": {
        "check": [VENV_PIP, "show", "piper-tts"],
        "upgrade": [VENV_PIP, "install", "--upgrade", "piper-tts"],
        "undo": "venv/bin/pip install piper-tts==<previous-version>",
        "kind": "pip",
    },
    "vosk": {
        "check": [VENV_PIP, "show", "vosk"],
        "upgrade": [VENV_PIP, "install", "--upgrade", "vosk"],
        "undo": "venv/bin/pip install vosk==<previous-version>",
        "kind": "pip",
    },
}

ROLLBACK_DIR = REPO / "data" / "tool_rollbacks"


def _current_version(tool):
    spec = APPROVED_TOOLS[tool]
    try:
        out = subprocess.run(spec["check"], capture_output=True, text=True, timeout=15).stdout
    except (subprocess.SubprocessError, OSError):
        return None

    if spec["kind"] == "pip":
        for line in out.splitlines():
            if line.lower().startswith("version:"):
                return line.split(":", 1)[1].strip()
        return None
    return out.strip() or None


def check_all():
    """Installed version of each approved tool."""
    return {name: _current_version(name) for name in APPROVED_TOOLS}


def spoken_status():
    versions = check_all()
    known = [f"{name} at {v}" for name, v in versions.items() if v]
    missing = [name for name, v in versions.items() if not v]

    parts = [f"I track {len(APPROVED_TOOLS)} approved tools"]
    if known:
        parts.append("currently: " + ", ".join(known))
    if missing:
        parts.append("not detected: " + ", ".join(missing))
    parts.append("Say 'upgrade' plus a tool name and I'll propose it — I never install on my own")
    return ". ".join(parts) + "."


def propose(tool):
    """Records a rollback point and returns a spoken proposal WITHOUT
    installing. Approval is a separate explicit step."""
    if tool not in APPROVED_TOOLS:
        return f"{tool} isn't on my approved-tools list, so I won't touch it."

    spec = APPROVED_TOOLS[tool]
    version = _current_version(tool)

    ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (ROLLBACK_DIR / f"{tool}-{stamp}.txt").write_text(
        f"tool: {tool}\ncurrent_version: {version}\nundo: {spec['undo']}\n"
    )

    command = " ".join(spec["upgrade"])
    return (
        f"Here's the plan for {tool}. It's currently {version}. I would "
        f"run: {command}. I've saved a rollback point, and to undo it you'd "
        f"run: {spec['undo'].replace('<previous-version>', version or '<previous-version>')}. "
        "I won't run the upgrade until you explicitly approve it. Approve?"
    )

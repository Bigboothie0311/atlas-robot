"""On-demand tool acquisition for A.T.L.A.S.

When the owner asks for something that needs a Python package A.T.L.A.S.
doesn't have yet, it can offer to install it — but only from a curated
catalog of vetted packages, only after a spoken "yes", and only after a
due-diligence check against PyPI (the package exists, the resolved name
matches exactly so a typo-squat can't slip in, and the latest release
isn't yanked). After a successful install it reports where the package
landed and refreshes the graphify knowledge graph so the new capability
is on the map.

This is the *acquire a new capability* counterpart to tool_manifest.py,
which only upgrades tools that are already approved and installed. As with
that module, nothing here installs autonomously — the confirmation and
the mic reply live in listen_and_answer.py so this module stays pure and
testable (no audio, no globals).
"""
import importlib.util
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path("/home/atlas/atlas-robot")
VENV_PIP = str(REPO / "venv" / "bin" / "pip")

PYPI_JSON = "https://pypi.org/pypi/{package}/json"
_HTTP_TIMEOUT = 15
_INSTALL_TIMEOUT = 300  # pip can be slow on a Pi

# The only packages A.T.L.A.S. is allowed to acquire on demand. Each entry
# maps a spoken need to a single vetted PyPI package.
#   package  = exact PyPI distribution name (verified against PyPI on install)
#   import   = the module name used to tell whether it's already installed
#   keywords = normalized substrings that mean "the question needs this tool"
#   desc     = short spoken description of what it unlocks
# Add new rows here; keep packages well-known and keywords specific so an
# offer never collides with a command that's already handled elsewhere.
INSTALLABLE_TOOLS = {
    "qrcode": {
        "package": "qrcode",
        "import": "qrcode",
        "keywords": ["qr code", "qr-code", "make a qr", "generate a qr", "create a qr"],
        "desc": "generate QR codes",
    },
    "barcode": {
        "package": "python-barcode",
        "import": "barcode",
        "keywords": ["barcode", "bar code"],
        "desc": "generate barcodes",
    },
    "translate": {
        "package": "deep-translator",
        "import": "deep_translator",
        "keywords": ["translate", "translation", "how do you say", "in spanish",
                     "in french", "in german", "in japanese"],
        "desc": "translate text between languages",
    },
}


def _canon(name):
    """PyPI treats '-' and '_' as equivalent and is case-insensitive."""
    return name.lower().replace("_", "-")


def is_installed(tool):
    """True if the tool's import module is importable in this environment."""
    spec = INSTALLABLE_TOOLS.get(tool)
    if spec is None:
        return False
    try:
        return importlib.util.find_spec(spec["import"]) is not None
    except (ImportError, ValueError):
        return False


def describe(tool):
    spec = INSTALLABLE_TOOLS.get(tool)
    return spec["desc"] if spec else ""


def find_missing_tool_for_request(text):
    """Return the catalog name of a tool the request needs but that isn't
    installed yet, or None. Already-installed tools never trigger an offer."""
    if not text:
        return None
    normalized = text.lower()
    for tool, spec in INSTALLABLE_TOOLS.items():
        if any(keyword in normalized for keyword in spec["keywords"]):
            if not is_installed(tool):
                return tool
            return None  # needed, but already have it — nothing to offer
    return None


def _fetch_pypi(package):
    url = PYPI_JSON.format(package=package)
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 (https, fixed host)
        return json.loads(resp.read().decode("utf-8"))


def due_diligence(tool):
    """Verify the package before touching pip. Checks that PyPI knows it,
    that the canonical name matches exactly (anti typo-squat), and that the
    latest release isn't fully yanked. Returns
    {ok: bool, version: str|None, reason: str}."""
    spec = INSTALLABLE_TOOLS.get(tool)
    if spec is None:
        return {"ok": False, "version": None,
                "reason": f"{tool} isn't on my approved-tools list, so I won't touch it."}

    package = spec["package"]
    try:
        data = _fetch_pypi(package)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"ok": False, "version": None,
                    "reason": f"I couldn't find {package} on PyPI, so I won't install it."}
        return {"ok": False, "version": None,
                "reason": f"PyPI returned an error for {package}, so I'll hold off."}
    except (urllib.error.URLError, OSError, ValueError):
        return {"ok": False, "version": None,
                "reason": f"I couldn't reach PyPI to vet {package}, so I won't install it."}

    info = data.get("info", {})
    resolved = info.get("name", "")
    if _canon(resolved) != _canon(package):
        return {"ok": False, "version": None,
                "reason": (f"The package on PyPI is called {resolved}, not {package} — "
                           "that name mismatch is a red flag, so I won't install it.")}

    version = info.get("version")
    files = data.get("releases", {}).get(version, [])
    if files and all(f.get("yanked") for f in files):
        return {"ok": False, "version": version,
                "reason": f"The latest {package} release was yanked, so I won't install it."}

    return {"ok": True, "version": version,
            "reason": f"{package} checks out on PyPI at version {version}."}


def install(tool):
    """Install the package into the venv. Assumes due_diligence already
    passed. Returns {ok: bool, message: str}."""
    spec = INSTALLABLE_TOOLS.get(tool)
    if spec is None:
        return {"ok": False, "message": f"{tool} isn't on my approved-tools list."}

    try:
        result = subprocess.run(
            [VENV_PIP, "install", spec["package"]],
            capture_output=True, text=True, timeout=_INSTALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"The install of {spec['package']} timed out."}
    except (subprocess.SubprocessError, OSError) as exc:
        return {"ok": False, "message": f"I couldn't run the installer: {exc}."}

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else "no output"
        return {"ok": False, "message": f"The install failed: {detail}"}

    return {"ok": True, "message": f"{spec['package']} installed."}


def where_is(tool):
    """Filesystem location of the installed module, so A.T.L.A.S. can
    'show himself where it is'. Returns a path string or None."""
    spec = INSTALLABLE_TOOLS.get(tool)
    if spec is None:
        return None
    try:
        found = importlib.util.find_spec(spec["import"])
    except (ImportError, ValueError):
        return None
    if found is None:
        return None
    if found.origin and found.origin != "namespace":
        return str(Path(found.origin).parent)
    if found.submodule_search_locations:
        return str(list(found.submodule_search_locations)[0])
    return None


def update_graph():
    """Refresh the graphify knowledge graph so the new tool is on the map.
    Best-effort — a failure here never blocks the install."""
    try:
        result = subprocess.run(
            ["graphify", "update", "."],
            cwd=str(REPO), capture_output=True, text=True, timeout=180,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False

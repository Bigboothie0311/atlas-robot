"""Conservative local repair layer for speech-to-text transcripts."""
import json
import re
import time
from pathlib import Path

DATA_DIR = Path("/home/atlas/atlas-robot/data")
ALIASES_PATH = DATA_DIR / "speech_aliases.json"
HISTORY_PATH = DATA_DIR / "speech_history.json"

DEFAULT_REPAIRS = {
    "spot of i": "spotify",
    "spot if i": "spotify",
    "spot a fi": "spotify",
    "spotifi": "spotify",
    "fusion three sixty": "fusion 360",
    "fusion three six zero": "fusion 360",
    "ad five x": "ad5x",
    "ad five ex": "ad5x",
    "recycling bin": "recycle bin",
    "wake on land": "wake on lan",
}

def normalize(text):
    text = str(text or "").lower().strip()
    text = re.sub(r"[,.?!;:]", " ", text)
    return " ".join(text.split())

def _load(path, fallback):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return fallback

def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True))
    temporary.replace(path)

def aliases():
    data = _load(ALIASES_PATH, {})
    return data if isinstance(data, dict) else {}

def teach(alias, target):
    alias, target = normalize(alias), normalize(target)
    if not alias or not target:
        return False
    data = aliases()
    data[alias] = target
    _save(ALIASES_PATH, data)
    return True

def repair(text):
    repaired = normalize(text)
    applied = []
    for original, replacement in sorted(
        {**DEFAULT_REPAIRS, **aliases()}.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        pattern = r"(?<!\w)" + re.escape(normalize(original)) + r"(?!\w)"
        if re.search(pattern, repaired):
            repaired = re.sub(pattern, normalize(replacement), repaired)
            applied.append({"from": normalize(original), "to": normalize(replacement)})
    return repaired, applied

def record(raw, repaired, corrections, alternatives):
    history = _load(HISTORY_PATH, [])
    if not isinstance(history, list):
        history = []
    history.append({
        "ts": time.time(),
        "raw": normalize(raw),
        "repaired": normalize(repaired),
        "corrections": corrections or [],
        "alternatives": alternatives or [],
    })
    _save(HISTORY_PATH, history[-12:])

def previous_report():
    history = _load(HISTORY_PATH, [])
    if len(history) < 2:
        return "I don't have a previous speech transcript yet."
    previous = history[-2]
    raw = previous.get("raw") or "nothing clear"
    repaired = previous.get("repaired") or raw
    corrections = previous.get("corrections") or []
    if corrections:
        changes = ", ".join(f"{x['from']} as {x['to']}" for x in corrections)
        return f"Last time I heard: {raw}. I corrected {changes}, then used: {repaired}."
    return f"Last time I heard: {raw}. I used: {repaired}."

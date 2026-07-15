# A.T.L.A.S. — build your own voice-controlled desk robot

A.T.L.A.S. is a Raspberry Pi desk assistant that listens for a wake word,
answers questions out loud using OpenAI, looks through a camera when asked,
and tracks a simple "face expression" state you can render however you like.
Everything here was written by the project owner working with Claude Code —
no code was copied from another project.

## What it does

- **Wake word detection** (`wake_listener.py`) — listens continuously on a
  microphone using [Vosk](https://alphacephei.com/vosk/) (fully offline,
  no cloud calls) for the phrase "hey atlas", with confidence/volume/repeat
  thresholds tuned to reject false positives and its own speaker echo.
- **Voice Q&A** (`listen_and_answer.py`) — records your question, transcribes
  it locally with Vosk, and (for anything that isn't a local command) sends
  it to the OpenAI Responses API for an answer, spoken back through
  [Piper](https://github.com/OHF-voice/piper1-gpl) TTS. A small tool-calling
  layer (`ai_tools.py`) lets the model check live weather via Open-Meteo and
  do web search.
- **Camera vision** (`vision_test.py`) — on request ("what do you see?"),
  captures a frame from the Pi camera and asks OpenAI to describe it.
- **Face/speech hub** (`robot_hub.py`) — a small Flask service exposing
  `/face`, `/state`, and `/speak`. It tracks an "expression" string
  (`happy`, `listening`, `thinking`, `talking`, etc.) and does the actual
  text-to-speech playback. **This repo does not include a face renderer** —
  poll `/state` from whatever display you build (small HDMI/SPI screen, a
  browser tab, an LED matrix) and show an expression for the current state.
- **Cost control** — every OpenAI call is metered against a monthly USD
  budget (`MONTHLY_LIMIT_USD` in `listen_and_answer.py`) tracked in
  `data/openai_usage.json`, so a bug or bad Wi-Fi day can't run up a
  surprise bill. Local wake-word/transcription/local-command features
  keep working even if the budget is hit.
- **Optional local-command routing** — before anything is sent to OpenAI,
  `handle_local_command()` intercepts printer-control phrases ("printer
  status", "pause the printer", etc.) and forwards them to an external HTTP
  hub of your own. See [Optional: 3D printer integration](#optional-3d-printer-integration)
  below — you do not need this to use the rest of the project.

## Hardware used in the reference build

- Raspberry Pi 5 (should work on any Pi capable of running Python 3.11+
  and a camera; adjust audio device names for your hardware)
- USB microphone (any class-compliant USB mic works — see
  [Finding your audio device names](#finding-your-audio-device-names))
- I2S amplifier/speaker (reference build uses a MAX98357A I2S DAC/amp
  board); a USB speaker or HDMI audio also works with a device name change
- Raspberry Pi Camera Module (OV5647/CSI, via `picamera2`)
- A face display of your choice — not included, see above

## Repo layout

```
robot_hub.py       Flask hub: face state + Piper text-to-speech
wake_listener.py   Always-on wake-word listener
listen_and_answer.py  Records/transcribes a question, calls OpenAI, speaks the answer
ai_tools.py        Tool functions the model can call (weather, web search)
vision_test.py     Camera capture + image description
config/            Env-style config files (see setup below)
systemd/           Unit file templates for running on boot
```

## Setup

### 1. System packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip alsa-utils python3-picamera2 --no-install-recommends
```

`picamera2` is tightly coupled to Raspberry Pi OS's `libcamera` stack and
should come from `apt`, not `pip`.

### 2. Python environment

```bash
cd atlas-robot
python3 -m venv venv --system-site-packages   # --system-site-packages picks up apt's picamera2
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Download the models (not included in this repo — large binaries)

**Vosk speech recognition model** (small English model, ~40MB):

```bash
mkdir -p models
cd models
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip
cd ..
```

**Piper TTS voice** (pick any voice from the
[Piper voice list](https://github.com/OHF-voice/piper1-gpl); the reference
build uses `en_US-ryan-medium`):

```bash
mkdir -p voices
cd voices
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json
cd ..
```

If you pick a different voice, update `PIPER_MODEL` in `robot_hub.py`.

### 4. Configure your API key and name

```bash
cp config/openai.env.example config/openai.env
cp config/robot.env.example config/robot.env
```

Edit `config/openai.env` and paste in your
[OpenAI API key](https://platform.openai.com/api-keys). Edit
`config/robot.env` and set `OWNER_NAME` to whatever you'd like the assistant
to call you. Both files are gitignored — they'll never accidentally get
committed.

Also check `MODEL_NAME` near the top of `listen_and_answer.py` — set it to
whatever OpenAI model you have access to and want to pay for.

### 5. Finding your audio device names

List your devices and update the constants at the top of each file:

```bash
arecord -l   # microphone -> MIC_DEVICE in wake_listener.py and listen_and_answer.py
aplay -l     # speaker    -> the "-D" device in robot_hub.py's aplay call
```

Device strings look like `plughw:CARD=<name>,DEV=0`.

### 6. Try it manually before installing services

```bash
# Terminal 1
python robot_hub.py

# Terminal 2
python wake_listener.py
```

Say "hey atlas", wait for the listening cue, then ask a question.

### 7. Run on boot (optional)

Unit file templates are in `systemd/`. Replace `YOUR_USERNAME` in both files
with your Linux username first, then:

```bash
sudo cp systemd/atlas-robot.service systemd/atlas-wake.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now atlas-robot.service
sudo systemctl enable --now atlas-wake.service
```

Logs: `journalctl -u atlas-robot -f` / `journalctl -u atlas-wake -f`.

## Tuning the wake word

`wake_listener.py` has four thresholds at the top worth adjusting for your
mic and room:

- `MIN_WORD_CONFIDENCE` — how confident Vosk must be in "hey"/"atlas"
- `MIN_UTTERANCE_RMS` — minimum loudness, filters out distant background speech
- `MIN_PARTIAL_HITS` — how many partial-result frames must match before a
  final result is trusted, filters out one-off misrecognitions
- `SPEAKER_COOLDOWN_SECONDS` — mutes the mic briefly after the robot itself
  stops talking, so it can't wake itself up on its own voice

Watch the console output — it prints every wake candidate with its
confidence, peak volume, and partial-hit count, which makes it easy to see
why a phrase was accepted or rejected and adjust from there.

## Optional: 3D printer integration

The reference build also talks to a separate 3D-printer control service over
plain HTTP, but that service isn't part of this repo (it's specific to one
printer model). If you want to wire up your own, `listen_and_answer.py`
expects a hub at `ATLAS_HUB` (default `http://127.0.0.1:5050`) with:

- `GET /atlas?cmd=printer_status` → text containing `AD5X ONLINE`/`OFFLINE`,
  optionally `STATE:`, `PROGRESS <current>/<total>`, `Layer: N`, `NOZ x/y BED a/b`
- `GET /atlas?cmd=printer_pause` → text containing `PRINTER PAUSE SENT` or `DISABLED`
- `GET /atlas?cmd=printer_resume` → text containing `PRINTER RESUME SENT` or `DISABLED`
- `GET /atlas?cmd=printer_cancel` → text containing `PRINTER CANCEL SENT` or `DISABLED`

If you don't have a matching hub running, printer-related phrases will just
get an "I could not..." spoken response — nothing else breaks.

If you don't want this feature at all, delete `handle_local_command()`'s
printer branch and its helpers (`confirm_printer_cancel`,
`summarize_printer_status`, `call_atlas_command`) from
`listen_and_answer.py`.

## License

MIT — see [LICENSE](LICENSE). Do whatever you want with it.

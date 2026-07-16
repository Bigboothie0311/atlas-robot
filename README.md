# A.T.L.A.S. — build your own voice-controlled desk robot

A.T.L.A.S. is a Raspberry Pi desk assistant that listens for a wake word,
answers questions out loud using OpenAI, looks through a camera when asked,
and tracks a simple "face expression" state you can render however you like.
all work snd ideas are my Own vibe coded with claude.

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

## Bill of materials

| Part | Notes |
|---|---|
| Raspberry Pi 5 (4GB or 8GB) | Reference build. A Pi 4 also works — see notes below. |
| microSD card, 32GB+ (A2 rated recommended) | For Raspberry Pi OS |
| USB-C power supply, 5V/5A (27W) | The **official Raspberry Pi 5 PSU** is strongly recommended — underpowered supplies cause random USB/camera glitches |
| Raspberry Pi Camera Module (any CSI camera, e.g. OV5647 or the official Camera Module 3) | Connects to the Pi's CSI port with a ribbon cable |
| USB microphone | Any class-compliant USB mic/USB sound-card-plus-mic combo works |
| MAX98357A I2S mono amplifier breakout board | e.g. Adafruit #3006 or any generic MAX98357A board |
| Small 4Ω or 8Ω speaker, 3W (matched to your amp board) | Solder or JST-connect to the amp board's speaker output |
| Jumper wires (female-female, 4+) | For amp board ↔ Pi GPIO header |
| Enclosure of your choice | Not covered here — any box/3D-printed case that fits a Pi, camera, mic, and speaker works. If you want a face expression display, you'll need to add your own small screen and write a renderer that polls `robot_hub.py`'s `/state` endpoint — that part isn't included in this repo. |

A Pi 4 will run everything here, but you'll need `python3-picamera2` compiled
for your OS release and slightly lower expectations for Vosk/Piper latency —
the reference build uses a Pi 5.

## Assembly

### 1. Camera

Connect the CSI ribbon cable to the Pi's camera port (blue side of the
cable faces the Ethernet port on a Pi 5) and to the camera module's
connector. Do this with the Pi powered off.

### 2. MAX98357A amplifier → speaker

Wire the speaker to the amp board's speaker output terminals (polarity
doesn't matter for a simple 2-wire speaker). Then wire the amp board to the
Pi's 40-pin GPIO header:

| MAX98357A pin | Pi 40-pin header pin | Purpose |
|---|---|---|
| VIN | Pin 2 or 4 (5V) | Power |
| GND | Pin 6 (or any GND pin) | Ground |
| BCLK | Pin 12 (GPIO18) | I2S bit clock |
| LRC | Pin 35 (GPIO19) | I2S word select |
| DIN | Pin 40 (GPIO21) | I2S data |
| GAIN | Leave unconnected | Unconnected = 9dB gain (a common default) |
| SD | Leave unconnected | The config below uses "no shutdown pin" mode |

Do this with the Pi powered off as well.

### 3. Microphone

Just plug the USB microphone into any USB port after first boot — no
wiring needed.

## Flash and configure Raspberry Pi OS

1. Flash **Raspberry Pi OS (64-bit)** to the microSD card using
   [Raspberry Pi Imager](https://www.raspberrypi.com/software/). In the
   Imager's advanced options (gear icon / Ctrl+Shift+X) you can pre-set a
   hostname, enable SSH, and set your Wi-Fi credentials so it boots headless.
2. Boot the Pi, log in (via SSH or a monitor+keyboard), and update it:
   ```bash
   sudo apt update && sudo apt full-upgrade -y
   ```
3. Enable the camera and I2S audio. Edit the boot config:
   ```bash
   sudo nano /boot/firmware/config.txt
   ```
   Make sure these lines are present (camera is usually auto-detected
   already; add the `dtoverlay` line for the amp board):
   ```
   camera_auto_detect=1
   dtoverlay=max98357a,no-sdmode
   ```
4. Reboot: `sudo reboot`
5. After reboot, confirm the camera is detected:
   ```bash
   libcamera-hello --list-cameras
   ```
   and confirm the amp shows up as a playback device:
   ```bash
   aplay -l
   ```
   You should see an entry like `MAX98357A`. Note its card number — you'll
   need it for `robot_hub.py`'s speaker device string.
6. Plug in the USB microphone and confirm it's detected:
   ```bash
   arecord -l
   ```
   Note its card name — you'll need it for `MIC_DEVICE` in the code.

## Repo layout

```
robot_hub.py       Flask hub: assistant state, Piper text-to-speech, HUD routes
hud_stats.py        Live weather/CPU/disk/network/uptime data for the HUD
hud/                J.A.R.V.I.S. HUD frontend (served by robot_hub.py, shown via Chromium kiosk)
wake_listener.py   Always-on wake-word listener
listen_and_answer.py  Records/transcribes a question, calls OpenAI, speaks the answer
ai_tools.py        Tool functions the model can call (weather, web search)
web_search.py       Text/image web search (DuckDuckGo, no API key)
vision_test.py     Camera capture + image description
config/            Env-style config files (see setup below)
systemd/           Unit file templates for running on boot
```

## Setup

### 1. System packages

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip alsa-utils python3-picamera2 chromium --no-install-recommends
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

### 4. Get an OpenAI API key

The voice Q&A and camera-description features call the OpenAI API, which is
separate from a ChatGPT subscription and billed per request (this project's
built-in budget cap, described above, keeps that predictable).

1. Go to [platform.openai.com](https://platform.openai.com/) and sign up or
   log in.
2. Add a payment method: **Settings → Billing → Payment methods**, and add
   at least a small starting credit. The API will not respond without
   billing set up, even for very small requests.
3. (Recommended) While you're in Billing, set a **usage limit** — a monthly
   dollar cap after which OpenAI itself stops serving requests. This is a
   second safety net on top of this project's own local budget tracker.
4. Create a key: go to
   [platform.openai.com/api-keys](https://platform.openai.com/api-keys),
   click **Create new secret key**, name it (e.g. "atlas-robot"), and copy
   it immediately — OpenAI only shows it once.
5. Check which models your account can use and pick one that supports the
   [Responses API](https://platform.openai.com/docs/api-reference/responses)
   with vision input (needed for `vision_test.py`); a small/cheap model is
   plenty for this use case. You'll set this as `MODEL_NAME` below.

### 5. Configure your API key and name

```bash
cp config/openai.env.example config/openai.env
cp config/robot.env.example config/robot.env
```

Edit `config/openai.env` and paste in the API key you just created:
```
OPENAI_API_KEY=sk-...your-key...
```

Edit `config/robot.env` and set `OWNER_NAME` to whatever you'd like the
assistant to call you. Both files are gitignored — they'll never
accidentally get committed if you push your own changes back to a fork.

Then open `listen_and_answer.py` and set two things near the top to match
what you found in step 4:
- `MODEL_NAME` — the OpenAI model you picked
- `MONTHLY_LIMIT_USD` — your own local monthly spending cap in dollars

### 6. Set your audio device names in code

You already found these card names in the "Flash and configure" section
above. Update the code to match:

- In `robot_hub.py`, the `aplay` call uses `-D plughw:0,0` — change the `0,0`
  to match your MAX98357A card/device number from `aplay -l`.
- In `wake_listener.py` and `listen_and_answer.py`, set `MIC_DEVICE` to your
  USB microphone's card name from `arecord -l`, in the form
  `plughw:CARD=<name>,DEV=0`.

### 7. Try it manually before installing services

```bash
# Terminal 1
python robot_hub.py

# Terminal 2
python wake_listener.py
```

Say "hey atlas", wait for the listening cue, then ask a question.

### 8. Run on boot (optional)

Unit file templates are in `systemd/`. Replace `YOUR_USERNAME` in all three
files with your Linux username first, then:

```bash
sudo cp systemd/atlas-robot.service systemd/atlas-wake.service systemd/atlas-hud.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now atlas-robot.service
sudo systemctl enable --now atlas-wake.service
sudo systemctl enable --now atlas-hud.service
```

`atlas-hud.service` runs the HUD in Chromium under `cage`, a minimal Wayland
kiosk compositor. Install both and enable `seatd` first, so `cage` can get
DRM/VT permission to draw fullscreen:

```bash
sudo apt install -y cage seatd
sudo systemctl enable --now seatd
```

Logs: `journalctl -u atlas-robot -f` / `journalctl -u atlas-wake -f` / `journalctl -u atlas-hud -f`.

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

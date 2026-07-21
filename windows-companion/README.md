# A.T.L.A.S. Windows Companion

A tiny authenticated service that runs on the gaming PC and lets A.T.L.A.S.
(on the Pi) trigger a **fixed whitelist** of safe actions. It cannot run
arbitrary commands, control the mouse freely, make purchases, send
messages, or delete anything outside the predefined maintenance scripts.

## What it exposes (and nothing else)

| Action | What it does |
|--------|--------------|
| `open_fusion` | Launch Fusion 360 |
| `open_project` | Open a named project from the approved list |
| `newest_screenshot` | Open the newest screenshot + return it to the Pi |
| `volume` | Volume up / down / mute (media keys only) |
| `media` | Play-pause / next / previous (media keys only) |
| `open_folder` | Open an approved folder in Explorer |
| `screenshot` | Capture the screen, return it to the Pi to analyze |
| `active_apps` | List titles of open windows |
| `run_script` | Run one predefined maintenance script by name |
| `slicer_status` | Return the slicer's status |
| `capture_screenshot` | Save the full screen to `recordings_folder` with a metadata sidecar |
| `capture_window` | Save ONE named window (by title substring) to `recordings_folder` |
| `start_recording` | Start an ffmpeg screen recording (full desktop or one window), bounded by `max_recording_seconds` |
| `stop_recording` | Stop the in-progress recording and verify the file landed on disk |
| `list_recordings` | List every capture/recording's metadata, newest first |

`capture_screenshot`, `capture_window`, and `start_recording` all refuse a
privacy-blocked window (see `privacy_blocked_window_substrings` in the
config — password managers, email, banking, etc. by default).

Every request needs the shared `X-Companion-Token`. No token, no action.

## Install (on the Windows PC)

1. Install Python 3 (python.org) if not present. Everything except
   recording is stdlib only. `start_recording`/`stop_recording` need
   **ffmpeg** on PATH (same tool already used for camera capture on the
   Pi side) — install it separately if you want screen recording.
2. Copy the `windows-companion` folder to the PC, e.g. `C:\atlas-companion`.
3. Run once to generate the config:
   ```
   python atlas_companion.py
   ```
   It writes `companion_config.json` and exits.
4. Edit `companion_config.json`:
   - Set `token` to a long random string (the same value goes in the Pi's
     `config/robot.env` as `PC_COMPANION_TOKEN`).
   - Fix `fusion_path`, `projects`, `screenshot_folder`, `approved_folders`.
   - Add any `maintenance_scripts` you want (name → command list). Only
     these can be run.
   - Set `recordings_folder` to where screenshots/window captures/
     recordings should permanently live, and `max_recording_seconds`
     to the hard ceiling for any single recording.
5. Start it:
   ```
   python atlas_companion.py
   ```
   To run at login, drop a shortcut in `shell:startup` or register it as a
   scheduled task at logon.
6. Allow it through Windows Firewall for the **Private** network only, on
   the configured port (default 5060).

## Configure the Pi side

In `config/robot.env` (gitignored) on the Pi:
```
PC_COMPANION_URL=http://<PC-LAN-IP>:5060
PC_COMPANION_TOKEN=<the same token>
```
Restart `atlas-wake` and `atlas-robot`. Test with "Atlas, what's open on my PC?".

## Security notes

- The token is the only credential — keep it long and private.
- Bind to the LAN and firewall to Private; never port-forward this to the
  internet. Remote access should go through the Phase 2-C secure phone
  link, not by exposing the companion.
- The whitelist is the security boundary. To add a capability you add a
  named action in code — there is deliberately no generic "run command"
  endpoint.

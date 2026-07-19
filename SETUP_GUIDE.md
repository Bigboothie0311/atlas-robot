# A.T.L.A.S. — Setup Guide for the PC Companion, PC Search, PC Admin & Phone Link

Everything below is **your side** — the Pi code is already done. When a
step says "on the PC" do it on the gaming rig; "on the Pi" means over SSH
to the Pi.

---

## Part 1 — Windows Companion (unlocks A, B, and E)

The companion is a tiny Python service that runs on the PC. A.T.L.A.S.
can only trigger the fixed whitelist of actions it exposes — no arbitrary
control.

### 1.1 Install Python on the PC
1. Get Python 3 from https://python.org/downloads (tick **"Add Python to
   PATH"** in the installer).
2. Verify: open PowerShell and run `python --version`. No extra packages
   are needed — the companion is stdlib-only.

### 1.2 Copy the companion over
Copy the `windows-companion` folder from the Pi to the PC, e.g. to
`C:\atlas-companion`. (USB stick, or `scp` from the Pi, or just re-create
the two files from the repo.)

### 1.3 Generate and edit the config
In PowerShell:
```powershell
cd C:\atlas-companion
python atlas_companion.py
```
It writes `companion_config.json` and exits. Open that file and set:

- **`token`** — a long random string. Generate one:
  ```powershell
  python -c "import secrets; print(secrets.token_hex(24))"
  ```
  Copy the result — you'll paste the same value on the Pi.
- **`fusion_path`** — full path to `Fusion360.exe`. Find it:
  ```powershell
  Get-ChildItem "$env:LOCALAPPDATA\Autodesk\webdeploy\production" -Recurse -Filter Fusion360.exe | Select-Object -First 1 -ExpandProperty FullName
  ```
- **`projects`** — name → `.f3d` path pairs you want to open by voice,
  e.g. `"nozzle mount": "C:\\Users\\You\\Documents\\Fusion\\nozzle.f3d"`.
  (Use double backslashes in JSON.)
- **`screenshot_folder`** — where your screenshots land (default
  `C:\Users\You\Pictures\Screenshots`).
- **`approved_folders`** — name → folder pairs Atlas may open.
- **`maintenance_scripts`** — leave the default `clear_temp` or add your
  own named commands. **Only** scripts listed here can ever run.

### 1.4 Firewall — Private network only
Allow the port (default **5060**) inbound on the Private profile:
```powershell
New-NetFirewallRule -DisplayName "ATLAS Companion" -Direction Inbound -Protocol TCP -LocalPort 5060 -Profile Private -Action Allow
```

### 1.5 Start it (and auto-start at login)
Test run:
```powershell
python atlas_companion.py
```
You should see `A.T.L.A.S. companion listening on 0.0.0.0:5060`.

To start automatically at login, create a scheduled task:
```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "C:\atlas-companion\atlas_companion.py"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "ATLAS Companion" -Action $action -Trigger $trigger -RunLevel Highest
```

### 1.6 Point the Pi at it
Find the PC's LAN IP: `ipconfig` on the PC → IPv4 Address (e.g.
192.168.0.216). Then on the **Pi**, edit `config/robot.env` and add:
```
PC_COMPANION_URL=http://192.168.0.216:5060
PC_COMPANION_TOKEN=<the same token you generated>
```
Restart the services on the Pi:
```bash
sudo systemctl restart atlas-robot atlas-wake
```

### 1.7 Test
Say: **"Hey Atlas, what's open on my PC?"** — it should read back your
window titles. If it says "the PC companion isn't set up," re-check the
URL/token and that the companion is running.

---

## Part 2 — PC Search (B) — no extra setup

Once the companion is running, this just works. Say:
**"Hey Atlas, find me videos showing how to replace an AD5X nozzle."**
Atlas wakes the PC if it can, opens YouTube to that search (long tutorials,
Shorts filtered out), full-screens it, says "results are ready," and stops
so you pick the video.

> If the PC is off and Wake-on-LAN can't wake it (the USB-adapter issue),
> Atlas will tell you. Fix WoL per Part 4 or leave the PC in sleep.

---

## Part 3 — PC Admin (E) — no extra setup

With the companion running:
- **"How's my PC?"** — CPU, RAM, disk-free, uptime; flags low disk / long
  uptime and offers a cleanup.
- **"Clean up my PC"** — runs the whitelisted `clear_temp` script only.

---

## Part 4 — Fixing Wake-on-LAN (so "boot my PC" works from off)

The learned wake target is a **Winstars USB network adapter**, which
almost certainly can't do Wake-on-LAN (USB NICs lose power when the PC
shuts down). To fix, in order of preference:

1. **Quick test first:** put the PC to **sleep** (not shut down) and say
   "boot my PC." If it wakes, sleep is your no-hardware workaround.
2. **Proper fix — wired onboard Ethernet:**
   - Plug a network cable from your router/switch into the PC's
     **motherboard** Ethernet port (not the USB dongle).
   - **BIOS/UEFI:** enable "Wake on LAN" / "Power On by PCIe/PCI".
   - **Windows:** disable **Fast Startup** (Control Panel → Power Options
     → "Choose what the power buttons do" → uncheck "Turn on fast
     startup").
   - **Device Manager** → your onboard NIC → Power Management → tick
     "Allow this device to wake the computer" and "Only allow a magic
     packet to wake the computer."
   - Then on the Pi, while the PC is on and wired, run once so Atlas
     re-learns the correct MAC — or set it explicitly in `robot.env`:
     `WOL_MAC=<onboard NIC MAC>` (find it with `ipconfig /all` → the
     Ethernet adapter's Physical Address).
3. Ask Atlas **"why won't my PC wake?"** anytime for this diagnosis.

---

## Part 5 — Secure Phone Link (C)

Lets you talk to Atlas, check the camera, review away-mode events, and
drive the PC from your phone when you're out. It's **off until you set a
token**, and must be reached over a private network — never a public
port-forward.

### 5.1 Set the token (on the Pi)
```bash
python3 -c "import secrets; print(secrets.token_hex(24))"
```
Add to `config/robot.env`:
```
PHONE_TOKEN=<that value>
```
Restart: `sudo systemctl restart atlas-robot`.

### 5.2 Make the Pi reachable — Tailscale (recommended)
On the **Pi**:
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
Follow the login link. Install the **Tailscale app on your phone** and log
in to the same account. Note the Pi's Tailscale IP (`tailscale ip -4` on
the Pi, usually `100.x.y.z`).

> Why Tailscale: only your own devices can reach the Pi. The hub's other
> endpoints are unauthenticated, so **do not** port-forward 5051 to the
> internet.

### 5.3 Call it from your phone
Any HTTP client works (iOS Shortcuts, Tasker, a REST app). Always send the
`X-Phone-Token` header. Examples (replace `100.x.y.z` and `$TOKEN`):

- **Ask a question** (continues the same conversation as the desk):
  ```
  POST http://100.x.y.z:5051/phone/ask
  Header: X-Phone-Token: <token>
  Body (JSON): {"text": "what happened while I was gone?"}
  ```
- **Status:** `GET /phone/status`
- **Away-mode events:** `GET /phone/events` (and
  `GET /phone/event_photo/<id>` for a photo)
- **What does the camera see:** `POST /phone/camera`
- **Drive the PC:** `POST /phone/pc/youtube` body `{"query":"..."}`, or
  `/phone/pc/open_fusion`, `/phone/pc/screenshot`, `/phone/pc/apps`

### 5.4 iOS Shortcut quick recipe
Shortcuts → new → "Get Contents of URL" → your Tailscale URL → Method POST
→ Headers add `X-Phone-Token` = your token → Request Body JSON `text` =
Ask-for-input. Add "Show Result." Name it "Ask Atlas." Add to home screen.

---

## Part 6 — Phone presence (arrival, goodbye & auto face-gate arming)

So A.T.L.A.S. can greet you when you get home, and arm face verification
when you leave, it needs your phone's Wi-Fi MAC address.

### 6.1 Turn OFF MAC randomization for your home Wi-Fi (important)
Phones fake a random MAC per network by default, which breaks presence
detection. Disable it for your home network only:
- **iOS:** Settings → Wi-Fi → tap the (i) by your network → turn off
  "Private Wi-Fi Address" → rejoin.
- **Android:** Settings → Wi-Fi → your network → Privacy → choose
  "Use device MAC" → reconnect.

### 6.2 Find the MAC
Either read it from the phone (iOS: Settings → General → About → Wi-Fi
Address; Android: Settings → About phone → Status → Wi-Fi MAC), or ask
A.T.L.A.S. "what's on my network" while the phone is connected and pick
it out by maker (Apple / Samsung / etc.).

### 6.3 Set it (on the Pi)
Add to `config/robot.env` (gitignored):
```
PHONE_MAC=AA:BB:CC:DD:EE:FF
```
Restart: `sudo systemctl restart atlas-robot`. Now:
- Phone rejoins after 30+ min away → spoken **arrival greeting** with
  pending reminders and priorities.
- Phone leaves the LAN → **face gate arms** (next user is verified).
- "is my phone home" answers from presence.

## Quick reference — files
- Companion: `windows-companion/atlas_companion.py` + `README.md`
- Phone link: `PHONE_LINK.md`
- Chief of staff: `CHIEF_OF_STAFF.md`
- Full command list: `COMMANDS.md`

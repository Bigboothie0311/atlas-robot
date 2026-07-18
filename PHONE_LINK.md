# Secure Phone Link

A.T.L.A.S. exposes an authenticated `/phone/*` API on the hub (port 5051)
so you can reach it from your phone when you're away. It stays **inert
until you configure `PHONE_TOKEN`** in `config/robot.env`.

## Endpoints (all require `X-Phone-Token`)

| Method / path | Purpose | Cost |
|---------------|---------|------|
| `POST /phone/ask` `{text, speak_at_desk?}` | Ask Atlas a question; continues the same conversation as the desk | model (on-demand) |
| `GET /phone/status` | Weather, PC/printer state, device count, unreviewed intruders | local |
| `GET /phone/events` | Away-mode intruder records | local |
| `GET /phone/event_photo/<id>` | An intruder photo | local |
| `POST /phone/camera` | "What does the camera see?" — describes aloud at the desk | model (on-demand) |
| `POST /phone/pc/<action>` | Approved PC actions: `open_fusion`, `screenshot`, `youtube`, `apps` | local (+companion) |

`open_fusion`, `youtube`, etc. reach the PC through the Windows companion,
so they need the PC on and the companion running.

## Reachability — DO NOT port-forward this to the internet

The hub's non-phone routes (`/state`, `/speak`, …) are **unauthenticated**
and must never be exposed. Reach the phone API over a private overlay
instead:

**Recommended: Tailscale.** Install Tailscale on the Pi and your phone,
join the same tailnet, and hit `http://<pi-tailscale-ip>:5051/phone/...`.
Only your own devices can reach it; nothing is exposed publicly.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Alternative: a Cloudflare Tunnel scoped to only the `/phone/` path with
Cloudflare Access in front. More setup; Tailscale is simpler and safer
for a single user.

## Configure

In `config/robot.env` (gitignored):
```
PHONE_TOKEN=<a long random string>
```
Restart `atlas-robot`. Build a phone Shortcut / small app that sends the
token header to the Tailscale IP. Example:
```
curl -H "X-Phone-Token: $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"text":"what happened while I was gone?"}' \
     http://<pi-tailscale-ip>:5051/phone/ask
```

## Security model

- The token is the credential; keep it long and private.
- Tailscale/Access is the network boundary — the token is defense in depth.
- Only the whitelisted PC actions are reachable; the companion's own
  whitelist still applies on top.

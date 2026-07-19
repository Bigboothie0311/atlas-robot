# A.T.L.A.S. — Session Handoff

> **Read this first, then `COMMANDS.md` and `SETUP_GUIDE.md`.**
> Canonical codebase: `/home/atlas/atlas-robot` on the Pi (`atlas@192.168.0.183`).
> A directive for the next working session is at the bottom of this file — execute it.

## Current state (as of 2026-07-18)

- **All 38 formal tasks complete** — Phases 1–4, P1-A→G, P2-A→F, F2→F13, plus the smarter auth-trigger and phone-presence fixes.
- Nothing mid-flight or broken: **all 4 services active**, regression suite **23/23**, working tree clean at last check.
- ~19 commits are **local-only and unpushed** since the last force-push. The Pi working tree is the source of truth; it is ahead of GitHub and also contains face-auth work not on GitHub.
- Graphify analysis is current in `graphify-out/` (771 nodes, 1,466 edges, 30 communities, no import cycles). Refresh with `graphify update .` (zero API cost).

## Pending — requires the owner, not code work

1. **Push to GitHub** — only with the owner's explicit go-ahead. Never push otherwise.
2. **Install the Windows companion** on the PC (`windows-companion/README.md`) — unlocks PC/YouTube/profiles/admin commands. Cannot be done from the Pi.
3. **Tailscale + `PHONE_TOKEN`** — activates the phone link (currently inert). See `PHONE_LINK.md`.
4. **Confirm "Private Wi-Fi Address" stays off** on the owner's phone. The phone MAC used for presence detection is stored in gitignored config; if presence misfires, that iOS setting likely rotated the MAC.

## Optional / deferred (hardware or a decision needed)

- ReSpeaker 4-mic array for true direction-finding (`ROOM_AWARE_HEARING.md`)
- Live calendar/email sources for chief-of-staff (adds on-demand token cost — needs approval)
- Planet visibility in Sky Watch (needs an ephemeris lib)
- Emergency-protocol hardware: UPS, smoke/water sensors, smart plugs (`emergency.py` stubs document each)

## Standing rules

- **Never `git push`** without the owner asking.
- **Always confirm before restarting services** (canary restarts as part of an approved autonomous run are the exception — restart one service, verify, proceed).
- Live-action code (PC shutdown, goodbye routine) is tested **only through mocked/unconfigured paths**.
- Never commit secrets/PII; private values live in gitignored config/data.

## Architecture quick-orientation

- `_handle_turn_body()` in the turn-dispatch module is the mega-hub (97 edges, links 13 of 30 communities) — refactor with extreme care; graphify flags it as a split candidate.
- Other cross-community bridges: `record_incident()` (logbook) and `_run_proactive_checks()` (hub watcher).
- Deterministic/local command handling always runs before any paid model call.

---

# DIRECTIVE FOR THE NEXT WORKING SESSION

Work autonomously in `/home/atlas/atlas-robot`. I will not be available for questions. Use best judgment, complete everything safely, test it, and give one final report. Do not pause for approval unless a truly destructive/external action cannot be avoided.

Important current state:

* Treat the Pi working tree as the source of truth; it contains newer local-only commits and uncommitted face-auth fixes not on GitHub.
* Preserve the new multi-angle enrollment, 18-frame verification, strong-match voting, one-hour/departure arming, and persistent unauthorized rechecks.
* Preserve `.gitattributes`, `.graphifyignore`, `graphify-out/`, and unrelated work.
* Never reset/discard changes or push to GitHub.
* Create a local rollback backup/checkpoint before editing.

Required upgrades:

1. Intruder clearing

* Add local voice intent: "clear intruder alerts" plus close natural variants.
* The spoken phrase itself is authorization to clear them: remove unreviewed alert state and associated stored intruder photos/records without another confirmation.
* Return a fast count-based result.
* Keep "were there any unauthorized users while I was gone?" review behavior working.

2. Conflict cleanup + privacy audit

* Trace the current architecture and fix conflicting/duplicate routes, intents, constants, state handling, dead code, or newer features overriding older ones.
* Avoid a broad rewrite; preserve working features.
* Audit the working tree and all reachable Git history for secrets/PII: credentials, tokens, passwords, precise location/address, phone/email, SSID, usernames, LAN IP/MAC/hostnames, screenshots, photos, and logs.
* Remove current tracked leaks, move private configuration to gitignored config/data, and update examples with neutral placeholders.
* Do not rewrite history or push. If history contains anything requiring history rewriting or credential rotation, report the exact finding and remediation without exposing the sensitive value.

3. Expanded PC control

* Harmless app/window actions must not request confirmation.
* Support natural commands such as "open Steam," "launch Discord," "open [installed app]," "start my gaming profile," etc.
* Dynamically resolve installed Windows apps, Start Menu shortcuts, approved executables, and Steam games instead of requiring every app to be manually hardcoded.
* Gaming profile must at least open Steam and Discord; retain/extend existing profiles.
* Keep the Windows companion token-authenticated. Do not turn this into arbitrary remote shell execution.
* Destructive/sensitive actions—delete, purchases, messages, shutdown, credential changes—must retain existing safety gates.

4. Fast intruder review

* The first intruder result/photo should appear almost immediately; local data must not incur a ~15-second startup delay or model call.
* Load records once, send them to the HUD in one operation, display the first photo immediately, and handle speech/display timing asynchronously.
* Preserve the intended 10-second full-screen viewing period per photo, but do not block the command pipeline with unnecessary sleeps or sequential network overhead.
* Delete each photo only after its display period completes.

5. Weather-map HUD

* Add commands such as "show weather map," "show weather radar," and "show my state's weather."
* Provide a polished full-screen state/regional map with current conditions, animated rain/radar forecast, precipitation timing, temperature, alerts, and short forecast.
* Read location/region from gitignored configuration—never hardcode personal location.
* Prefer free authoritative/keyless sources when practical, cache locally, load quickly, and degrade cleanly offline.
* Add an exit/close weather HUD command.

6. PC Screen Copilot

* Expand the companion so Atlas can answer "what's on my screen?", explain visible errors, identify active applications/windows, focus/minimize/close requested windows, and dismiss clearly safe popups.
* Use screenshots/vision only on demand and reuse the existing secure companion transport.
* Harmless screen/window control needs no repeated confirmation; destructive or ambiguous high-risk actions remain gated.

7. Teachable commands/macros

* Support: "When I say [phrase], [actions]."
* Store taught commands locally in gitignored data.
* Compile actions through the existing safe action registry/companion controls—never arbitrary shell text.
* Add commands to list, run, update, and delete taught commands.
* Validate before saving, prevent recursion/collisions with protected commands, and provide concise success/failure speech.

8. Passive face + voice authentication

* Add fully local speaker recognition using the owner's voice enrollment and normal wake/command audio.
* Voice may strengthen a borderline face match but must never override a strong face rejection.
* Preserve face-only authorization when the voice component is unavailable.
* Store voice embeddings locally in gitignored data; no cloud/model tokens.
* Add guided owner voice enrollment and status/re-enrollment commands.
* Avoid making authentication slower; reuse audio already captured whenever possible.

9. Additional upgrades

* After the required work, independently choose and implement 2–4 genuinely useful, high-tech, software-only A.T.L.A.S. features.
* Avoid duplicates, extra hardware/accounts, high recurring token use, gimmicks, or fake functionality.
* Integrate them into the existing architecture and command system.

Execution rules:

* Deterministic/local commands must run before any paid model call.
* Keep services responsive; optimize repeated file/API/camera operations.
* If Windows-side installation or credentials are unavailable, finish the Pi/companion code and exact setup documentation rather than waiting.
* Run syntax checks, unit/regression tests, parser tests, endpoint tests, and a canary service restart. Roll back any failing change.
* Do not delete user data, rewrite Git history, expose secrets, push, purchase, message anyone, or open public network access.

Before finishing:

* Update `COMMANDS.md` with every supported spoken command, variants, behavior, safety/confirmation rules, and whether it is local or uses model tokens.
* Update setup/config examples for every new option.
* Provide one final report covering: root conflicts found/fixed, privacy audit results, every feature added, files changed, tests and live checks, performance improvements, any incomplete PC-side steps, rollback location, and the updated command list.
* Do not ask me questions; complete all safely possible work and report only when finished.

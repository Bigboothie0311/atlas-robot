# A.T.L.A.S. V2/V3 — Phase 4 Continuation (addendum)

> ## ⬛ CHECKPOINT — read this block first, every session
> **This is the newest handoff file.** If Wesley says "check the handoff
> and continue," this is the one — check the repo root for a newer
> `ATLAS_V2_AGENT_HANDOFF_*.md` by date first (`ls -lat *.md`), since a
> future session will add one instead of editing this file (see
> Phase2/Phase3 pattern already in this repo).
>
> - **Branch / HEAD:** `atlas-v2-agent` @ `afdcf5f` (verify with `git log -1 --oneline`)
> - **Phase / milestone:** Phase 4, milestone 1 of 1 planned so far
> - **Ledger status:** `implemented` (deliberately NOT `live_verified` —
>   one real capability, ffmpeg recording, is genuinely blocked; see
>   below). This is the honest state as of 2026-07-20, end of session 2.
> - **What's LIVE-VERIFIED for real (2026-07-20, across two sessions —
>   Pi hardware AND the real Windows PC at 192.168.50.2):**
>   - `camera_gate.capture_clip()` — real h264/aac mp4, video-only AND
>     video+audio, confirmed with `ffprobe` on the Pi
>   - `scrot` capture of the kiosk's own `:0` display — real 800x480 PNG
>     (`scrot` was missing on the Pi and has been installed)
>   - Deployed the updated `atlas_companion.py` to the real PC
>     (`C:\atlas-companion`), added `recordings_folder` to its config,
>     restarted it, confirmed `/health` responds
>   - `capture_screenshot`, `capture_window` (privacy-blocklist refusal
>     AND no-match reporting), `list_recordings` all tested via curl
>     directly against the real companion — all behave exactly as coded
>   - **Full Pi→PC path end to end, through the real code (not mocks):**
>     both `pi.capture_hud_frame` and `camera.capture_clip` executed via
>     the actual `ToolExecutor`/`ResultVerifier`/`SFTPClient`/companion
>     stack — file captured on Pi → uploaded → SHA-256 verified →
>     confirmed present on the PC with matching size → local Pi copy
>     deleted → tool marked `VERIFIED`. Test artifacts cleaned up after.
> - **Two real blockers found this session (not yet resolved):**
>   1. **ffmpeg is not installed on the PC.** `start_recording` fails
>      with a clean, non-crashing error ("could not start ffmpeg: ...
>      cannot find the file"). Nothing to fix in code — someone needs to
>      install ffmpeg on the PC and put it on PATH, then re-test
>      `start_recording`/`stop_recording`.
>   2. **The companion had to be restarted via SSH for this test**,
>      which launched it in Windows **Session 0** (a service session)
>      instead of Wesley's normal interactive login session. Session 0
>      is isolated from the real desktop, so `capture_screenshot`
>      mechanically succeeded (valid PNG + sidecar) but the image
>      content was blank, not the real screen. This should resolve on
>      its own the next time the companion starts normally (its
>      existing login-time startup shortcut, not SSH) — **not yet
>      confirmed**. If it recurs after a normal restart, the fix is
>      likely moving the companion's startup to a Scheduled Task with
>      "Run only when user is logged on" rather than a raw shortcut.
> - **Known real constraint (not a bug):** `camera.capture_clip`'s audio
>   branch needs `plughw:CARD=Device,DEV=0` free. `atlas-wake.service`
>   holds it via its own `arecord` between wake words, so calling
>   `capture_clip` with audio while `atlas-wake` is idly listening (e.g.
>   from a standalone test script) fails with "device busy" — confirmed
>   by stopping `atlas-wake`, capturing successfully, restarting it
>   clean, twice. Inside a real voice turn the wake listener has already
>   released the mic before `handle_turn()` runs, so this should be a
>   non-issue in practice — **not yet confirmed with an actual spoken
>   command**, only via direct Python calls to the tool.
> - **Not done at all yet:** confirming real (non-blank) screen content
>   capture from a normal PC restart; installing ffmpeg on the PC; the
>   HUD recording-state indicator (Phase 4's own spec still wants one);
>   one real end-to-end spoken-command test ("Hey Atlas, record a clip
>   of yourself" / "...take a picture of your screen"); Phase 11 (edit
>   pipeline) and Phase 12 (Instagram publish) — both still `not_started`.

**Prepared:** July 20, 2026
**Branch:** `atlas-v2-agent` — HEAD `afdcf5f`
**Full instructions:** [ATLAS_V2_AGENT_HANDOFF_PHASE2_CONTINUATION.md](ATLAS_V2_AGENT_HANDOFF_PHASE2_CONTINUATION.md)
(sections 0, 3, 4, 5, and 6 there still apply verbatim; the phase list in
section 4 there is the source of truth for Phase 4/11/12 requirements —
this addendum only records what changed since it was written).

## Why this session exists

Wesley asked for a "hey Atlas, record and edit a showcase for Instagram"
capability where A.T.L.A.S. records himself (not just the PC screen),
edits the footage, writes a witty script/caption, and posts it — with
confirmation once per showcase before the actual Instagram upload. That
request spans three separate roadmap phases:

- **Phase 4** — recording/capture foundation (this session's scope)
- **Phase 11** — the FFmpeg edit pipeline, captions, A.T.L.A.S. narration
- **Phase 12** — Instagram publish, with a confirm-once-then-post gate

Wesley chose to scope **this session to Phase 4 only**. Phases 11 and 12
are still `not_started` in the ledger and are the next work.

## Phase 4 milestone 1 is implemented and mostly live-verified

Commit `afdcf5f`, full suite **645 passed**. See the checkpoint block at
the top for exactly what's been proven against real hardware/the real PC
vs. what's still open (ffmpeg on the PC, one non-SSH restart, one real
spoken-command test).

- Windows companion (`windows-companion/atlas_companion.py`): new
  `capture_screenshot`, `capture_window`, `start_recording`,
  `stop_recording`, `list_recordings` actions. Recording state is a
  sidecar JSON file with crash-orphan reconciliation on companion
  restart, a privacy title blocklist (password managers, email,
  banking, etc.), and duration bounded via ffmpeg's own `-t` flag
  rather than a separate watchdog thread.
- `atlas_agent/pc_tools.py`: those five actions registered as
  `pc.capture_screenshot`, `pc.capture_window`,
  `pc.start_screen_recording`, `pc.stop_screen_recording`,
  `pc.list_recordings` AtlasTools with verifiers.
- `camera_gate.capture_clip()`: A.T.L.A.S. recording himself — video
  from the USB camera, audio from the Pi mic — bounded to
  `MAX_CLIP_SECONDS` (120s).
- New `atlas_agent/pi_tools.py`: `pi.capture_hud_frame` (kiosk screen
  via `scrot`) and `camera.capture_clip` AtlasTools. Both upload to the
  PC over a new `SFTPClient.upload()` (mirrors the existing
  `download()`, same SHA-256 verification) and only delete the local Pi
  copy once the upload is verified — footage never lingers on the Pi.
  Wired into `runtime_factory.build_pc_agent_runtime` via an optional
  `recordings_remote_root` parameter (already pointed at
  `C:\Users\wesle\Videos\AtlasRecordings` in `listen_and_answer.py`).

Also worth knowing: Phase 4's own spec (see the phase list) still wants
an **HUD recording state** — this milestone did not touch `hud/app.js`
or `robot_hub.py`. That's a small follow-up (same `/screen`-flag pattern
already used for dark-mode) before Phase 4 is fully done.

## Testing checklist (what's actually left — most of it is done)

Session 2 (2026-07-20) did the heavy lifting: Pi hardware, PC companion
deploy, curl smoke tests, and the full Pi→PC upload path were all
live-verified against the real devices (see checkpoint above for exact
evidence). What's genuinely left, cheapest first:

1. **Install ffmpeg on the PC** and put it on PATH, then re-run:
   ```bash
   curl -s -X POST http://192.168.50.2:5060/start_recording \
     -H "X-Companion-Token: <token from companion_config.json>" \
     -d '{"target":"full","max_seconds":10}'
   # wait 10s
   curl -s -X POST http://192.168.50.2:5060/stop_recording \
     -H "X-Companion-Token: <token>" -d '{}'
   ```
   Confirm a real playable mp4 lands in
   `C:\Users\wesle\Videos\AtlasRecordings`.
2. **Confirm real (non-blank) screenshot content** after a *normal*
   companion restart (not an SSH-triggered one) — log Wesley out/in or
   have him relaunch the startup shortcut, then re-run the
   `capture_screenshot` curl above and open the resulting PNG. If it's
   still blank, the companion's startup mechanism needs to change to a
   Scheduled Task with "Run only when user is logged on."
3. **One real spoken-command test** — "Hey Atlas, record a 10 second
   clip of yourself" and "Hey Atlas, take a picture of your screen" —
   to confirm the full voice → planner → tool → upload path works
   outside of direct Python calls, and that the mic-busy constraint
   above is genuinely a non-issue in a real turn.
4. Once 1–3 pass, flip `phase4_screen_capture` to `live_verified` in
   the ledger and add the HUD recording-state indicator to fully close
   out Phase 4 before starting Phase 11.

## Next session

Verify state first (HEAD `afdcf5f`, 645 tests), then work through the
4-item testing checklist above to fully close Phase 4, or move straight
to Phase 11 (edit pipeline) since the capture primitives it needs
already exist and are proven end to end. Same milestone loop as always:
graphify orientation (max 3 queries) → tests first → implement → full
suite → `graphify update .` → restart only affected services → live
verify → commit exact paths → update `implementation_ledger.py`
honestly. Query the ledger by voice with "what is your upgrade status".

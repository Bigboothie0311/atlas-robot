# A.T.L.A.S. V2/V3 — Phase 4, Session 4

> ## ⬛ CHECKPOINT — read this block first, every session
> **This is the newest handoff file.** Check the repo root for a newer
> `ATLAS_V2_AGENT_HANDOFF_*.md` by date first (`ls -lat *.md`) before
> trusting this one.
>
> - **Branch / HEAD:** `atlas-v2-agent` — HEAD is `a9ce869` ("fix:
>   self-recorded clips are silent and unplayable on Windows"). `git log
>   -1` to confirm it's still there. **674 tests passing.**
> - **⏸️ LEFT OFF HERE (2026-07-21, iteration 5):** Wesley live-tested
>   iteration 4's work — the REC indicator worked, but the clip itself
>   was silent and wouldn't play on his PC at all. Root-caused and fixed
>   both problems (see "What changed 2026-07-21, iteration 5" below):
>   an mp4 pixel-format bug Windows' native players reject outright, and
>   the *actual* mic-contention fix — the previous session's
>   retry-then-fallback (`9030897`) could never succeed in practice
>   because barge-in never releases the mic until the whole answer
>   finishes, so self-recording (invoked mid-answer) always landed on
>   muted. Added `mic_arbiter.py` so `capture_clip()` now actively
>   coordinates with `watch_for_barge_in()` to get the mic released
>   instead of hoping a retry gets lucky. **Not yet live-voice-verified —
>   this is the first-ever change to the core barge-in device loop and it
>   has zero prior test coverage**, so treat it as higher-risk than the
>   other fixes this session. See "What Wesley needs to test" at the
>   bottom. **Still stopped deliberately before Phase 11** (edit-and-post
>   pipeline) — needs Wesley's input on Instagram credentials, licensed
>   audio, and branding/caption style before any of that gets built; see
>   that section below, unchanged from iteration 4.
> - **Phase / milestone:** Phase 4, milestone 1 (spoken-command tests,
>   see below) plus an out-of-band Phase 3 fix Wesley reported directly:
>   he asked Atlas to turn off his PC and it didn't work.
> - **What changed 2026-07-21, iteration 5 (silent/unplayable clip
>   investigation):** Wesley live-tested iteration 4's mic-contention fix.
>   The REC indicator worked, but the resulting clip had no audio, and
>   separately wouldn't open on his PC at all — Windows reported
>   "unsupported encoding settings" despite it being a real `.mp4`. Two
>   distinct bugs:
>   1. **Unplayable file.** The mjpeg v4l2 camera source decodes to
>      `yuvj420p` (JPEG full-range color). `libx264` encoded that as-is,
>      which `ffprobe` reads fine but Windows' built-in players
>      (Photos/Movies & TV, Media Foundation) reject outright. **Fix:**
>      force `-pix_fmt yuv420p` on every `capture_clip()` video encode.
>   2. **Silent clip — the actual root cause of the mic-contention "fix"
>      never really fixing anything.** The prior session's
>      retry-then-fallback (`9030897`) could structurally never succeed:
>      `watch_for_barge_in()` doesn't release the mic until the *entire*
>      streamed answer finishes speaking, and self-recording is invoked
>      *during* that same answer — so a 1.5s retry was always going to
>      find the mic still busy and fall back to muted, every single time,
>      not just occasionally. **Fix:** added `mic_arbiter.py`, a small
>      fail-open coordination point. `capture_clip()` now actively asks
>      `watch_for_barge_in()` to release the mic before opening it, and
>      `listen_for_barge_in()` cooperates — closes its `arecord`, confirms
>      release, waits for the request to clear, reopens. The old
>      busy-retry/muted-fallback logic stays as a safety net if nothing
>      responds in time (e.g. `atlas-wake` isn't running at all).
>   12 new tests (`tests/test_mic_arbiter.py`,
>   `tests/test_listen_for_barge_in.py`, updated
>   `tests/test_camera_gate.py`) — **674 passing (was 666)**.
>   `graphify update .` run, `atlas-wake` restarted clean. Committed as
>   `a9ce869`. **Higher risk than the other fixes this session** — this
>   is the first-ever change to `listen_for_barge_in`'s device-handling
>   loop, which had zero prior test coverage and cannot be fully
>   validated without live mic hardware. **Not yet live-voice-verified.**
>   Wesley's stated end goal, for context: record witty short-form self
>   clips, edit them on the PC, post to Instagram. This fix targets
>   "capture with real narration" specifically — editing and posting are
>   still Phase 11, untouched (see below).
> - **What changed 2026-07-21, iteration 2 (PC shutdown investigation):**
>   Wesley reported "he couldn't turn off my PC." Root cause:
>   `pc_control.py` has always called the `shutdown_pc`,
>   `cancel_pc_shutdown`, and `empty_recycle_bin` companion actions, but
>   `windows-companion/atlas_companion.py`'s `ACTIONS` dispatch dict never
>   registered handlers for any of them — they simply didn't exist
>   server-side, so every request 404'd as "unknown action" and Atlas
>   correctly reported the failure. (A prior ledger note claiming
>   "recycle-bin emptying and shutdown already existed pre-Phase-3" was
>   wrong.) `youtube_search` had the same problem from the other
>   direction: its handler function existed but was never added to
>   `ACTIONS`. **Fix:** implemented `act_shutdown_pc` (`shutdown /s /t
>   60`, so a misheard command is always recoverable via
>   `act_cancel_pc_shutdown` = `shutdown /a`), `act_empty_recycle_bin`
>   (`Clear-RecycleBin`), and registered all four
>   (`shutdown_pc`/`cancel_pc_shutdown`/`empty_recycle_bin`/
>   `youtube_search`) in `ACTIONS`. Added regression tests in
>   `tests/test_windows_companion_actions.py` — **658 passing (was
>   652)**. Updated `windows-companion/README.md`'s action table.
>   `implementation_ledger.py`'s `phase3_pc_companion` entry updated with
>   the honest root cause and evidence. Committed as `e09cac4`.
>   **Not yet live-verified — see blocker below, same one as the
>   pre-existing `focus_or_open_app`/`active_window` fixes.**
> - **⚠️ Standing blocker, unchanged by this fix:** none of this takes
>   effect until Wesley manually copies the updated
>   `windows-companion/atlas_companion.py` to the real Windows PC and
>   restarts the companion service there — this agent must not overwrite
>   the deployed companion itself (policy, and it has no access to the PC
>   filesystem from the Pi). Until that deploy happens, "turn off my PC"
>   will keep failing exactly as Wesley saw, even though the code fix is
>   committed. **Tell Wesley this explicitly** — it's the one manual step
>   between this fix and it actually working.
> - **What changed the prior session (2026-07-21, iteration 1 — Phase 4
>   spoken-command tests):**
>   1. **Ran the actual spoken-command test** Session 3 left open: "Hey
>      Atlas, take a picture of my screen" and "Hey Atlas, record a 10
>      second clip of yourself" — the first real end-to-end voice test of
>      Phase 4, not curl/SSH.
>   2. **Bug found and fixed — PC screenshot misrouted to Pi selfie.**
>      `is_vision_command()` (`listen_and_answer.py`) does a fuzzy
>      word-intersection match ({picture, photo, camera, ...} ∩ {take,
>      show, ...}) and runs *before* `_pc_dispatch()` in the turn-handling
>      order. "Take a picture of my screen" matched it (picture + take)
>      and the Pi ran its own camera instead of ever reaching
>      `pc_control.screenshot_to_hud()` — confirmed live: Atlas described
>      "a person leaning close to the camera in a kitchen," not the PC
>      screen. Also, `PC_SCREENSHOT_PHRASES` never contained that
>      phrasing at all, so even a routing fix alone wasn't enough.
>      **Fix:** `is_vision_command()` now returns `False` whenever
>      "screen" is one of the words (a Pi camera vision request never
>      means the monitor); added `"take a picture of my screen"` /
>      `"take a picture of the pc screen"` / photo/screenshot variants to
>      `PC_SCREENSHOT_PHRASES`. **Confirmed fixed live** — Wesley re-ran
>      the same phrase, the real PC screenshot displayed on the HUD.
>   3. **Bug found and fixed — model refused a real capability.**
>      "Record a 10 second clip of yourself" fell through to the plain
>      LLM answer path (`_answer_and_speak`), which is governed entirely
>      by `capabilities.REGISTRY` injected into the system prompt as
>      *"These are the ONLY device actions you can actually perform... if
>      asked to do something not in this list, say plainly that you
>      can't."* Phase 4's actual capabilities (`camera_gate.capture_clip`
>      self-recording, PC `start_recording`/`stop_recording`) were never
>      added to `capabilities.py` when Phase 4 landed, so the model
>      correctly-per-its-own-instructions said *"I can't record video
>      clips yet."* **Fix:** added `self_record_clip` and
>      `pc_screen_recording` entries to `capabilities.REGISTRY`.
>      **Confirmed fixed live** — same phrase, same session, the model
>      now calls the `run_atlas_agent` tool (20,498 input tokens, a real
>      agent run) instead of refusing outright.
>   4. **New real blocker found — mic contention during a live turn.**
>      With the capability now recognized, the agent actually attempted
>      `camera_gate.capture_clip()`, and its audio branch failed for
>      real: `ffmpeg` got `cannot open audio device
>      plughw:CARD=Device,DEV=0 (Device or resource busy)`. Root cause:
>      `watch_for_barge_in()` (`listen_and_answer.py` ~L3501) holds its
>      own `arecord` open on that same mic device for the *entire*
>      duration of the streamed answer — including whatever tool calls
>      happen inside that response. This **directly contradicts** the
>      Session 3 assumption ("this also confirms the mic-busy constraint
>      noted in Session 2 is a non-issue in a real turn") — it reproduced
>      on the very first live attempt. Atlas's own spoken answer was
>      honest about it: *"The self-recording failed, and I couldn't
>      verify that a video was created."*
>   5. Added regression tests for both fixed bugs
>      (`tests/test_pc_dispatch_app_routing.py`,
>      `tests/test_capabilities_registry.py`) — **652 tests passing**
>      (was 645). Ran `graphify update .`. Restarted `atlas-wake` clean
>      (the only service that imports `listen_and_answer.py`/
>      `capabilities.py`).
>   6. Ledger (`data/implementation_ledger.json`, `phase4_screen_capture`)
>      updated with both fixes and the new blocker. **State intentionally
>      left at `implemented`, not `live_verified`** — screenshot-by-voice
>      now genuinely works end to end, but self-recording-by-voice still
>      fails on the mic-contention bug above.
> - **PC access used this session:** none needed — everything this
>   session was Pi-local (code, tests, `atlas-wake` restart) plus live
>   voice testing by Wesley.

**Prepared:** July 21, 2026 (session 4)
**Branch:** `atlas-v2-agent`
**Full instructions:** [ATLAS_V2_AGENT_HANDOFF_PHASE2_CONTINUATION.md](ATLAS_V2_AGENT_HANDOFF_PHASE2_CONTINUATION.md)
(sections 0, 3, 4, 5, and 6 there still apply verbatim). See
[ATLAS_V2_AGENT_HANDOFF_PHASE4_CONTINUATION.md](ATLAS_V2_AGENT_HANDOFF_PHASE4_CONTINUATION.md)
and
[ATLAS_V2_AGENT_HANDOFF_PHASE4_SESSION3.md](ATLAS_V2_AGENT_HANDOFF_PHASE4_SESSION3.md)
for the full history this session builds on.

## What's left on the PC-shutdown fix

~~Done.~~ Wesley deployed the updated companion and live-confirmed "shut
down my PC" works (2026-07-21). `phase3_pc_companion` ledger flipped to
`live_verified` for shutdown_pc/cancel_pc_shutdown/empty_recycle_bin/
youtube_search. `focus_or_open_app`/`active_window` rode along on the
same deploy but weren't separately re-confirmed — flag it if Wesley
reports either misbehaving.

## What's left to fully close Phase 4

1. ~~**Fix the mic-contention bug.**~~ **Attempted twice.** Iteration 3's
   retry-and-fallback (`9030897`) was live-tested by Wesley in iteration
   4/5 and, as suspected, always landed on the muted fallback — it could
   never actually succeed given how `watch_for_barge_in()` holds the mic.
   Iteration 5 (`a9ce869`) took the harder, originally-deferred approach
   instead: `mic_arbiter.py` coordinates an actual mic release between
   `capture_clip()` and `listen_for_barge_in()`. This is now the real
   fix; the retry/fallback from iteration 3 remains as a fail-open safety
   net underneath it. 12 more regression tests, 674 passing total.
   **Still needs a real live-voice test — this is higher-risk than the
   other fixes**, since it's the first-ever change to
   `listen_for_barge_in`'s device loop and that loop has zero prior test
   coverage. Say "Hey Atlas, record a 10 second clip of yourself" and
   **actually talk during the recording window** — confirm the clip has
   real narration, AND separately confirm barge-in still works normally
   afterward (say "Hey Atlas" mid-answer on an unrelated turn) since a
   regression there would be silent and easy to miss.
2. Once self-recording-by-voice is live-verified end to end (see item 1),
   flip `phase4_screen_capture` to `live_verified` in the ledger.
3. ~~**Add the HUD recording-state indicator.**~~ **Done 2026-07-21
   (iteration 4), pending live verification.** New `/hud/recording` flag
   endpoint (same `/screen`-flag pattern as dark mode), `recording_active`
   in `GET /state`, a `recording-active` body class toggled by
   `hud/app.js`, and a pulsing red "REC" dot in the masthead
   (`hud/style.css`). Wired into `capture_self_clip` in
   `atlas_agent/pi_tools.py` (flag on before the capture, off in a
   `finally` after) via a best-effort HTTP notifier that swallows its own
   failures. Scoped to Pi self-recording only — **not** wired to PC
   screen recording (`pc.start_screen_recording`), which would need a
   separate design for polling PC state back to the Pi HUD; flag that as
   a follow-up if Wesley wants it too. 4 new tests, 666 passing.
   Committed as `d693854`. `atlas-robot`/`atlas-wake`/`atlas-hud`
   restarted clean, confirmed `recording_active: false` in a live
   `GET /state`. **Still needs a real live-voice test** — confirm the REC
   dot actually appears on the physical kiosk during self-recording and
   disappears after.
4. **Phase 11 (edit pipeline) — deliberately not started.** This is where
   this session stopped. It needs decisions only Wesley can make before
   any code gets written:
   - **Instagram credentials/OAuth.** No API access exists yet at all.
   - **Licensed background audio.** Can't just grab something off the
     internet — copyright risk. Needs a real source (royalty-free
     library, an actual license, or Wesley's own audio).
   - **Branding/caption style.** Logo overlay? Watermark? Caption tone?
     Nothing defined yet.
   - **Which editing approach.** Deterministic FFmpeg filter chains vs.
     something higher-level — not evaluated this session.
   - The pipeline's last step is a **real public Instagram post**, which
     the safety model (see the main mission doc, "Safety and Authority
     Model") explicitly requires confirming the exact media and caption
     with Wesley before ever sending — this isn't a capability to just
     build silently and gate behind a runtime confirmation prompt later;
     the credentials and content-source decisions above have to happen
     with Wesley first.
   The capture primitives Phase 11 will consume already exist and are
   proven end to end (PC screenshot confirmed live; Pi self-recording
   fixed this session, pending its own live-voice confirmation above).

## Next session

Verify state first (`git log -1`, `./venv/bin/python -m pytest tests/ -q`
should show 666+ passing). Everything code-side for this session's two
fixes is done — what's left is live voice verification (see the
checklist below) and then, only after Wesley weighs in on the Phase 11
questions above, starting the edit pipeline. Same loop as always:
graphify orientation (max 3 queries) → tests first → implement → full
suite → `graphify update .` (only if source changed) → restart only
affected services → live verify → commit exact paths → update
`implementation_ledger.py` honestly. Query the ledger by voice with
"what is your upgrade status".

## What Wesley needs to test (in order)

Say/do each of these for real, one at a time, and report back what
actually happened in this same order:

1. **"Hey Atlas, record a 10 second clip of yourself" — and actually
   talk during the 10 seconds.** This is the real test of the
   `mic_arbiter` fix. Pass = the clip has your voice in it, not silence.
2. **Play the resulting clip on your PC.** Confirms the `-pix_fmt
   yuv420p` fix — it should just open and play now instead of
   "unsupported encoding settings."
3. **While #1 is recording, watch the HUD kiosk screen** for the small
   red pulsing "REC" dot in the top-right masthead area, and confirm it
   disappears once the recording finishes.
4. **On a separate, later turn, say "Hey Atlas" while Atlas is mid-answer
   on something unrelated** (barge-in), and confirm it still interrupts
   normally. This is the regression check — `listen_for_barge_in`'s
   device loop changed for the first time ever this session, and a
   regression here wouldn't show up in #1-3.
5. *(Optional sanity check, not new this session)* **"Hey Atlas, take a
   picture of my screen."** — confirms the Session 4 screenshot routing
   fix is still solid after this session's restarts.

Report back in that order — which one(s) passed, which didn't, and
anything Atlas said, the clip sounded/looked like, or the HUD showed that
seemed off. That determines whether `phase4_screen_capture` gets flipped
to `live_verified` or needs another round. Once all of this passes,
Phase 11 (edit-and-post) is next — but that needs your answers on
Instagram credentials, audio licensing, and branding/caption style
first (see the checkpoint block and "What's left to fully close Phase 4"
item 4 above).

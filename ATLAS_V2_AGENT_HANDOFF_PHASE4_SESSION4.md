# A.T.L.A.S. V2/V3 — Phase 4, Session 4

> ## ⬛ CHECKPOINT — read this block first, every session
> **This is the newest handoff file.** Check the repo root for a newer
> `ATLAS_V2_AGENT_HANDOFF_*.md` by date first (`ls -lat *.md`) before
> trusting this one.
>
> - **Branch / HEAD:** `atlas-v2-agent` — HEAD is `e09cac4` ("fix:
>   implement missing shutdown_pc/cancel/recycle-bin companion actions").
>   `git log -1` to confirm it's still there.
> - **Phase / milestone:** Phase 4, milestone 1 (spoken-command tests,
>   see below) plus an out-of-band Phase 3 fix Wesley reported directly:
>   he asked Atlas to turn off his PC and it didn't work.
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

1. **Wesley needs to deploy the updated companion.** Copy
   `windows-companion/atlas_companion.py` to the real Windows PC (same
   path as before, e.g. `C:\atlas-companion\atlas_companion.py`) and
   restart it. This also picks up the earlier, still-undeployed
   `focus_or_open_app`/`active_window` fixes from Phase 3 milestone 1.
2. After deploying, live-verify by voice: "Hey Atlas, shut down my PC"
   (confirm the 60-second warning and that `shutdown /a` cancels it),
   then "empty the recycle bin" and a YouTube search command. Flip the
   `phase3_pc_companion` ledger's `external_blockers` once confirmed.

## What's left to fully close Phase 4

1. **Fix the mic-contention bug.** `watch_for_barge_in()` needs to
   release/pause its `arecord` hold on `plughw:CARD=Device,DEV=0` for the
   duration of any tool call that itself needs the mic (right now, only
   `camera_gate.capture_clip()`'s audio branch). Options worth weighing
   next session:
   - Have the agent runtime signal back to the turn handler before
     running an audio-capturing tool, so `barge_stop_event` can be set
     (killing the barge-in `arecord`) and restarted after — requires a
     way for a tool executing deep inside `run_atlas_agent` to reach the
     barge-in thread's stop event, which doesn't currently exist as a
     plumbed-through hook.
   - Or have `camera_gate.capture_clip()` retry with backoff and fall
     back to muted/video-only capture (it already supports
     `mute_audio=True`) if the device is busy after N attempts, so a
     recording still succeeds even without audio narration.
   - Whichever approach: reproduce live again afterward — say "Hey
     Atlas, record a 10 second clip of yourself" for real, don't just
     trust a unit test, since this bug is inherently about real-device
     contention that a mock won't catch.
2. Once self-recording-by-voice genuinely works end to end, flip
   `phase4_screen_capture` to `live_verified` in the ledger.
3. Add the HUD recording-state indicator (Phase 4's own spec still wants
   one — same `/screen`-flag pattern already used for dark-mode;
   `hud/app.js` / `robot_hub.py` untouched so far).
4. Then move to Phase 11 (edit pipeline) — the capture primitives it
   needs already exist and are proven end to end (screenshot confirmed
   live; self-recording blocked only by the mic-contention bug above,
   which Phase 11 will also need solved).

## Next session

Verify state first (`git log -1`, `./venv/bin/python -m pytest tests/ -q`
should show 652+ passing). Then tackle the mic-contention bug (item 1
above) — it's a real, reproducible, well-understood bug, not a mystery;
the fix just needs a live PC/mic test cycle to confirm, which costs
voice-turn round trips with Wesley same as any other live verification.
Same loop as always: graphify orientation (max 3 queries) → tests first
→ implement → full suite → `graphify update .` (only if source changed)
→ restart only affected services → live verify → commit exact paths →
update `implementation_ledger.py` honestly. Query the ledger by voice
with "what is your upgrade status".

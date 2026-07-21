# A.T.L.A.S. V2/V3 — Phase 4, Session 4

> ## ⬛ CHECKPOINT — read this block first, every session
> **This is the newest handoff file.** Check the repo root for a newer
> `ATLAS_V2_AGENT_HANDOFF_*.md` by date first (`ls -lat *.md`) before
> trusting this one.
>
> - **Branch / HEAD:** `atlas-v2-agent` — HEAD is `dc594e9` ("fix: stop
>   offering self_record_clip -- physical camera faces the room, not
>   Atlas"). `git log -1` to confirm it's still there. **674 tests
>   passing** (test count unchanged from iteration 5 — one test swapped
>   for its inverse).
> - **⏸️ LEFT OFF HERE (2026-07-21, iteration 6) — self-recording via the
>   physical camera is RETIRED, not fixed further.** Wesley live-tested
>   iteration 5's mic-coordination fix and confirmed (via journalctl) it
>   actually worked mechanically — no more "resource busy" fallback. But
>   the clip showed **Wesley**, not Atlas: the onboard USB camera
>   physically faces the room/desk, not himself. This is the exact "may
>   not be able to film Atlas itself depending on mounting position" risk
>   the original mission doc flagged in advance, now confirmed live twice
>   (the clip, and separate vision-command snapshots earlier in the same
>   session log). Wesley was explicit: **never use this camera for
>   self-showcase content again**; PC screen recording, or a genuine
>   future "his own" video source, are the only acceptable paths for
>   Instagram content. Removed `self_record_clip` from
>   `capabilities.REGISTRY` so voice no longer routes there — see "What
>   changed 2026-07-21, iteration 6" below for full detail, including why
>   the underlying tool/`mic_arbiter`/HUD indicator were left in place
>   rather than ripped out. Video-jump/audio-content quality symptoms on
>   that camera path were **not** debugged further — not worth chasing
>   artifacts on a capture source we just retired. **Still stopped before
>   Phase 11** (edit-and-post pipeline) — same blockers as before
>   (Instagram credentials, licensed audio, branding/caption style), now
>   with an added wrinkle: Wesley wants Instagram content specifically as
>   *screen recording + witty narrated conversation*, not raw self-video,
>   which actually simplifies Phase 11 now that the camera path is off
>   the table.
> - **Phase / milestone:** Phase 4, milestone 1 (spoken-command tests,
>   see below) plus an out-of-band Phase 3 fix Wesley reported directly:
>   he asked Atlas to turn off his PC and it didn't work.
> - **What changed 2026-07-21, iteration 6 (wrong-camera finding —
>   self-recording retired):** Wesley live-tested iteration 5's
>   mic-coordination fix. Checked journalctl for the actual test run
>   (22:48): confirmed `mic_arbiter` worked — no "resource busy" fallback
>   message this time, unlike an earlier 22:36 attempt that was still
>   running the old retry-only code. But Wesley reported the clip's video
>   jumped, its audio didn't capture cleanly, and — the important part —
>   **it showed him, not Atlas**. His onboard USB camera physically faces
>   the room/desk, not himself; confirmed independently by two unrelated
>   vision-command snapshots earlier in the *same* session log
>   ("a person leaning close to the camera in a kitchen", "a shirtless
>   person seated close to the camera"). This is the exact risk the
>   original mission doc called out in advance ("Atlas's onboard camera
>   may not be able to film Atlas itself depending on its mounting
>   position") — a hardware/placement fact, not a bug any of this
>   session's software fixes could have caused or can fix.
>   Wesley's instruction was explicit: never use this camera for
>   self-showcase content; PC screen recording, or a real future "his
>   own" video source, are the only acceptable capture paths for
>   Instagram content — where he wants Atlas doing a screen recording
>   with witty narrated conversation, not raw self-video. **Fix:**
>   removed `self_record_clip` from `capabilities.REGISTRY` so voice no
>   longer routes "record a clip of yourself" to `camera.capture_clip` at
>   all. **Deliberately did NOT rip out** the underlying tool,
>   `mic_arbiter.py`, or the HUD recording indicator — none of that is
>   actually broken, it's just pointed at the wrong physical source; all
>   of it is one registry line away from working again once the camera
>   is repositioned or a real "his own" (e.g. Pi HUD screen-video) source
>   exists. Quick recon done: no `wf-recorder`/`wl-recorder` installed on
>   the Pi, and the HUD kiosk runs under Cage (Wayland/wlroots), so
>   `ffmpeg`'s `x11grab` won't work directly for a future HUD-video
>   capability — that would need a package install and live testing
>   against the real kiosk session, not attempted this session. Did
>   *not* chase the video-jump/audio-quality symptoms further, since
>   that capture path is no longer voice-reachable. Updated
>   `tests/test_capabilities_registry.py` (one test inverted, not added)
>   — 674 passing, unchanged count. `graphify update .` run, `atlas-wake`
>   restarted clean. Committed as `dc594e9`.
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

1. ~~**Fix the mic-contention bug.**~~ **Mechanically fixed, but now
   moot for the moment.** Iteration 3's retry-and-fallback (`9030897`)
   was live-tested and, as suspected, always landed on the muted
   fallback. Iteration 5 (`a9ce869`) took the harder, originally-deferred
   approach: `mic_arbiter.py` coordinates an actual mic release between
   `capture_clip()` and `listen_for_barge_in()` — confirmed *mechanically
   working* via journalctl in iteration 6 (no more "resource busy"
   fallback). But iteration 6 also found the actual blocker was
   upstream of any of this: the physical camera faces the wrong
   direction, so `self_record_clip` is now removed from voice entirely
   (see item 4). `mic_arbiter` and the retry/fallback logic are still
   real, tested, working code — they'll matter again the moment
   self-recording (or any other audio-capturing tool) is voice-reachable
   again, whether that's a repositioned camera or a Pi HUD screen-video
   capability. **Regression check still worth doing:** on a live turn,
   interrupt Atlas mid-answer on something *unrelated* to recording
   (say "Hey Atlas" while he's talking) and confirm barge-in still works
   normally — `listen_for_barge_in`'s device loop changed for the first
   time ever this session and had zero prior test coverage, so this is
   the one regression risk left worth a live sanity check even though
   the recording path itself is now dormant.
2. ~~Once self-recording-by-voice is live-verified end to end, flip
   `phase4_screen_capture` to `live_verified`.~~ **Superseded** — see
   item 4; self-recording via the physical camera isn't the path forward
   anymore, so this specific milestone won't be the thing that flips the
   ledger. PC screen recording is already `live_verified` from Phase 4
   milestone 1.
3. ~~**Add the HUD recording-state indicator.**~~ **Built and confirmed
   working (iteration 4), now dormant.** New `/hud/recording` flag
   endpoint (same `/screen`-flag pattern as dark mode), `recording_active`
   in `GET /state`, a `recording-active` body class toggled by
   `hud/app.js`, and a pulsing red "REC" dot in the masthead
   (`hud/style.css`) — Wesley confirmed live it actually shows up on the
   physical kiosk. Wired into `capture_self_clip` in
   `atlas_agent/pi_tools.py`, which is no longer voice-reachable as of
   iteration 6 (see item 1/4), so the dot currently has nothing to fire
   it — not broken, just unused until self-recording (physical camera or
   a future HUD-video source) is voice-reachable again. **Not** wired to
   PC screen recording (`pc.start_screen_recording`), which would need
   separate design for polling PC state back to the Pi HUD; worth
   revisiting once Phase 11 actually needs a recording indicator for
   screen-record content instead.
4. **Phase 11 (edit pipeline) — deliberately not started.** This is where
   this session stopped. Wesley clarified the actual product goal this
   session: Instagram content should be a **screen recording with witty
   narrated conversation**, not raw self-video via camera — which
   conveniently simplifies this list, since the self-video capture
   question (item 1) is now moot. It still needs decisions only Wesley
   can make before any code gets written:
   - **Instagram credentials/OAuth.** No API access exists yet at all.
   - **Licensed background audio** — only relevant if music/background
     audio is wanted under the narration; Atlas's own spoken narration
     itself isn't a licensing question. Can't just grab something off
     the internet if music is wanted — copyright risk.
   - **Branding/caption style.** Logo overlay? Watermark? Caption tone?
     Nothing defined yet.
   - **Which editing approach.** Deterministic FFmpeg filter chains vs.
     something higher-level — not evaluated this session.
   - **Where the narration audio comes from and how it syncs to the
     screen recording** — Atlas's TTS plays on the Pi's speakers while
     the screen recording happens on the PC, two separate machines; the
     narration audio needs its own capture/transfer path to mux with the
     PC video during editing. Not designed yet.
   - The pipeline's last step is a **real public Instagram post**, which
     the safety model (see the main mission doc, "Safety and Authority
     Model") explicitly requires confirming the exact media and caption
     with Wesley before ever sending — this isn't a capability to just
     build silently and gate behind a runtime confirmation prompt later;
     the credentials and content-source decisions above have to happen
     with Wesley first.
   The capture primitive Phase 11 will actually use is already proven
   end to end (PC screenshot/screen-recording confirmed live 2026-07-20).
   Pi self-recording via camera is explicitly off the table (item 1).

## Next session

Verify state first (`git log -1`, `./venv/bin/python -m pytest tests/ -q`
should show 674+ passing). What's left is one live regression check (see
the checklist below) and then, only after Wesley weighs in on the
Phase 11 questions above, starting the edit pipeline. Same loop as always:
graphify orientation (max 3 queries) → tests first → implement → full
suite → `graphify update .` (only if source changed) → restart only
affected services → live verify → commit exact paths → update
`implementation_ledger.py` honestly. Query the ledger by voice with
"what is your upgrade status".

## What Wesley needs to test (in order)

Say/do each of these for real, one at a time, and report back what
actually happened in this same order:

1. **"Hey Atlas, record a 10 second clip of yourself."** Should now get
   an honest refusal or a redirect toward PC screen recording, instead of
   actually using the camera — confirms `self_record_clip` is really off.
2. **On a separate, later turn, say "Hey Atlas" while Atlas is mid-answer
   on something unrelated** (normal barge-in, nothing to do with
   recording), and confirm it still interrupts normally. This is the one
   regression risk left from this session — `listen_for_barge_in`'s
   device loop changed for the first time ever and had zero prior test
   coverage, so even though nothing currently triggers the
   mic-yield path in practice, the loop itself runs on every barge-in
   check now and deserves a sanity check.
3. *(Optional sanity check, not new this session)* **"Hey Atlas, take a
   picture of my screen."** — confirms the Session 4 screenshot routing
   fix is still solid after this session's restarts.

Report back in that order. Once #1 and #2 pass, this session's work is
fully closed out. Next up is Phase 11 (edit-and-post) — but that needs
your answers first: Instagram credentials, whether you want background
music under the narration (and if so, from where), branding/caption
style, and how narration audio should get from the Pi to the PC to sync
with the screen recording (see "What's left to fully close Phase 4" item
4 above for the full list).

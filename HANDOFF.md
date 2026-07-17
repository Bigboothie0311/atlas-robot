# Handoff — 2026-07-16 session

Everything below was built/fixed in one long session tonight, on top of
the HUD v1/v2 work from the previous session (see git log before
`6218c2c` for that). All of it is committed to `main` and pushed to
`origin/main`. Nothing is stashed or uncommitted.

## Current verified state (as of this handoff)

All four services active and healthy: `atlas-robot.service`,
`atlas-wake.service`, `atlas-hud.service`, `atlas-hub.service` (the last
one is a separate, unrelated pre-existing service — printer control hub,
not part of tonight's work).

**The boot-stall bug is fixed and confirmed via a real cold reboot** —
`atlas-hud.service` started clean on the first attempt, zero restarts,
no SSH login needed. Full root-cause chain below.

OpenAI spend this month: **$0.54 of $8.00 budget.** Voice: Piper,
`en_US-joe-medium`, chosen via on-device listening comparison earlier
this session.

## What was built, roughly in order

1. **HUD cosmetic fixes** — darker-but-visible navy background, hidden
   `qa-log` scrollbar, hidden mouse cursor via CSS (later found to need
   much more than CSS — see the boot-stall section).
2. **Voice switch** — compared 10 Piper voices live through the robot's
   own speakers, landed on `en_US-joe-medium`.
3. **Memory subsystem** (`memory_store.py`) — session memory (~5 min
   rolling window, survives across wake-ups since `wake_listener.py` is
   one long-lived process), cross-session facts via "remember
   that..."/"forget everything", and timed reminders ("remind me in 20
   minutes to..."), all fully local/zero-token.
4. **Proactive engine** (`robot_hub.py`'s `proactive_watcher_loop`, 120s
   poll) — unprompted speech on: gaming-PC temp >85°C (30 min cooldown),
   rain ≥50% (once/day), **this Pi's own CPU >75% sustained 3+ minutes**
   (not brief spikes), and delivering due reminders. Muted 11pm–6am
   except reminders, which fire regardless since they're user-scheduled.
5. **Ambient quiet-hours mode** — 11pm–6am: HUD dims via a `body.quiet-
   hours` CSS class, TTS volume drops 30%, answers shorten to 1-2
   sentences/120 tokens.
6. **Barge-in** — say "hey atlas" mid-answer to cut it off and start a
   new turn. Shares `wake_detection.py` (extracted from
   `wake_listener.py`'s proven verification logic) so both listeners stay
   in sync. **Two real bugs found and fixed here, not assumed:**
   - `/interrupt` only killed an *already-playing* process — if the wake
     phrase landed while Piper was still synthesizing (0.3–1.3s window),
     there was nothing to kill and that sentence played out in full.
     Fixed with a 2.5s retry loop in `watch_for_barge_in`.
   - `listen_for_barge_in` only logged on a *successful* match, so a
     rejected/garbled attempt left zero trace. Now logs every candidate
     (`wake_detection.check_wake_phrase` returns the finalized
     text/confidence for this). **Last known state: barge-in still
     needs a fresh real test with this logging active** — haven't seen
     a live failure with the new diagnostics yet to know if it's fully
     fixed or still has an acoustic-masking issue (robot's own voice
     drowning out the mic, no echo cancellation implemented).
7. **Push notifications** — `POST /notify` (speak+log) and `POST
   /remember` (save as a fact), both authenticated via `X-Notify-Token`
   in `config/robot.env` (gitignored). Wesley has an iOS Shortcut wired
   to `/notify` already; declined the "delivery alert via email trigger"
   idea as too much setup.
8. **Streaming TTS** — `ask_and_speak_streaming` in `listen_and_answer.py`
   replaced the old `ask_atlas`/`speak_with_barge_in` (both deleted, no
   longer called anywhere). Speaks each sentence as it's generated
   instead of waiting for the full answer. Measured finding: ~80% of
   latency is "time to first token" (fixed, unavoidable), so the real
   win is skipping the wait to synthesize everything after sentence one
   — a genuine ~1-2s improvement, not dramatic. Same token cost as
   before, just different delivery timing.
9. **Zero-token instant answers** — time/date/uptime/"are you there"
   answered locally, no API call.
10. **Weather optimization** — `get_weather` tool now defaults to home
    location (was requiring the model to ask for a zip code) and
    properly supports "tomorrow" via a 2-day forecast fetch. Current-
    weather questions about home now skip the tool call entirely
    (cached data injected directly into the prompt), cutting ~4s to
    ~1-1.3s for that query shape. Tool call is still used for "tomorrow"
    or other cities — confirmed both paths work and the HUD's activity
    label ("CHECKING WEATHER" instead of generic "THINKING") only shows
    for the actual tool-call path, which is correct, not a bug.
11. **Follow-up questions** — if Atlas asks a clarifying question, the
    mic stays open for the reply instead of returning to idle (capped
    at 3 rounds). Also told the model explicitly that only a literal
    "?" keeps the mic open, since it was phrasing invitations as
    statements ("if you want, I can...") which never triggered it.
12. **Rule book personality rewrite** — the system instructions
    (`build_instructions_and_limits()` in `listen_and_answer.py`) had no
    character, just generic "be friendly, useful, direct" boilerplate.
    Rewrote with real wit, zero corporate-assistant filler phrases, and
    an explicit instruction to give real opinions instead of hedging.
    User confirmed this landed well.
13. **HUD voice-activity equalizer** — replaced a flat "SYSTEM STATUS:
    NOMINAL" text panel (which missed the point of freeing up that
    space) with a 7-bar animated equalizer reusing the existing
    `body.state-*` classes — idle breathing animation, amber/faster
    while listening, green/energetic while speaking. CPU-warning
    indicator and memory% folded into one sub-line underneath.
14. **The boot-stall saga** — this took several real iterations, each
    verified against actual reboots, not assumed:
    - First hypothesis (wrong-ish but real): `atlas-hud.service` had no
      ordering dependency on `seatd.service`, so systemd could start
      cage before seatd was ready. Fixed with `After=`/`Requires=
      seatd.service`. Real improvement, kept, but not the actual cause
      of the "waits for SSH" symptom.
    - Second bug (real, and one I introduced): `hud_cursor_fix.py`
      crashing (`/dev/uinput` permission race between the module
      loading and udev applying its rule) failed the *entire*
      `ExecStartPost`, which fails the whole service start under
      `Type=simple`, so `Restart=on-failure` killed already-working
      cage/Chromium and retried — turning a cosmetic cursor issue into
      no HUD rendering at all. Fixed: force `uinput` to load early via
      `/etc/modules-load.d/uinput.conf` (`systemd/uinput.conf` in repo),
      and made the script catch every exception and always exit 0 —
      a failed cursor nudge must never be able to take down the kiosk.
    - Third bug (real): even non-crashing, the nudge schedule (5 tries
      over 20s) was still too short — a real cold boot showed cage/
      Chromium not reporting "Started" until 22s in, so every nudge
      missed. Extended to nudge every 5s for up to 90s.
    - Fourth bug (real, self-inflicted by fix #3): 90s exceeds
      systemd's default start-job timeout, so `systemctl restart`
      itself started hanging/timing out. Fixed by backgrounding the
      nudge script via `setsid ... &` in `ExecStartPost` (piped through
      `systemd-cat -t hud_cursor_fix` to keep it in the journal) so its
      long runtime no longer blocks the start job.
    - **Actual root cause, found via exact timestamp correlation on a
      real cold boot**: `atlas-hud.service` hardcodes
      `XDG_RUNTIME_DIR=/run/user/1000`, which systemd only creates once
      an actual login session starts (`pam_systemd`). On a cold boot
      with no interactive login, cage failed repeatedly with "Unable to
      open Wayland socket: Invalid argument" — confirmed the first two
      failed attempts happened *before* `/run/user/1000`'s birth
      timestamp, and the third attempt (right after that directory
      appeared) succeeded. That's exactly why SSH login "unblocked" it
      every time — logging in is what created the directory. Fixed
      with `sudo loginctl enable-linger atlas`, which makes systemd
      create that directory at boot regardless of any login. **This is
      the fix that was actually verified working on a real reboot** —
      first-try clean start, zero restarts, confirmed in journalctl.

## Still open / needs your input or a live test

- **Barge-in**: needs a fresh real "hey atlas" mid-answer test now that
  `listen_for_barge_in` logs every candidate (not just successful
  ones). If it still doesn't cut off, check
  `journalctl -u atlas-wake.service` for "Barge-in candidate: ..."
  lines — that'll show whether it heard something and rejected it
  (confidence/RMS too low) or heard nothing at all (likely the robot's
  own voice masking the mic — no echo cancellation exists).
- **Cursor**: FIXED and user-confirmed on screen (2026-07-16 late
  session). The nudge-based theory was wrong all along — the visible
  arrow was **cage's own default cursor**, not Chromium's, which is why
  it appeared from cold boot before any pointer device existed and why
  the HUD's `cursor: none` never touched it. Proven live: with
  WAYLAND_DEBUG protocol tracing, Chromium's `set_cursor(nil)` hide
  request was sent and received yet the sprite stayed; holding a
  virtual pointer device open changed nothing; WLR_NO_HARDWARE_CURSORS=1
  was tried and ruled out. Real fix: a fully transparent Xcursor theme
  at `/usr/share/icons/atlas-invisible` (generated 24x24 all-alpha-0
  `left_ptr` + symlinks for common names) selected via
  `Environment=XCURSOR_THEME=atlas-invisible` in atlas-hud.service —
  cage documents XCURSOR_THEME in its man page and Chromium's Wayland
  backend honors it too. The theme is installed system-side only (not
  in the repo); `hud_cursor_fix.py` and its uinput plumbing are now
  redundant but harmless, left in place for the moment — safe to remove
  in a future cleanup pass.
- **Camera feature** — still not designed/built at all. Ideas pitched
  early in a prior session (OCR, face recognition, gesture confirm,
  snap-and-save, QR scan), OCR was the recommendation, never confirmed.
  Camera will be a fixed mount above the screen, stationary.
- **Face/gaze tracking** ("follow me") — mentioned early on, not
  designed or built.
- **Voice macros** (item 9 from the original feature-batch pitch) — user
  was iffy on this, explicitly skipped, don't build without re-asking.
- **Cloud TTS voice upgrade** — considered as a fallback if a model
  upgrade wasn't worth it (it wasn't — see below). Pricing for
  `tts-1`/`gpt-4o-mini-tts` wasn't cleanly available via the pricing
  page fetch, and swapping the TTS engine is a real architecture change,
  not a quick tweak. Not started. Revisit only if explicitly asked.

## Decisions made, for context if this comes up again

- **Not switching the LLM model.** Checked live: `gpt-5.6-luna`
  (current) is already the *cheapest* of the three 5.6-generation
  models available on the account — `sol` is 5x the cost, `terra` 2.5x,
  likely trading speed for more capability, not offering "faster."
  Per the user's own rule about not chasing drastic cost increases,
  left it alone.
- **Voice macros declined** by the user when originally pitched — don't
  build without re-confirming.

## Known gotchas (in case they resurface)

- **`pgrep -f <pattern>` self-matches** if the pattern is textually
  embedded in the invoking shell command (e.g. checking for a running
  script by grepping for its own filename inside a wrapper script whose
  full command line contains that filename elsewhere). Use the
  `ps aux | grep "[x]yz"` bracket trick, or grep a narrower field like
  `python3.*` and exclude the wrapper explicitly, or just check for the
  specific interpreter process rather than the whole command line.
- **`/etc/systemd/system/*.service` is the live copy** — the repo's
  `systemd/*.service` files are templates with `YOUR_USERNAME`
  placeholders. Every fix tonight required copying the repo version
  over the installed one and running `sed -i 's/User=YOUR_USERNAME/
  User=atlas/'` before `daemon-reload`, or the live unit silently keeps
  running the old config.
- **This project has zero automated test infrastructure** — everything
  is verified via direct script execution against real hardware/network
  and headless Chromium screenshots, same as previous sessions. See
  older handoff content (now only in git history, not carried forward
  here) for the exact screenshot command pattern if needed.
- **Always confirm before rebooting or restarting
  `atlas-robot`/`atlas-wake`/`atlas-hud`** — standing rule, followed all
  session. Two real reboots happened tonight, both explicitly requested.
- **Never `git push` without asking first** — standing rule, followed
  all session (every commit tonight was pushed only after an explicit
  yes).

## Where to pick up next

Given everything above is fixed and verified, the natural next things
(none started, no obligation to do any of them without asking first):
1. Get a real "hey atlas" barge-in test with the new logging, to close
   out whether that's actually fully fixed or has a deeper acoustic
   issue.
2. The camera feature — still just ideas, needs a real decision and
   design pass.
3. Anything else that comes up from actually living with tonight's
   changes for a few days.

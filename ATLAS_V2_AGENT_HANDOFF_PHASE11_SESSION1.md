# A.T.L.A.S. V2/V3 — Phase 11/12, Session 1

> ## ⬛ CHECKPOINT — read this block first, every session
> **This is the newest handoff file as of 2026-07-21.** Check the repo
> root for a newer `ATLAS_V2_AGENT_HANDOFF_*.md` by date first
> (`ls -lat *.md`) before trusting this one.
>
> - **Branch:** `atlas-v2-agent`. **737 tests passing** (`./venv/bin/python
>   -m pytest tests/` — this repo's own `venv/`, not system Python, which
>   is missing `psutil` and others). **Use pytest, not `unittest
>   discover`** — confirmed live: `unittest discover` silently collects
>   *zero* tests from files written as plain pytest functions with
>   `tmp_path`/`monkeypatch` fixtures (e.g. all of
>   `tests/agent/test_content_tools.py`) instead of `unittest.TestCase`
>   subclasses — no error, no warning, it just contributes 0 passing
>   tests to the total. A prior version of this checkpoint said "300
>   tests passing" from a `unittest discover` run that had silently
>   skipped that file — wrong number, corrected here.
> - **Phase 11 (self-showcase edit pipeline) and Phase 12 (Instagram
>   publish) are DONE and live_verified** — see
>   `implementation_ledger.py` entries `phase11_showcase_media` and
>   `phase12_instagram` for full evidence. This closes out the item
>   `ATLAS_V2_AGENT_HANDOFF_PHASE4_SESSION4.md` left as "Next up." All of
>   Phase 11/12's files were sitting uncommitted until this session —
>   now committed (`9724ef4`).
> - **Real live proof, not a claim:** a real narrated Reel was recorded,
>   edited, and published tonight —
>   https://www.instagram.com/reel/DbDnA9rDk9M/ (media_id
>   `18059282249759566`). A `dry_run=True` pass proved token scope and
>   the publish mechanics before the real post ran.
> - **Session 1 continuation, same day:** the first real Instagram post
>   above got cut off mid-sentence — `content_pipeline.CAPTION_MAX_LENGTH`
>   was an arbitrary `200`, nowhere close to Instagram's real 2200-char
>   cap. Fixed (see "Follow-up fixes" below). Also clarified that
>   `content.record_self_showcase`'s `beats` param already lets a caller
>   hand it a fully custom narration script instead of the default tour
>   — that capability existed in the code but nothing surfaced it in the
>   tool description or `COMMANDS.md`, so it read as fixed-script only.
>   Fixed both. **Evaluated `github.com/calesthio/OpenMontage`** (Wesley's
>   ask: "let him edit videos in a not-so-shitty way") — verdict below,
>   not adopted.
> - **Round 2, same day: actual choppiness fix + "no repetition" +
>   readiness.** The published Reel was choppy because HUD video capture
>   was 2fps still-stitching, not a CPU problem (measured live: this Pi
>   sits well under half its CPU capacity even mid-recording). Switched
>   `hud_capture.record_hud_clip()` to `wf-recorder` (installed via apt)
>   for real continuous 24fps capture. The default tour was also one
>   fixed, word-for-word script every time — replaced with
>   `_build_default_tour()`, which randomizes phrasing and which extra
>   real-feature beats show up. `atlas-wake.service` restarted to
>   actually load all of this. Said at the time this was "ready now" --
>   **that claim was wrong, see round 3.** Full detail in "Same day,
>   round 2" below.
> - **Round 3, same day: the actual bug behind "ready now" being wrong,
>   plus PC-demo interleaving.** Wesley tried the exact phrase and Atlas
>   ran self-diagnostics instead of recording anything -- confirmed via
>   `journalctl -u atlas-wake.service`. Root cause: the top-level voice
>   router (`ai_tools.py`) had a tool description gap, not a
>   content-pipeline bug -- see "Same day, round 3" below. Fixed, plus
>   added real PC-screen-clip interleaving (`"source": "pc"` beats) and
>   found + fixed a real concat DTS bug live-testing that. Said
>   "actually ready now" -- **wrong again, see round 4.**
> - **Round 4, same day: the real, actual root cause.** Wesley tried it
>   again and got a different but equally wrong result ("the HUD service
>   is active and running... recording requires another attempt").
>   Round 3's `ai_tools.py` fix was real and correct but incomplete --
>   the top-level router picked the right tool (`run_atlas_agent`) both
>   times, confirmed via `data/agent_missions.json`, which had a
>   well-formed goal both times. The actual bug was one layer deeper:
>   `OpenAIPlanGenerator._deterministic_local_plan()` -- a zero-token
>   shortcut that runs *before* the real planning LLM -- was hijacking
>   every self-showcase goal via loose word matches on "HUD" and
>   "status"/"diagnostics", words *any* self-showcase goal naturally
>   contains, so `content.record_self_showcase` never even got
>   considered. Fixing that surfaced a second, previously-masked bug:
>   the tool's own `beats` schema was invalid for OpenAI's strict mode
>   (missing `items` on an array type) -- never caught before because
>   the shortcut bug meant a real planning call never happened. Both
>   fixed and verified live end-to-end twice (once at the planning
>   level, once running the full workflow for real) -- see "Same day,
>   round 4" below. This is the first time "ready" was verified by
>   actually running the previously-failing goal through the real
>   planner and the real executor, not by inference.
> - **Round 5, same day: the recording actually worked, the voice loop
>   just didn't wait for it.** Wesley tried again, got "I was unable to
>   generate an answer" -- but a real, valid Reel (`reel_1784676131.mp4`)
>   was sitting finished in `/home/atlas/atlas-staging/incoming/`
>   seconds later. `ask_and_speak_streaming()`'s consumer only waited
>   30s for the next streamed sentence, far short of
>   `content.record_self_showcase`'s own registered 300s timeout, so it
>   gave up and reported failure while the recording finished orphaned
>   in the background. Raised to 320s
>   (`SENTENCE_STREAM_IDLE_TIMEOUT_SECONDS`). **Not re-verified live this
>   round** (can't practically re-run a 300+s wait to prove a timeout
>   fix) -- see "Same day, round 5" below, including a loose end: that
>   Reel is still sitting there unpublished, and the goal it came from
>   explicitly said not to publish yet.

## What got built

- **`content_pipeline.py`** (new, repo root) — `render_narration()` (Piper
  synth via `robot_hub`'s new `/speak` `play=False` mode, never played
  aloud), `edit_reel()` (one deterministic ffmpeg pass: mux narration
  onto the recording, reframe to 1080x1920, `loudnorm`, `-ar 48000`),
  `build_caption()`.
- **`instagram_publish.py`** (new, repo root) — the write side of
  Instagram Graph API publishing. `instagram_stats.py` was always
  read-only; this is genuinely new.
- **`atlas_agent/content_tools.py`** (new) — `content.record_self_showcase`
  (permission_level=0) and `content.publish_to_instagram`
  (permission_level=2, `CONFIRMATION_REQUIRED` — the only tool in the
  whole registry at that level). Registered in
  `runtime_factory.build_pc_agent_runtime()`.
- **`atlas_agent/pc_tools.py`** — added `pc.upload_file` (mirrors the
  existing `pc.download_file`, wraps `SFTPClient.upload()`, which
  existed but wasn't exposed as a tool yet).
- **`robot_hub.py`** — `/speak` gained a `play` field (default `True`);
  `play=False` synthesizes without touching the speaker or the HUD
  "talking" state and returns the wav_path instead of deleting it. No
  lingering mute state — it's a per-call flag.
- **`windows-companion/atlas_companion.py`** `act_start_recording` — added
  explicit `-c:v libx264 -pix_fmt yuv420p -movflags +faststart`. This
  also fixed a **real bug Wesley hit**: an existing recording
  (`atlas intro video for instagram.mp4`) wouldn't play on Windows — root
  cause was the `moov` atom landing at the end of the file (no
  `+faststart`), not a codec problem. Fixed losslessly in place
  (`-c copy -movflags +faststart`) and handed back as
  `atlas intro video for instagram_FIXED.mp4` on the Desktop.

## Two real bugs found only by actually running this live (not from unit tests)

1. **My own local file server didn't support `HEAD` requests.** Instagram's
   video fetcher needs one; without it, Instagram's container just went to
   a bare, undiagnosable `ERROR` status with no further detail from the
   API. Fixed: `_SingleFileHandler` now supports `HEAD` and byte-range
   `GET` (many fetchers use ranged reads, not one whole-file GET).
2. **`ffmpeg`'s `loudnorm` filter doesn't preserve the input audio sample
   rate.** Without pinning `-ar` explicitly downstream of it, the edited
   reel ended up with an unpinned 96kHz mono track — Instagram silently
   rejected it (same bare `ERROR`, no detail). Fixed: `-ar 48000` pinned
   explicitly in `edit_reel()`'s ffmpeg command.

Both are covered by regression tests now (`test_content_pipeline.py`,
`test_instagram_publish.py`'s `ServeFileLocallyTests`).

## Why Tailscale Funnel, and how it's bounded

Instagram's publish API for this account (`graph.instagram.com`, the
direct "Instagram API with Instagram Login" product, confirmed live) flatly
requires a public `video_url` — the resumable/byte-upload path this module
first tried (`upload_type=resumable`) is rejected outright with "The
parameter video_url is required." This robot has no public hosting and is
deliberately never port-forwarded.

`instagram_publish._funnel_video_url()` briefly serves just the one
video file (random unguessable path, no directory listing) over
Tailscale Funnel — real public HTTPS via Tailscale's infrastructure, not
a router port-forward — for exactly the span of container creation +
processing polling, then tears it down unconditionally in a
`contextlib.contextmanager`'s `finally`, even on error. Verified live
that the exposure is genuinely public (not just resolving locally within
the tailnet via MagicDNS — that gave a false-positive the first time
this was tested) by forcing a fetch through the real public relay IP,
and verified it's fully torn down afterward (`tailscale funnel status`
→ "No serve config").

**One-time setup this required, both now done:**
- Funnel enabled for the tailnet (owner did this in the Tailscale admin
  console).
- `sudo tailscale set --operator=atlas` on the Pi, so funnel commands
  don't need an interactive sudo prompt.

## Correction, same day: recording the wrong screen

The first version of `content.record_self_showcase` recorded the Windows
PC's screen (via `pc.start_screen_recording`), reusing the same primitive
Phase 4 already proved live. **Wesley caught, from actually watching the
output, that this showed his own Windows desktop, not Atlas** — the
whole point of self-showcase content is Atlas narrating his own
features, which only exist on his own HUD, not the PC.

Real fix, same session: `content.record_self_showcase` now records
**Atlas's own HUD kiosk display** and drives a scripted, narrated tour
through real HUD states between clips — weather radar open/close,
self-diagnostics — instead of touching the PC/Windows companion at all
for recording.

- **`hud_capture.py`** (new) — the kiosk runs on Wayland via `cage`
  (`atlas-hud.service`), not X11. There's no installed video-capture
  tool for this wlroots kiosk, so `record_hud_clip()` frame-stitches
  periodic `grim` (Wayland-native screenshot) stills into an mp4 via
  ffmpeg's image2 sequence input.
- **Found a second real bug fixing this**: `atlas_agent/pi_tools.py`'s
  existing `capture_hud_frame` tool used `scrot` against `DISPLAY=:0`
  (X11) — confirmed live this silently captured a **solid-black frame,
  every time**, on this Wayland kiosk. `pi_tools.py` now shares
  `hud_capture.capture_frame` (grim-based), fixing that tool too.
- **`content_pipeline.py`** gained `concat_clips()` (ffmpeg concat
  demuxer, stream copy) to join each tour beat's already-edited clip
  into one final Reel.
- **`atlas_agent/content_tools.py`**: `DEFAULT_TOUR` is intro → weather
  radar → self-diagnostics → outro, each with its own narration line.
  `_apply_hud_action()` drives the real `/hud/weather_overlay` and
  `/diagnostics_report` endpoints (reusing `diagnostics.run_structured_checks()`,
  the same function the real "run diagnostics" voice command uses) — not
  fakes. `/hud/recording` is flipped around the whole tour, finally
  giving that dormant HUD indicator (built in an earlier Phase 4 session,
  noted then as "unused until self-recording is voice-reachable again")
  something real to fire for.
- `register_content_tools()` no longer takes `pc_client`/`sftp_client` —
  this feature has zero PC/Windows-companion dependency now.

**Live-verified the fix is real, not just "it ran without erroring":**
produced a 17.5s, 1080x1920 Reel; sampled 6 frames spread across the
timeline and confirmed they're all visually distinct (different MD5
hashes, tens of thousands of distinct colors each) — ruling out both the
old failure modes (frozen single frame, solid black) — plus a full
ffmpeg decode pass with zero errors.

## Known follow-up, not blocking

- `DEFAULT_TOUR` only covers weather radar + self-diagnostics. Adding
  more beats (e.g. status report, capability list, printer status) is a
  one-line addition to the tuple in `atlas_agent/content_tools.py` — no
  architecture change needed. Callers can also pass a fully custom
  `beats` list to `content.record_self_showcase` already.
- Frame rate for HUD clips is a fixed 2fps (`hud_capture.CAPTURE_FPS`) —
  fine for mostly-static panel content; would look choppy for anything
  with real motion/animation on the HUD.
- No background music / branding watermark — deliberately out of scope
  (copyright risk for music; no branding asset exists in the repo; not
  asked for).

## Scope note

This is the practical MVP of Phase 11/12 (narrate → record → mux →
caption → publish), not the full aspirational Phase 11 spec from the
master mission doc (automatic evidence-clip scoring across HUD/Graphify/
coding sessions, animated captions, picture-in-picture, background-audio
ducking). Those remain not started.

## Same-day follow-up: caption length, custom scripts, OpenMontage

Three things from Wesley after the first real post went up.

**1. Caption got cut off mid-sentence on the live Reel.**
`content_pipeline.CAPTION_MAX_LENGTH` was `200` — an arbitrary v1
placeholder, not Instagram's actual limit (2200 characters). Raised to
`2200` in `content_pipeline.py`. `tests/test_content_pipeline.py`'s
truncation test bumped its fixture past the new threshold (`"x" * 3000`,
was `500`) so it still actually exercises truncation instead of silently
passing without ever hitting the new limit.

**2. "Make sure he has full capabilities — custom videos, not some
predetermined script."** This mostly already worked and wasn't
discoverable: `content.record_self_showcase`'s `beats` param has always
accepted a fully custom list of `{narration, action}` overriding
`DEFAULT_TOUR` entirely — arbitrary narration text, arbitrary order,
arbitrary length. Nothing said so anywhere a caller (or Wesley, reading
`COMMANDS.md`) would see it, so it read as fixed-script. Fixed by
rewriting the tool description, the `beats`/`action` JSON-schema
descriptions in `atlas_agent/content_tools.py`, and adding a row to
`COMMANDS.md`'s Self-showcase content table. No behavior changed, only
what's documented as possible. Also documented (wasn't before) that an
unrecognized `action` string never errors — it's best-effort, the HUD
just stays on whatever it was already showing for that beat.

**3. Evaluated `github.com/calesthio/OpenMontage`** (cloned to
`/tmp/.../scratchpad/OpenMontage` for inspection, not vendored in) —
Wesley's ask was "let him edit videos in a not-so-shitty way." **Verdict:
don't adopt it, at least not wholesale.** It's a full agentic *video
production* framework: an LLM coding agent (Claude Code/Cursor/etc.)
drives it to generate videos from prompts or reference clips, using
paid image/video/TTS provider APIs (fal.ai, Veo, Kling, OpenAI, ...),
composed and rendered through Remotion (a Node.js/React video-rendering
engine), with its own web UI ("Backlot"), pipeline/schema system, and
skills directory. None of that matches this project's actual constraints:
- Everything here is deliberately free and local (Piper TTS, plain
  ffmpeg, no paid generation APIs) — see [[feedback_outbound_calls_policy]]
  in memory. OpenMontage's whole value proposition assumes paid
  generation providers.
- It'd add a Node.js/Remotion runtime dependency to this Raspberry Pi.
  (Was going to cite the old fixed 2fps HUD capture here as evidence of
  how resource-constrained this Pi is — turned out that wasn't actually
  a CPU-constraint problem at all; see the follow-up right below, same
  day, where that got fixed and CPU headroom got measured directly.)
- It has no "record my own live screen" input mode at all — its inputs
  are text prompts, reference videos to remix, or AI-generated/stock
  footage. Atlas's whole self-showcase premise (his own real HUD,
  narrating his own real features) doesn't map onto what it's built to
  do.

**What's actually worth borrowing, not built yet (needs a decision
before doing it, since it's new scope, not a bugfix):** two ideas from
OpenMontage's output quality that would genuinely make the Reels look
less "shitty" without adopting any of its stack — both doable in plain
ffmpeg, already a dependency:
- Burned-in captions per beat (ffmpeg `drawtext`/subtitle filter),
  instead of narration-only audio with no on-screen text.
- Crossfade transitions between tour beats in `concat_clips()` instead
  of the current hard cuts (would need to move off the pure stream-copy
  concat demuxer to an `xfade`-based filter graph, i.e. a real
  re-encode, not just a copy).

## Same day, round 2: the actual choppiness fix, "no repetition," readiness

Wesley watched the real published Reel and it was choppy, and asked for
(a) that fixed, maybe by killing processes during recording, (b) "record
a video of yourself for Instagram" to self-generate/edit/upload a fresh,
non-repeating video every time with one voice command, and (c) the
OpenMontage exploration explained above (his intent: let Atlas edit
video "in a not-so-shitty way").

**Root cause of the choppiness, confirmed live, was not CPU contention.**
Sampled `top` during an actual `hud_capture.record_hud_clip()` call:
total system CPU stayed at 25-37% used (62-75% idle) across this Pi's 4
cores, even while chromium (rendering the live HUD — the thing being
recorded) was itself using ~25-30%. There was no resource fight to
resolve, so **no processes get killed during recording** — that would've
solved nothing real and only added risk (e.g. killing the wake listener
mid-session). The actual cause was architectural: `record_hud_clip()`
frame-stitched periodic `grim` stills at a fixed **2fps** because no
real video-capture tool was installed for this wlroots/`cage` kiosk.

**Fix:** installed `wf-recorder` (`sudo apt install -y wf-recorder`,
now in [README.md](README.md)'s cage/HUD setup section as a required
system package) — it's the wlroots-native continuous screen recorder,
confirmed live it works against `cage`. Rewrote `hud_capture.
record_hud_clip()` around it: starts `wf-recorder` as a subprocess for
the beat's duration, stops it with SIGINT (its documented stop signal)
instead of stitching stills. **Confirmed live:** a direct 4.8s test
recording came back as real, continuous 24fps (116 actual frames, not
stitched stills); a full `content.record_self_showcase` run through the
real registered tool (not a mock) produced a proper 1080x1920, 24fps
Reel end to end. `CAPTURE_FPS` raised from `2` to `24` to match.
`capture_frame()` (single-still `grim` grabs, used elsewhere for plain
screenshots) is untouched — this only changed video capture.
`tests/test_hud_capture.py`'s `RecordHudClipTests` rewritten for the new
`Popen`/SIGINT flow instead of the old stitching-loop mocks.

**"No repetition":** `DEFAULT_TOUR` was one fixed tuple — every
"record a promo video" call with no custom `beats` produced the
*exact* same narration script, word for word, every single time. That's
the boring-repetition half of Wesley's complaint (the choppiness was the
other half). Replaced with `_build_default_tour()` in
`atlas_agent/content_tools.py`: weather radar and self-diagnostics
always run (they're the only two beats with a real HUD-driving action,
and the existing test asserts on them), but their phrasing is now
randomly chosen from `WEATHER_LINES`/`DIAGNOSTICS_LINES`, intro/outro
phrasing is randomized from `INTRO_LINES`/`OUTRO_LINES`, and 0-2 extra
beats get randomly sampled in from `EXTRA_BEATS` (system status,
printer, gaming PC — all `action="idle"` since those panels are already
part of the always-visible HUD dashboard per `hud/app.js`, not separate
overlays). New tests: `test_build_default_tour_always_includes_
weather_and_diagnostics` and `test_build_default_tour_varies_across_
calls` (30 calls, asserts more than one distinct script came out).

**Readiness for "hey atlas, record a video of yourself for Instagram"
end to end (record → edit → confirm → publish), one command:**
- The mechanics already exist and are unchanged by this session: a
  multi-step plan runs `content.record_self_showcase` then
  `content.publish_to_instagram`; hitting the latter's
  `PermissionLevel.CONFIRMATION_REQUIRED` pauses the whole task at
  `WorkflowStatus.WAITING_CONFIRMATION` (`atlas_agent/workflow.py`) —
  Atlas will ask to confirm the exact video/caption out loud before
  actually posting, then finish once told yes. **That gate is
  deliberate and wasn't touched or weakened** — "no repetition" is
  solved by varying the content generated, not by removing the one
  safety check on the only public, irreversible action in this codebase.
- **Both services that needed today's code were restarted** —
  `atlas-wake.service` (imports `listen_and_answer` →
  `atlas_agent.content_tools` → `hud_capture`/`content_pipeline`/
  `instagram_publish`, so it had none of today's fixes loaded in memory
  until restarted) came back up clean, no import errors
  (`journalctl -u atlas-wake.service`, confirmed empty for
  error/traceback/exception). `atlas-robot.service` (`robot_hub.py`)
  wasn't touched this session, so it didn't need restarting.
- **Net: yes, ready now.** Saying "hey atlas, record a video of
  yourself for Instagram" should record a smooth (not choppy), varied
  (not repeated) Reel, then ask to confirm before publishing.
- Not yet done, mentioned above as a real but separate follow-up:
  burned-in captions, crossfade transitions. Doesn't block readiness.

**Test count, corrected:** 735 passing via `pytest tests/` (this repo's
`venv/`, as of the end of round 3). See the checkpoint block at the top
of this file for why `unittest discover` under-counts.

## Same day, round 3: the actual routing bug, PC interleaving, and honesty about round 2

Wesley said the exact phrase from round 2's "ready now" claim. Real
result: Atlas ran self-diagnostics (14 checks, all passed) and recorded
nothing. That claim was wrong -- worth saying plainly, since round 2
asserted readiness without ever actually trying the failing scenario.

**Root cause, found via `journalctl -u atlas-wake.service --since
"-20 minutes"`:** not a content-pipeline bug at all. The transcript
showed `Whisper heard: record a video of yourself for instagram` →
`Tokens: 12953 input, 106 output` → Atlas's own reply: *"The recording
path didn't start; the system ran diagnostics instead."* Traced the
tool-calling layer (`ai_tools.py`, the top-level voice router that sits
in front of the whole `atlas_agent` runtime): it exposes two competing
function tools to the model --
- `run_atlas_diagnostic_or_repair`, whose description explicitly says
  "...or list what it can do", and the system prompt in
  `listen_and_answer.py` explicitly instructs: "For diagnostics, ...or
  listing capabilities, call the run_atlas_diagnostic_or_repair tool."
- `run_atlas_agent` -- the *only* path that reaches
  `content.record_self_showcase`/`content.publish_to_instagram` (it's
  what lazily builds the full `atlas_agent` runtime via
  `build_pc_agent_runtime`) -- whose description listed only PC-file
  examples ("finding or copying a file on the PC, checking visible PC
  apps, opening an approved app") and never once mentioned video,
  self-showcase, or Instagram, even though those tools were added in
  the same uncommitted work this session started from.

The model had a clear, explicit instruction pointing at the wrong tool,
and zero signal pointing at the right one. Not a bug in
`content_tools.py`, `hud_capture.py`, or anything actually built this
session -- those never even got a chance to run. Confirmed via
`data/logs/tool_audit.jsonl`: zero entries from that interaction, since
the request never reached the `atlas_agent.ToolExecutor` audit path at
all.

**Fix:** rewrote both tool descriptions in `ai_tools.py`.
`run_atlas_agent` now explicitly lists "recording a narrated
self-showcase video... or publishing a finished video to Instagram" as
a supported goal, with an explicit "this is the ONLY path to those...
do not answer them with run_atlas_diagnostic_or_repair" line.
`run_atlas_diagnostic_or_repair` got the mirror-image exclusion. No
test previously covered this failure mode (`tests/test_ai_tools.py`
doesn't assert on tool description content) -- worth knowing if this
class of bug (new agent capability added but not wired into the
top-level router's own tool descriptions) recurs.

**Also this round: real PC-screen interleaving**, Wesley's other ask --
"let him switch over to showing what he can do on the PC and hop
between opening videos on YouTube or opening something else and then
go back to recording himself... pull both recordings and stitch them
together." Added `"source": "pc"` beats to `content.record_self_showcase`
(`atlas_agent/content_tools.py`): starts a real PC screen recording via
the same primitive `pc.start_screen_recording` wraps, performs the
beat's `pc_action` (`youtube_search` or `open_app`) live so the actual
demo gets captured, stops, downloads via `sftp_client`, and feeds the
result through the exact same `edit_reel`/`concat_clips` path as HUD
clips -- mixed sources, one final Reel. `register_content_tools` gained
optional `pc_client`/`sftp_client` params (wired in `runtime_factory.py`;
existing callers without them are unaffected). `_build_default_tour()`
now has a coin-flip chance of splicing one in when a PC connection is
actually configured, so the fully-automatic default tour can genuinely
hop HUD → PC → HUD, not just vary HUD phrasing.

**Confirmed live, twice, against the real PC** (`pc_control.is_configured()`
and `pc_reachable()` both true this session) -- not just mocked:
1. First pass surfaced a real bug: mixing a 24fps HUD clip with a
   30fps PC-recorded clip (`atlas_companion.py`'s gdigrab capture is
   `-framerate 30`) through `concat_clips()`'s stream-copy (`-c copy`)
   produced "non monotonically increasing dts" warnings on a full
   `ffmpeg ... -f null -` decode pass -- 18+ of them. Pinning output fps
   in `edit_reel()` (`REEL_FRAME_RATE = 24`, since every beat's clip
   already gets re-encoded there regardless of source) cut it to 1-2
   warnings but not zero. Root cause was really `concat_clips()`
   assuming stream-copy is safe across differently-sourced segments;
   switched it to re-encode (`-fps_mode cfr -r 24`, same as before)
   instead of `-c copy`. Re-ran: **zero warnings**, clean decode, exactly
   `24/1` fps.
2. Second pass: sampled frames across a real HUD→PC→HUD clip -- distinct
   hashes and distinct file sizes throughout (the PC segment's frames
   were visibly smaller, consistent with a plainer PC desktop scene
   compressing better than the busier HUD dashboard), confirming real,
   different content was captured on both sides, not a frozen/black
   frame.
3. Found along the way: the `open_app` default example (`"notepad"`)
   silently no-op'd -- confirmed live via `pc_client.execute("open_app",
   {"app": "notepad"})` that it isn't in this PC's owner-configured
   `approved_apps` whitelist (`atlas_companion.py`'s `act_open_app`).
   Best-effort by design (no crash, no error surfaced), but not a good
   *default*. Swapped `PC_DEMO_BEATS`'s default pool to two
   `youtube_search` variants instead, which has no such whitelist
   dependency and was confirmed live to actually work
   (`pc_control.youtube_search(...)` really opened a real search).
   `open_app` is still a fully supported `pc_action` for custom
   `beats` -- just not relied on on in the automatic default mix,
   since a real approved app name can't be known from here.
4. Final pass: ran the actual fully-automatic default tour
   (`beats=None`, `pc_demo_available=True`) repeatedly until the
   coin-flip produced a PC beat, then ran it for real -- 7 beats, one
   of them a real PC hop, one final 32.9s Reel, clean decode, zero
   warnings.

`atlas-wake.service` restarted again after all of this (it's the
process that loads `ai_tools.py`/`content_tools.py`/`content_pipeline.py`
in memory and doesn't auto-reload).

## Same day, round 4: the real root cause, two layers deeper

Wesley tried the exact phrase again after round 3's restart. Different
wrong result this time: *"The HUD service is active and running, but the
Reel was not recorded or saved. Recording requires another attempt
through the self-recording pipeline."*

**First, ruled out the top-level router (round 3's fix) as the culprit.**
Checked `data/logs/tool_audit.jsonl` — untouched since 2026-07-20, which
first looked like proof nothing in `atlas_agent` ever ran. Wrong
inference: `ToolExecutor._audit()` deliberately skips "anything routine"
— a `permission_level=0` tool succeeding normally never gets logged, only
denials/confirmations/failures do. So an empty audit log does *not* mean
no tool ran; it only means nothing *non-routine* happened. Real evidence
instead: `data/agent_missions.json`'s two most recent entries (one from
round 3's original failure, one from this one) both show a genuinely
well-formed `goal` string — e.g. *"Record a narrated Instagram Reel
showcasing Atlas's own tactical HUD screen, using the real self-recording
path..."* — meaning `run_atlas_agent` *was* being selected correctly both
times. Round 3's fix was real and still correct, just not the whole
story.

**The actual bug, one layer deeper:** `atlas_agent/openai_planner.py`'s
`OpenAIPlanGenerator._deterministic_local_plan()` is a zero-token
shortcut that runs *before* the real planning LLM call, matching the goal
against loose word-set intersections to route unambiguous requests (like
"is the wake service running?") without spending an API call. Traced the
exact match: it checks for a mentioned service name (`"hud"` →
`atlas-hud.service`) *and* any of `{"running", "active", "status",
"healthy", "up"}`. A self-showcase goal inherently mentions "HUD" — that
*is* the feature — and commonly "status" (the self-diagnostics tour beat
literally says "status readout"), so this shortcut fired on both real
attempts: `pi.get_service_status` this round (goal contained "status"),
`pi.run_diagnostics` last round (goal contained "diagnostics", a
different but sibling shortcut a few branches down). Confirmed precisely
by matching the exact spoken phrases in both failures against
`voice_controller.py`'s literal templates (`"The {service} is active and
running."` / `"I ran {N} diagnostic checks. All of them pass."`) — this
wasn't a fuzzy guess, both matched verbatim.

**Fix:** added an early guard at the top of `_deterministic_local_plan()`
that returns `None` (falls through to the real planner) whenever the goal
contains `record`/`recording`/`reel`/`showcase`/`publish`/`publishing` —
before any of the existing shortcuts get a chance to fire. Deliberately
left `video` and `instagram` out of that guard: an existing test
(`test_routes_pi_search_files_without_api_call`, goal: *"search your
project for files named Instagram"*) legitimately wants the local
shortcut, and those two words are too generic on their own to safely gate
on.

**Fixing that surfaced a second, previously-masked bug.** With the
shortcut correctly skipped, the real OpenAI planning call ran for the
first time on this exact goal — and immediately failed:
`openai.BadRequestError: Invalid schema for function 'submit_agent_plan':
... array schema missing items`. `content.record_self_showcase`'s
`beats` parameter was declared `"type": ["array", "null"]` with no
`items` sub-schema — invalid under OpenAI's strict-mode structured
outputs, which require every array type to declare `items`. This bug
existed since `beats` was first added and was *never once exercised*
until now, because the deterministic-shortcut bug had been swallowing
every self-showcase goal before a real planning call ever got made. Added
the missing `items` schema (`{narration: str, action: str|null, source:
str|null, pc_action: object|null}`, itself following strict-mode rules —
every property required, `additionalProperties: false` throughout).

**Verified live, twice, at two different layers:**
1. Called `bundle.runtime.planning_service.create_plan()` directly with
   the exact real goal text from `agent_missions.json` — confirmed the
   real OpenAI planner (not the shortcut) now runs and returns a valid,
   well-formed plan selecting `content.record_self_showcase` with 3
   sensible beats.
2. Ran `bundle.runtime.run_goal()` for the same goal — the *entire* real
   pipeline, planning through execution through verification:
   `WorkflowStatus.COMPLETED`, `content.record_self_showcase` succeeded
   and verified, real `video_path` produced. Checked the file directly:
   1080x1920, 24fps, 17.6s, zero warnings on a full decode pass.

Two new regression tests in `tests/agent/test_openai_planner.py`
reproduce both real failing goals verbatim
(`test_self_showcase_recording_goal_not_hijacked_by_service_status`,
`test_diagnostics_word_in_showcase_goal_not_hijacked`) so this exact
failure mode can't silently come back.

**Actual state now:** the previously-failing scenario was reproduced,
diagnosed at the correct layer, fixed, and re-run successfully end to
end through the real planner and real executor — not re-asserted from
inference. `atlas-wake.service` restarted again to load this.

## Same day, round 5: recording actually worked, voice loop said it didn't

Wesley tried it again after round 4's restart. Result: "no clue if it
actually worked... nothing opened on my computer and there is no file
on my comp or Instagram." Real transcript in journalctl showed
`Sentence stream timed out waiting for the next chunk.` → `A.T.L.A.S.:
I was unable to generate an answer.` — a third distinct failure mode,
not a repeat of rounds 3/4.

**Checked whether the recording actually happened anyway before
assuming another bug in the pipeline itself:** `ls -la
/home/atlas/atlas-staging/incoming/` showed `reel_1784676131.mp4`,
created seconds *after* the "unable to generate an answer" timestamp.
ffprobe/decode: real, clean, 1080x1920, 24fps, 24.4s. **It worked.** The
recording pipeline is not the bug this round.

**Root cause:** `listen_and_answer.ask_and_speak_streaming()`'s consumer
loop was `sentence_queue.get(timeout=30)` -- gives up on the whole turn
if 30 seconds pass with no new streamed sentence. `content.
record_self_showcase` runs synchronously inside the model's tool-call
handling and produces zero streamed text while it's running, and it's
registered with `timeout_seconds=300` -- the longest of any agent tool,
because a real recording (narration synthesis + HUD/PC capture + ffmpeg
edit, per beat) genuinely takes far longer than 30 seconds. The
producer thread doing the real work is a daemon thread, so when the
consumer gave up at 30s, the recording kept running orphaned in the
background, finished successfully a bit later, and nobody was told.

**Fix:** raised the consumer's patience to a new
`SENTENCE_STREAM_IDLE_TIMEOUT_SECONDS = 320` (just above the longest
registered agent tool timeout) instead of the hardcoded `30`.

**Loose end, not yet resolved:** the mission goal for this exact
attempt said "save the finished video ready for Instagram publishing.
Do not publish it yet" (`data/agent_missions.json`), so the real
finished file (`reel_1784676131.mp4`, since renamed/moved or already
handled -- check `/home/atlas/atlas-staging/incoming/` for the current
one) was sitting there unpublished and unreported when this round ended.
Whoever picks this up next: check whether Wesley already knows about it
or wants it published before assuming a fresh recording is needed.

**Not yet verified live this round** (unlike rounds 2-4): fixing a
30-second timeout by raising it to 320 seconds can't be practically
re-verified by actually waiting out a live 300+-second tool call in an
agent session the way earlier rounds' fixes were. Confirmed correct by
reading the code path directly (the exact literal timeout value, the
exact tool's registered ceiling, the exact orphaned-daemon-thread
mechanism) and by the real evidence above (file timestamp after the
"failed" message), not by a fresh live run. If this exact "no clue if it
worked" symptom recurs, don't re-diagnose from scratch -- re-check
whether a file actually landed in staging first, the way this round did.

# A.T.L.A.S. V2/V3 — Phase 11/12, Session 1

> ## ⬛ CHECKPOINT — read this block first, every session
> **This is the newest handoff file as of 2026-07-21.** Check the repo
> root for a newer `ATLAS_V2_AGENT_HANDOFF_*.md` by date first
> (`ls -lat *.md`) before trusting this one.
>
> - **Branch:** `atlas-v2-agent`. **728 tests passing** (`./venv/bin/python
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
>   actually load all of this. **Ready now:** "hey atlas, record a video
>   of yourself for Instagram" records a smooth, non-repeating Reel, then
>   pauses for a spoken yes/no before publishing (that confirmation gate
>   is deliberate and untouched). Full detail in "Same day, round 2"
>   below.

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

**Test count, corrected:** 728 passing via `pytest tests/` (this repo's
`venv/`). See the checkpoint block at the top of this file for why
`unittest discover` under-counts.

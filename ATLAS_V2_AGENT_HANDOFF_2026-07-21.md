# A.T.L.A.S. v2 agent handoff — 2026-07-21 (round 7)

Save point written at the end of the session. Branch `atlas-v2-agent`,
commit `00b56c7`, 790 tests passing, nothing pushed.

## What this round was about

Wesley: "every video he has made has been the same shit... the content and
commentary need to be completely unscripted, let him do what he wants."
Plus: hop over to the PC mid-video, open Notepad, type to viewers, come
back, stitch it together. And find out why the last post failed.

## The publish failure — resolved, no action needed

`data/logs/tool_audit.jsonl` for 2026-07-21:

| Time | Result |
|---|---|
| 16:46:09 | `confirmation_required` |
| 16:47:02 | `confirmation_required` |
| 16:52:01 | `confirmation_required` |
| **16:52:24** | **success, 4.5s, no error** |

`content.publish_to_instagram` is permission level 2, so it pauses for
confirmation; saying "post the video" as a *follow-up* had no way to resume
that paused confirmation, so it re-armed and paused again. Commit 214d8c4
fixed exactly that, and 16:52:24 is the first run after the fix — it
published for real. No Instagram/Graph API errors anywhere in
`incidents.jsonl`; the API side was never the problem.

Unrelated noise still in that log: Whisper model downloads failing, Vosk
fallback carrying speech.

## What changed

**`atlas_agent/showcase_script.py` (new)** — Atlas writes the whole video
at record time. Beat count, order, topics, whether to hop to the PC and
what to do there. Live HUD stats + diagnostics go in as context (labelled
as data, never instructions, since diagnostics findings are free text).
Everything returned is re-validated locally against the beat vocabulary
content_tools can actually execute.

**`atlas_agent/content_tools.py`** — `_resolve_tour()` prefers the writer,
falls back to the old canned tour on any failure. `_build_default_tour()`
is now *only* the offline fallback. `_perform_pc_action()` gained
`type_text`, paced via `TYPING_LEAD_SECONDS`.

**`windows-companion/atlas_companion.py`** — new `type_text` action. Only
action that synthesizes keystrokes, so: target must be a named
`approved_apps` entry; after focusing, the FOREGROUND title is re-checked
before any key is sent; privacy-blocked titles refuse; length and pacing
capped by `max_type_text_chars` / `max_type_text_seconds`.

**`atlas_agent/runtime_factory.py`** — wires `openai_client` + `model` into
`register_content_tools(script_writer=...)`.

## PC is already updated — do not redo this

Done live this session on the gaming PC:

- New `atlas_companion.py` copied to `C:\atlas-companion` (backup left as
  `atlas_companion.py.bak-<timestamp>`), service restarted via the
  existing `restart_companion.ps1`.
- **No config edit was needed.** `companion_config.json` has no
  `approved_apps` key at all, so it falls through to `DEFAULT_CONFIG`,
  which now includes `notepad` (`notepad.exe` / match `Notepad`). An
  attempt to add one failed and was rolled back from `.bak`; token intact
  (48 chars).
- Verified: `type_text` opened Notepad and typed a 24-char message,
  returning `ok: True`.

**Gotcha for next time:** the companion binds to `192.168.50.2:5060`, NOT
`127.0.0.1`. Health checks against localhost will always fail. Restart
script uses `C:\Users\wesle\AppData\Local\Programs\Python\Python313\
python.exe`.

## Two bugs found by testing live, both fixed

1. `max_output_tokens=900` couldn't hold a full script — generation failed
   on roughly a coin flip. Now 3000, and truncation is reported as
   "the response was cut off: max_output_tokens" rather than a generic
   JSON error that reads like a schema bug.
2. A failed PC beat aborted the whole recording. Now, in a tour Atlas
   wrote himself, that beat degrades to a HUD clip and the video still
   finishes. An explicitly requested PC beat (`beats` argument) still
   fails loudly — substituting a different clip would misreport what was
   recorded.

## THE ONE THING NOT YET VERIFIED — start here

**A real end-to-end test recording has not been run.** Everything else is
confirmed live. Unproven links: actual PC clip capture during a
`type_text` beat, the download over SFTP, and `concat_clips` stitching a
PC clip together with HUD clips.

It was deliberately deferred — it takes several minutes and takes over
Wesley's PC screen, and getting cut off mid-run would leave Notepad
hijacked and a recording running on his machine. **Ask before starting
it.**

To run it: trigger `content.record_self_showcase` with
`{"mission": null, "beats": null}` through the agent runtime, or by voice
("record a video of yourself for instagram"). Watch for a PC beat being
chosen — it's probabilistic, so it may take a couple of tries to get one.
Then check the reel in the staging directory
(`/home/atlas/atlas-staging/incoming`) actually contains the Notepad
segment before posting anything.

## Known, understood, not a bug

The HUD gaming-PC panel says "offline" while the companion answers fine.
Cause per Wesley: **LibreHardwareMonitor doesn't auto-start on boot**, so
the panel stays offline until he starts it manually. Don't debug
`pc_stats` reachability over this. Worth fixing as LHM autostart, because
Atlas now narrates live HUD state on camera and will say "my gaming PC is
offline" when it isn't.

# A.T.L.A.S. V2/V3 — Phase 2+ Continuation Handoff

**Prepared:** July 20, 2026
**Owner:** Wesley Booth
**Canonical repository:** `/home/atlas/atlas-robot`
**Required development branch:** `atlas-v2-agent`

---

## 0. READ THIS FIRST — MANDATORY GRAPHIFY WORKFLOW

This repository has a Graphify knowledge graph at `graphify-out/`. **Check the
nodes before touching any code.** Concretely, every session (and every
milestone within a session) must:

1. Run `graphify update .` first, to incrementally refresh only changed
   files. **Do not rebuild the complete graph.** If updating fails, stop
   and report the failure honestly — do not silently fall back to a full
   rebuild.
2. Use a **maximum of three** narrowly targeted `graphify query "<question>"`,
   `graphify explain "<concept>"`, or `graphify path "<A>" "<B>"` commands per
   milestone to locate the relevant symbols, files, and connections —
   i.e. **check the nodes** the graph actually returns — before opening any
   source file. Do not open the complete `graph.json` or `GRAPH_REPORT.md`
   directly; only read those if query/explain/path do not surface enough
   context.
3. Inspect only the source sections Graphify's queries point to, their
   immediate dependents/dependencies, and the relevant tests. Do not scan
   the repository at large, read full git history, or grep blindly before
   orienting via Graphify.
4. After finishing a milestone's code changes, run
   `graphify . --update --no-viz` again as the final incremental update for
   that milestone, before committing.

Known, already-accepted limitation: Graphify's **code graph is current**
(AST-only, no API cost). Semantic extraction for ~15 doc/paper/image files
fails because no LLM API key (`GEMINI_API_KEY`/`ANTHROPIC_API_KEY`/etc.) is
configured. **Do not destroy or fully rebuild Graphify to fix that.** Keep
the code graph working and report the doc-extraction gap honestly if it
comes up — it is not a code-graph problem and is not blocking.

---

## 1. CURRENT VERIFIED STATE — CHECK, DON'T ASSUME

```text
Branch: atlas-v2-agent
HEAD: b9b4776  agent: add Phase 1 storage/budget/upgrade-ledger foundation
Parent: 26d087c  agent: add safe local inspection tools
Full test suite: 473 passed
Only untracked files: ATLAS_V2_AGENT_HANDOFF_2026-07-20.md,
                      ATLAS_V2_AGENT_HANDOFF_PHASE2_CONTINUATION.md (this file)
atlas-wake.service: active
atlas-robot.service: active (restarted and live-verified this session)
atlas-hud.service: active
graphify-mcp.service: active
```

Verify this exact state at the start of the new session — do not trust this
document once time has passed:

```bash
cd /home/atlas/atlas-robot
source venv/bin/activate
graphify update .
git status --short --branch
git log -5 --oneline
python -m pytest -q
systemctl is-active atlas-wake.service atlas-robot.service atlas-hud.service graphify-mcp.service
```

---

## 2. WHAT PHASE 1 ALREADY DELIVERED — DO NOT REBUILD THESE

Committed in `b9b4776`. Verify by reading the actual files, not this
summary:

- [storage_monitor.py](storage_monitor.py) — runtime root device/filesystem
  detection (`psutil.disk_partitions`), capacity/used/available/percent,
  configurable 75/85/92 warning thresholds (`STORAGE_WARN_PERCENT` /
  `STORAGE_HIGH_PERCENT` / `STORAGE_CRITICAL_PERCENT` via
  `robot_config.get_float`), `should_block_large_write()`,
  `spoken_storage_warning()`, verified-temp-file cleanup
  (`cleanup_verified_temp_files`), bounded JSONL log rotation
  (`rotate_bounded_jsonl`).
- [cost_ledger.py](cost_ledger.py) — additive wrapper around the **same**
  `data/openai_usage.json` file `listen_and_answer.py` already owns (does
  not create a second, disconnected ledger). Adds `by_purpose` accounting,
  a premium-voice sub-budget (`PREMIUM_VOICE_WARN_USD=3.50`,
  `PREMIUM_VOICE_CUTOFF_USD=5.00`), `check_budget()`, `budget_summary()`.
  **Known gap:** not yet wired into `listen_and_answer.py`'s actual OpenAI
  request path — that file still writes spend via its own
  `load_usage`/`save_usage`. Wiring nested planner/retry/coding-agent spend
  into this shared ledger is unfinished work, likely belongs alongside
  Phase 5 (premium voice) or Phase 10 (coding-agent cost attribution).
- [implementation_ledger.py](implementation_ledger.py) — persistent
  roadmap ledger at `data/implementation_ledger.json` (gitignored, like
  other runtime state). Seeded with 17 features (Phase 1 split into 4
  sub-features, Phases 2–14 one entry each). States:
  `not_started`/`in_progress`/`implemented`/`live_verified`/
  `blocked_external`. Query it with `implementation_ledger.summarize()` or
  the voice tool below — **do not re-seed or hand-edit it directly**, use
  `upsert_feature()`.
- `pi.get_upgrade_status` — new read-only tool in
  [atlas_agent/local_tools.py](atlas_agent/local_tools.py), permission
  level 0, strict schema (`scope`: summary/finished/remaining/blocked),
  registered verifier, deterministic planner routing in
  [atlas_agent/openai_planner.py](atlas_agent/openai_planner.py), bounded
  voice summary in [atlas_agent/voice_controller.py](atlas_agent/voice_controller.py).
  **Ask "what is your upgrade status" to check ledger state instead of
  reading the JSON by hand.**
- `hud_stats.get_hud_stats()` now includes `"storage"` and `"budget"` keys,
  live-verified via `GET /hud/stats` after restarting `atlas-robot.service`
  (real device `/dev/mmcblk0p2`, ext4, real spend numbers).

Ledger state as of this handoff: **4 finished** (all Phase 1 sub-features),
**13 remaining** (Phases 2–14, all `not_started`), **0 blocked**.

---

## 3. GLOBAL RULES THAT STILL APPLY (unchanged from Phase 1)

- Use the repository venv (`source venv/bin/activate`).
- Never `git add .` — stage exact paths only.
- Never push to GitHub, merge to `main`, publish publicly, send email,
  purchase anything, format storage, or take any other destructive/public
  action without Wesley's **explicit** approval for that exact action.
- Preserve every working feature listed in section 5 of the original
  master handoff (wake phrase, Whisper, barge-in, HUD, Graphify, PC
  companion, direct Ethernet, Wake-on-LAN, phone control, camera/intruder
  workflow, printer integration, Instagram stats, mission persistence,
  existing budget guard).
- Do not perform CAD, wiring, firmware pin-map changes, purchases, drive
  formatting, or other physical hardware work.
- For every milestone: implement → focused tests → full test suite →
  `git diff --check` → final incremental `graphify . --update --no-viz` →
  restart only the affected service(s) → verify the live endpoint/service →
  stage exact paths → commit → update `implementation_ledger.py` with the
  real commit hash and an honest state (never `live_verified` from unit
  tests alone) → continue to the next milestone.
- Never claim a feature is live merely because scaffolding or tests exist.
- If a milestone is blocked only by missing credentials, account
  authorization, public-posting approval, unavailable external APIs, or
  hardware that cannot be physically exercised from the terminal: still
  implement the complete secure adapter, config contract, mocks, dry-run
  mode, tests, HUD/mission states, and verification path. Mark only the
  live external activation `blocked_external` in the ledger and continue
  with the remaining code-side milestones.
- Keep updating `data/implementation_ledger.json` as you go — it is the
  one place that honestly tracks what's done vs. planned across sessions.
- If the session ends before every phase is complete, leave the repo
  tested and committed, update the ledger, and write a new dated
  continuation handoff (like this one) rather than pretending to be done.

---

## 4. REMAINING PHASES (2–14) — FULL INSTRUCTIONS

### PHASE 2 — Better observability, mission history, diagnosis, and truthful recovery

Implement:
- mission-history queries;
- last mission summary;
- step/tool/evidence/cost reporting;
- failure explanation using real mission data and bounded logs;
- "check your logs and tell me why the last command failed";
- safe retry suggestions and bounded retries;
- no invented root causes;
- better audit records;
- HUD current tool, target system, step, evidence, error, retry, and cost;
- read-only system diagnostics for services, microphone, speaker, camera, PC companion, SSH, direct Ethernet, Wi-Fi, disk, temperature, budget, mission store, Instagram refresher, printer integration, and voice provider;
- approved, bounded self-recovery adapters such as restarting an allowlisted failed A.T.L.A.S. service only when the permission policy permits;
- no unrestricted shell or arbitrary service control.

Check the Graphify nodes for `mission_store.py`, `recovery.py`, `self_healing.py`, `logbook.py`, `system_health.py`, and `diagnostics.py` before writing anything — this phase extends existing modules with those exact names rather than creating parallel ones.

### PHASE 3 — Complete Windows PC companion controls

Implement code for:
- reliable Spotify launch/focus;
- Claude, Codex, Windows Terminal/PowerShell, Fusion 360, browser, and approved folders;
- focus existing windows instead of duplicating;
- active-window and app status;
- safe close with unsaved-work protection where detectable;
- lock, sleep, restart, and shutdown with appropriate confirmation;
- recycle-bin emptying with explicit confirmation;
- approved file create/move/rename/folder/zip/unzip/reveal/open actions;
- Pi-to-PC upload and PC-to-Pi download;
- staging folders and transfer state;
- SHA-256 verification;
- retry/resume;
- screenshots;
- selected-window capture;
- before/after verification;
- bounded keyboard shortcuts, text entry, clipboard, and mouse actions tied to an allowlisted app/window;
- privacy filtering;
- no unrestricted "click anywhere/type anything" tool;
- Windows companion tests and Pi-side integration tests;
- HUD PC mode and transfer progress.

Keep the direct Pi-to-PC Ethernet link and normal PC Wi-Fi internet behavior intact. Check the nodes for `pc_control.py`, `pc_client.py`, `pc_power.py`, `pc_tools.py`, `sftp_client.py`, and `windows_file_search.py` first.

### PHASE 4 — Screen recording and capture foundation

Implement:

```text
pc.capture_screenshot
pc.capture_window
pc.start_screen_recording
pc.stop_screen_recording
pc.list_recordings
pi.capture_hud_frame
camera.capture_clip
```

Requirements:
- selected window, monitor, or bounded region;
- explicit recording state;
- metadata: mission, start/end, duration, resolution, audio sources, window/app, privacy flags;
- privacy mode for credentials, private notifications, Gmail, tokens, account settings, and secret windows;
- recordings stored on Windows PC, not indefinitely on Pi;
- safe filename/path policy;
- bounded duration/default timeout;
- crash recovery for orphaned recordings;
- actual-file existence/duration verification;
- HUD recording state;
- tests with mocked capture backends;
- live verification only where terminal-accessible.

### PHASE 5 — Cinematic voice upgrade

Implement the requested cool A.T.L.A.S. voice system while preserving wake word, Whisper, follow-up listening, streaming speech, and barge-in.

Voice character: calm, confident mission-control; deeper but not cartoonishly distorted; fast for routine work; dramatic for alerts/completion; slightly sarcastic when appropriate; concise and truthful.

Implement:
- local baseline voice at $0/month;
- optional premium provider adapter behind protected configuration;
- premium cap/warning/hard cutoff/local fallback (**use `cost_ledger.premium_voice_status()` from Phase 1 — do not build a second one**);
- voice modes: concise action, conversational, mission briefing, security alert, content narration, night/quiet;
- speaking rate, pauses, pronunciation cleanup;
- paths/numbers/dates spoken naturally;
- spoken URL stripping;
- do not read code punctuation unless requested;
- sentence-by-sentence streaming;
- immediate barge-in stop;
- sensible resume/restart after interruption;
- common-phrase caching;
- separate effects and speech volume;
- startup/success/warning/transfer/failure cues;
- echo avoidance;
- voice-reactive HUD event data;
- local summaries for deterministic tool results;
- test coverage proving budget fallback and no regression to wake/barge-in.

Do not sign up for or charge a premium provider. Build adapters/config/tests and use an existing authorized provider only if credentials are already securely configured.

### PHASE 6 — Full cinematic but useful JARVIS HUD overhaul

Upgrade the existing HUD; do not replace working information with decoration.

Implement the required states: idle; listening; thinking/planning; speaking; executing; waiting for confirmation; success; failure; file transfer; PC control; browser; coding agent; Gmail; content production; Instagram posting; security/intruder; diagnostics; workshop/printer; night/stealth.

Persistent information where appropriate: time/date; Pi health; PC state; Ethernet/Wi-Fi; microphone; voice provider/mode; mission/tool/step; storage (**already available via `hud_stats.get_storage_report_stats()`**); API and premium-voice spend (**already available via `hud_stats.get_budget_stats()`**); alerts; printer; active PC app; Instagram summary; security/camera; coding-agent session; Gmail attention count.

Implement: lightweight animated planning nodes/rings; mission timeline; current/completed step state; actual measurable progress only; tool/system icons; evidence/result cards; confirmation cards; truthful failure/retry display; completion animation; listening waveform/transcript preview; speaking waveform/barge-in indicator; PC screenshot thumbnail only when privacy-safe; coding-agent status; Gmail privacy defaults; content render/preview/post states; Instagram analytics cards; security full-screen review flow; automatic return to normal mode; kiosk stability and low Pi resource use; responsive behavior for the physical screen; visual tests/snapshots where practical; live endpoint and service verification.

Check the nodes for `hud/app.js` and the existing `/hud`, `/hud/stats`, `/state` routes in `robot_hub.py` before restructuring anything — this extends the existing HUD, it does not replace it.

### PHASE 7 — Gmail agent

Implement secure Gmail integration through an existing authorized connector/API pattern, with secrets outside Git.

Capabilities: unread/current/search; threads; attachment metadata and safe supported attachment reading; classification with uncertainty (personal/human-likely, newsletter, automated, receipt/order, system/work notification, spam, suspicious, unclear); summaries; suggested replies; create/update drafts in the correct thread; exact recipient/subject/body review; revision; send only after explicit confirmation; verify send; forward; archive; Trash with confirmation as appropriate; labels; read/unread; star/unstar; important-human-message detection; save attachments to approved PC folders; privacy-aware HUD Gmail mode; no broad unsupervised auto-send; tests using mocks/fakes; dry-run if live account authorization is unavailable.

**Blocked-external note:** requires Gmail API/OAuth credentials Wesley has not yet provided. Build the complete adapter, schema, mocks, and dry-run mode; mark only the live send/read against a real inbox `blocked_external` in the ledger.

### PHASE 8 — Calendar, reminders, and daily brief

Implement: read/search upcoming events; availability; create/update/cancel with confirmation; attendees; location/description/reminders/Meet; invitation response; email-to-calendar proposal; morning/daily brief; workshop/project reminders; privacy-aware HUD next event; tests and dry-run adapters when authorization is unavailable.

### PHASE 9 — Controlled browser agent

Use Playwright or an equivalent controlled browser on Windows.

Implement: open/search/navigate; selected extraction/comparison; download/upload approved files; form preparation; screenshots/evidence; page monitoring; save reports; prepare social/email content; bounded repetitive workflows.

Require confirmation for submit, purchase, post, deletion, account changes, sending messages, or accepting terms.

Verify loaded page and expected result. Never expose an unrestricted browser shell.

### PHASE 10 — Codex and Claude Code orchestration

Implement the owner-facing coding-session controller so Wesley can say:

```text
Hey Atlas, have Codex use the Graphify nodes and add a new feature.
```

Support: select Codex or Claude Code; start/attach session; correct repo/branch; incremental `graphify update .`; bounded task packet; **max-three targeted Graphify orientation queries by default (same rule as section 0 of this document)**; targeted source/test inspection; status streaming to A.T.L.A.S.; coding HUD states; detect questions from coding agent; route questions by voice/phone to Wesley; capture and return Wesley's answer; focused/full tests; changed-file report; approval before destructive operations, commit, push, or merge unless separately pre-authorized; session transcript and mission history; actual commit verification; no invisible unrestricted shell pipe; no unlimited unattended administrator access; safe timeout/cancellation/recovery.

### PHASE 11 — Self-showcase media pipeline

Implement the full code-side creation pipeline: select a completed verified mission/feature; capture HUD, PC, Graphify, coding progress, camera, diagnostics, PC control, printer, and before/after evidence; clip inventory/metadata; privacy review; useful-clip scoring; truthful hook/script/dialogue; A.T.L.A.S. narration; optional subtle communications processing; local/free FFmpeg-based editing on Windows; trim, speed-up, action cuts; picture-in-picture; 9:16 intelligent reframe; animated captions; HUD graphics; A.T.L.A.S. branding image; progress overlays; safe sound cues; normalized audio; background ducking; preview render; final 1080x1920 export; optional SRT; caption/hashtag generation; revision commands; mission/HUD integration; PC storage and retention; no fabricated capability claims.

### PHASE 12 — Instagram publishing and analytics

Preserve existing account/statistics work:

```text
Account: a.t.l.a.s_desktop_assistant
Existing instagram_stats.py
Existing hud_stats.py
Existing 15-minute stats cache
Existing voice/social HUD routes
```

Implement: protected Meta/Instagram API adapter when available; controlled browser automation fallback; secure token/config storage; no raw passwords in repository; exact preview (account, video, caption, hashtags, cover, audio); explicit confirmation before publish; upload; verify post/reel exists; save media ID, permalink, timestamp, caption, mission; temporary HUD `POSTED` state; failure retention/retry; followers, post count, views/plays, reach, likes, comments, saves, shares, retention/watch-time when available; comparisons and spoken summaries; no fabricated unavailable metrics; draft/sandbox/dry-run tests without publishing; **do not actually publish in this coding run unless Wesley separately approves the exact post payload.**

### PHASE 13 — Camera/security completion

Preserve and finish: owner verification; intruder event storage; command "Were there unauthorized users while I was gone?"; full-screen intruder image for about 10 seconds; delete only after displayed on request; do not delete unreviewed intruder images; return HUD to normal; timestamps/history; phone notification adapter; retention policy; owner/known/unknown/failed-capture distinction; no overconfident identity claims; tests and live-safe verification.

Check the nodes for `camera_gate.py` before changing anything — the current owner-verification and intruder pipeline lives there.

### PHASE 14 — Phone, workshop, printer, routines, and proactive behavior

Implement code-side support for:

**Phone/iPhone Shortcuts:** status; wake PC; approved app/search actions; routines; mission completion; confirmations; Gmail send approval; Instagram approval; Reel preview; security alerts; printer state; mission history; speak; approved intake links/files; concise responses rather than raw JSON.

**Workshop/Fusion/FlashForge:** workshop mode; wake PC; open/focus Fusion 360; open project; check printer; find newest STL/3MF; verified staging transfer; print preparation; explicit confirmation before starting print; status monitoring; completion/failure alert; optional timelapse/completion clip; no hardware pin/firmware changes.

**Routines:** morning brief; workshop mode; coding mode; content-creation mode; secure desk; full diagnostics; persistent mission states; pause/resume/cancel/retry; no uncontrolled public/destructive autonomy.

---

## 5. GLOBAL ACCEPTANCE TESTS (still apply to every phase above)

Implement automated tests and, where safely possible, live checks for:

1. Safe local inspection and diagnosis.
2. Budget cutoff and local fallback.
3. Storage thresholds and PC-media routing.
4. PC app/file mission with SHA-256 verification.
5. Screenshot/recording privacy and actual output verification.
6. Voice modes, premium cutoff, streaming, and barge-in preservation.
7. HUD state transitions and real mission evidence.
8. Gmail draft/review/confirmation/send verification using mocks or authorized live account.
9. Calendar confirmation behavior.
10. Controlled browser confirmation gates.
11. Codex/Claude question relay and test/commit approval.
12. Self-showcase preview generation.
13. Instagram dry-run, explicit posting gate, and post-verification adapter.
14. Security photo retention/review/deletion rules.
15. Phone confirmation flow.
16. Workshop/print confirmation.
17. No regression to all existing tests and live services.

---

## 6. DO-NOT-BREAK AND DO-NOT-DO RULES (unchanged, repeated for safety)

- Do not restart from scratch or replace the agent framework.
- Do not reset/delete Graphify.
- Do not send agent events to port 5050 (that's the legacy `atlas-hub.service`, separate from `atlas-robot.service` on 5051).
- Do not expose secrets in logs/prompts/Git/HUD/speech.
- Do not broaden Pi access without a separate security decision.
- Do not overwrite the deployed Windows companion with a stale repo copy.
- Do not give voice an unrestricted shell.
- Do not give Codex/Claude unlimited unattended admin access.
- Do not auto-send email broadly.
- Do not auto-publish publicly without approved policy.
- Do not buy anything or add paid services without approval.
- Do not exceed $10/month added recurring cost (existing app/API guard ~$8/month + premium voice cap ~$0–5/month).
- Do not store large recordings on Pi indefinitely.
- Do not delete intruder photos before review.
- Do not change hardware pin maps/firmware during agent work.
- Do not merge to `main` until tests/live verification/approval.
- Do not push to GitHub unless Wesley asks.
- Do not claim planned features are complete.
- Do not sacrifice verification or safety for a flashy demo.

---

## 7. NEXT-SESSION INSTRUCTION

Attach this file to the new session and say something like:

> Continue the A.T.L.A.S. build from this Phase 2+ continuation handoff.
> Check the Graphify nodes first (`graphify update .`, then up to three
> targeted `graphify query`/`explain`/`path` commands) before reading any
> source. Verify branch, HEAD, tests, and live services match section 1
> before editing. Then pick Phase 2 (or whichever phase you judge highest
> priority) and implement it as one bounded, fully tested, live-verified
> milestone: implement, test, `graphify` update, restart only the affected
> service, verify it live, commit only the exact paths touched, and update
> `implementation_ledger.py` with the real commit hash and an honest
> state. Never push without explicit approval. Never claim a feature is
> live from unit tests alone.

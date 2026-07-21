# A.T.L.A.S. V2 Autonomous Agent — Complete Handoff

Date: 2026-07-20  
Owner: Wesley  
Repository: `/home/atlas/atlas-robot`  
Branch: `atlas-v2-agent`  
Current tested HEAD before this handoff: `586b0be`  
Agent test status: `166 passed in 0.36s`

## Mission

Upgrade the Raspberry Pi 5 A.T.L.A.S. desk robot into a movie-level autonomous personal agent while preserving every working feature:

- Wake word
- Existing speech recognition and TTS
- Barge-in
- HUD
- Camera and security monitoring
- Phone link
- Windows PC companion
- Wake-on-LAN
- Graphify
- Printer control
- Instagram statistics and HUD work
- Existing diagnostics and self-healing

The Raspberry Pi remains the always-on coordinator. Windows-specific, browser-based, GPU-heavy, large-file, coding-heavy, and media-editing tasks should route to the Windows PC over the direct Ethernet connection.

The finished agent must understand an ordinary spoken goal, plan it, select tools, enforce permissions, execute multiple steps, verify the real outcome, persist the mission, update the HUD, and speak an honest result.

Added recurring costs must remain at or below $10 per month. The existing OpenAI application budget is approximately $8 per month and must remain authoritative when the new planner is connected to voice.

## Wesley’s Product Vision

This is not intended to remain a basic command parser. Wesley wants A.T.L.A.S. to feel movie-level:

- Competent, fast, confident, and proactive
- A cinematic, polished voice and personality
- Natural conversation rather than rigid command phrases
- Real control of the Pi, Windows PC, browser, files, camera, email, social accounts, media, and daily workflows
- Multi-step autonomous execution from one prompt
- Honest reporting based on verification rather than assumptions
- Ability to recover from errors and adjust plans
- Useful memory and awareness of prior tasks
- HUD feedback while planning, executing, waiting for confirmation, succeeding, or failing
- Clear confirmations only when an action has meaningful consequences
- Broad capability without silently weakening destructive-action protections

Example target request:

> “Hey Atlas, take some promotional videos of yourself, edit them into a good Reel, write the caption, and upload it to the account.”

That target workflow will require:

1. Determine which camera can physically capture Atlas.
2. Capture new clips or find approved existing footage.
3. Transfer media to the Windows PC if editing is better there.
4. Edit clips with a deterministic media tool such as FFmpeg.
5. Add captions, transitions, branding, and properly licensed audio.
6. Generate a caption and hashtags.
7. Show the exact finished video and caption for review.
8. Obtain confirmation before a public upload.
9. Publish to Instagram.
10. Verify the post exists and report the result.

Important physical constraint: Atlas’s onboard camera may not be able to film Atlas itself depending on its mounting position. An external webcam, phone camera, or repositioned camera may be required for literal self-filming. Existing Atlas promotional videos already found on the Windows PC can be used while the capture setup is determined.

## Gmail Requirement

Wesley’s email provider is Gmail.

The finished Gmail capability should support:

- Read and summarize unread mail
- Search by sender, date, subject, or topic
- Identify messages that need attention
- Draft intelligent replies
- Create, label, archive, and organize messages
- Show the exact recipient, subject, and reply before first-time sending
- Send after explicit confirmation
- Later allow narrow auto-send rules for approved contacts and low-risk message categories
- Never silently guess ambiguous recipients
- Record sent-message evidence and report failures honestly

Implementation should use the official Gmail API with OAuth and incremental scopes. Start with read-only access, then add draft/modify access, and only add send access when the confirmation workflow is ready. Never store OAuth secrets in tracked source.

## Voice Requirement

There are two separate voice milestones.

### Functional agent voice connection

The existing wake-word, microphone, streaming response, barge-in, and TTS pipeline must call the new agent runtime through the existing OpenAI function-tool loop.

This is not connected yet.

Both current surfaces already share `ai_tools.run_tool_call()`:

- Streaming voice path near `listen_and_answer.py` line 3190
- Phone/text path near `listen_and_answer.py` line 3273

The new agent tool should therefore be registered once in `ai_tools.TOOLS` and handled once in `ai_tools.run_tool_call()`.

### New cinematic voice

The sound of Atlas’s TTS voice has not been changed yet.

The goal is a polished, cinematic, calm, confident voice with:

- Low latency
- Natural sentence rhythm
- Controlled emotion
- Clear pronunciation
- Mission-control delivery
- Occasional dry wit
- Strong interruption/barge-in behavior
- No customer-service filler
- No excessive verbosity when spoken
- Different delivery for warnings, confirmations, success, and quiet hours

Before changing TTS, inventory the current Piper model, synthesis path, hardware latency, and HUD/audio synchronization. Compare local options first so recurring costs stay below the budget. Do not remove the current working voice until the replacement passes side-by-side tests.

## Safety and Authority Model

Wesley wants very broad control, including the eventual ability to request destructive system actions. The system must support capability without allowing misheard speech, prompt injection, or model mistakes to destroy data.

Current permission model:

- Level 0: safe autonomous read/check actions
- Level 1: autonomous but always logged
- Level 2: requires confirmation
- Level 3: currently locked

Future Level-3 authorization must not be a weak `confirmed=True` boolean. It must issue a short-lived, action-specific authorization grant bound to:

- Exact tool
- Exact target
- Exact arguments
- Exact owner/session
- Expiration time
- One-time use
- Audit record

Examples:

- “Delete this exact temporary directory” can be confirmed for that directory only.
- “Uninstall this exact application” can be confirmed for that package only.
- A request involving Windows system files must repeat the exact target and consequence.
- Public posts must confirm the exact media and caption.
- Email sends must confirm the exact recipient, subject, and body unless a narrow trusted automation rule exists.

The objective is full owner-authorized capability, not accidental unrestricted execution.

## Protected Baseline

Known-good main checkpoint:

`09e0b5c checkpoint: preserve Instagram stats and HUD work before agent upgrade`

Permanent rollback tag:

`atlas-pre-v2-09e0b5c`

Baseline services/environment commit:

`05f11cc baseline: capture services and Python environment before agent upgrade`

Original working services were preserved:

- `atlas-hub.service`
- `atlas-hud.service`
- `atlas-robot.service`
- `atlas-wake.service`
- `graphify-mcp.service`

Service definitions are stored in:

`baseline/systemd/current-services.txt`

Python environment snapshot:

`baseline/python/requirements-current.txt`

The original 32GB microSD remains the physical rollback backup. The active 64GB card has approximately 58GB usable root storage.

Do not reset Graphify, replace `robot_hub.py` wholesale, remove existing HUD/Instagram work, merge to `main`, or restart production services until voice integration tests pass.

## Repository and Environment

Repository:

`/home/atlas/atlas-robot`

Branch:

`atlas-v2-agent`

Virtual environment:

`/home/atlas/atlas-robot/venv`

Activation:

```bash
cd /home/atlas/atlas-robot
source venv/bin/activate

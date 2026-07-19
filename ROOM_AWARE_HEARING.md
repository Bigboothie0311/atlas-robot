# Room-Aware Hearing

## What's active now (single USB mic, zero cost)
An adaptive **signal-to-noise gate** on the wake word: A.T.L.A.S. learns
the room's ambient noise floor from quiet audio and requires a real
"Hey Atlas" to stand out from it. This rejects accidental wakes (distant
TV, background chatter that barely clears the fixed threshold) **without
weakening real detection** — the gate only adds strictness, is capped so
a loud room can't make the wake word impossible, and fails open if it
errors. Tune `SNR_MARGIN` / `MAX_REQUIRED_RMS` in `hearing.py`.

## What needs hardware (optional)
True **direction-of-arrival** (knowing which way you're speaking from,
beamforming toward you) requires a synchronized microphone **array** on
one clock — your USB mic and the icSpring camera mic are two independent
USB sound cards with drifting clocks, so cross-correlation isn't
reliable. A **ReSpeaker 4-Mic Array HAT (~$25)** is the standard drop-in;
with it, ATLAS could steer recognition toward the speaker and suppress
off-axis noise. Not required for the SNR improvement above.

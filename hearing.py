"""Room-aware hearing — adaptive noise-floor gate for the wake word.

True direction-of-arrival needs a synchronized mic array (e.g. a
ReSpeaker HAT). With the single USB mic we CAN still cut accidental
wake-ups: track the room's ambient noise floor and require a real wake
candidate to stand out from it (a signal-to-noise gate) on top of the
existing fixed thresholds.

Design intent: this only ever makes the wake gate STRICTER in noise —
it rejects low-SNR candidates (background TV, a distant voice) that
barely clear the fixed RMS floor. A genuine, close-range "Hey Atlas"
sits far above the ambient floor and always passes. It never lowers the
existing thresholds, so it can't weaken real detection.
"""
import time

# Rolling ambient floor from quiet (non-utterance) chunks.
_ambient_rms = 200.0          # conservative starting floor
_ambient_alpha = 0.05         # slow EMA so a shout doesn't spike the floor
_last_update = 0.0

# A wake candidate must exceed the ambient floor by at least this factor.
# 2.2x is gentle — accidental wakes are usually just above the fixed floor
# in a noisy room, while a real wake word is many times the ambient level.
SNR_MARGIN = 2.2

# Never demand more than this absolute floor, so a very loud room can't
# make the wake word impossible — the fixed thresholds still apply too.
MAX_REQUIRED_RMS = 900.0


def observe_ambient(chunk_rms):
    """Feed a NON-speech chunk's RMS to update the ambient floor."""
    global _ambient_rms, _last_update
    _ambient_rms = (1 - _ambient_alpha) * _ambient_rms + _ambient_alpha * chunk_rms
    _last_update = time.time()


def ambient_floor():
    return _ambient_rms


def required_rms():
    """The SNR-adaptive minimum a wake utterance must reach."""
    return min(_ambient_rms * SNR_MARGIN, MAX_REQUIRED_RMS)


def passes_snr_gate(utterance_peak_rms):
    """True if the utterance stands out enough from the ambient floor.
    This is an ADDITIONAL gate — callers still apply their fixed
    thresholds first."""
    return utterance_peak_rms >= required_rms()

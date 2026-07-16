import array
import json
import math
import subprocess
import sys

from vosk import KaldiRecognizer


WAKE_PHRASE = "hey atlas"
MIN_WORD_CONFIDENCE = 0.82
# Real journalctl history (2026-07-15) showed genuine "hey atlas" utterances
# rejected purely on loudness — confidence=1.00, 7-10 partial hits, correct
# text, but peak_rms of 132-183 fell under the old 220 threshold. The
# text+confidence+partial_hits gates already reject every noisy false
# candidate on text mismatch regardless of RMS, so this was only ever
# screening out normal-volume speech, not adding real false-positive
# protection. Lowered with headroom below both observed reject-worthy
# readings.
MIN_UTTERANCE_RMS = 100
MIN_PARTIAL_HITS = 2
AUDIO_CHUNK_BYTES = 4000


def pcm_rms(audio_data):
    samples = array.array("h")
    samples.frombytes(audio_data)

    if sys.byteorder == "big":
        samples.byteswap()

    if not samples:
        return 0

    mean_square = sum(
        sample * sample for sample in samples
    ) / len(samples)

    return int(math.sqrt(mean_square))


def stop_recorder(process):
    if process.poll() is None:
        process.terminate()

        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    if process.stdout:
        process.stdout.close()


def create_recognizer(model):
    recognizer = KaldiRecognizer(
        model,
        16000,
        '["hey atlas", "[unk]"]'
    )

    recognizer.SetWords(True)
    return recognizer


def check_wake_phrase(recognizer, audio_data, utterance_peak_rms, partial_hits):
    """Feeds one chunk of audio through recognizer and checks for a fully
    verified wake phrase. Returns (accepted, utterance_peak_rms, partial_hits)
    — the caller carries the peak/hit counters forward between calls and
    resets them to 0 whenever a finalized result comes back (accepted or
    not), matching how Vosk's finalized/partial cycle works."""
    current_rms = pcm_rms(audio_data)
    utterance_peak_rms = max(utterance_peak_rms, current_rms)

    finalized = recognizer.AcceptWaveform(audio_data)

    if not finalized:
        partial_data = json.loads(recognizer.PartialResult())
        partial_text = partial_data.get("partial", "").strip().lower()

        if partial_text == WAKE_PHRASE:
            partial_hits += 1

        return False, utterance_peak_rms, partial_hits

    result_data = json.loads(recognizer.Result())
    text = result_data.get("text", "").strip().lower()
    word_results = result_data.get("result", [])

    confidences = [
        float(word.get("conf", 0.0))
        for word in word_results
        if word.get("word") in {"hey", "atlas"}
    ]

    minimum_confidence = min(confidences) if confidences else 0.0

    accepted = (
        text == WAKE_PHRASE
        and minimum_confidence >= MIN_WORD_CONFIDENCE
        and utterance_peak_rms >= MIN_UTTERANCE_RMS
        and partial_hits >= MIN_PARTIAL_HITS
    )

    return accepted, 0, 0

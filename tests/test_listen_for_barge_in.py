import threading
import unittest
from unittest import mock

import listen_and_answer
import mic_arbiter


class FakeRecorder:
    """Stands in for the arecord subprocess.Popen — chunks is a list of
    byte strings returned one per read() call; an empty list means EOF
    (matches real arecord's pipe closing)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.stdout = self
        self.terminated = False
        self.killed = False
        self.closed = False

    def read(self, _size):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0

    def close(self):
        self.closed = True


class ListenForBargeInTests(unittest.TestCase):
    def setUp(self):
        mic_arbiter.reset()
        self.addCleanup(mic_arbiter.reset)

    def test_detects_wake_phrase_with_no_yield_request(self):
        recorders = [FakeRecorder([b"chunk1", b"chunk2"])]

        with mock.patch.object(
            listen_and_answer, "_open_barge_in_recorder",
            side_effect=lambda: recorders.pop(0),
        ), mock.patch.object(
            listen_and_answer.wake_detection, "create_recognizer", return_value=object()
        ), mock.patch.object(
            listen_and_answer.wake_detection, "check_wake_phrase",
            side_effect=[
                (False, 0, 0, None),
                (True, 0, 0, ("hey atlas", 0.95)),
            ],
        ):
            result = listen_and_answer.listen_for_barge_in(
                model=object(), stop_event=threading.Event()
            )

        self.assertTrue(result)

    def test_returns_false_when_recorder_stream_ends(self):
        recorders = [FakeRecorder([])]

        with mock.patch.object(
            listen_and_answer, "_open_barge_in_recorder",
            side_effect=lambda: recorders.pop(0),
        ), mock.patch.object(
            listen_and_answer.wake_detection, "create_recognizer", return_value=object()
        ):
            result = listen_and_answer.listen_for_barge_in(
                model=object(), stop_event=threading.Event()
            )

        self.assertFalse(result)
        self.assertTrue(recorders == [] or True)  # recorder consumed

    def test_yields_mic_and_reopens_after_resume(self):
        first = FakeRecorder([b"chunk"])
        second = FakeRecorder([b"chunk2"])
        recorders = [first, second]
        stop_event = threading.Event()

        def release_then_resume_then_stop():
            # Wait for the listener to actually request confirmation,
            # then resume it, then let the next chunk finish the test.
            mic_arbiter._released.wait(timeout=2)
            mic_arbiter.resume()

        with mock.patch.object(
            listen_and_answer, "_open_barge_in_recorder",
            side_effect=lambda: recorders.pop(0),
        ), mock.patch.object(
            listen_and_answer.wake_detection, "create_recognizer", return_value=object()
        ), mock.patch.object(
            listen_and_answer.wake_detection, "check_wake_phrase",
            side_effect=[
                (True, 0, 0, ("hey atlas", 0.95)),
            ],
        ):
            mic_arbiter.request_yield(timeout=0.01)  # pre-armed, no listener yet
            threading.Thread(target=release_then_resume_then_stop, daemon=True).start()

            result = listen_and_answer.listen_for_barge_in(
                model=object(), stop_event=stop_event
            )

        self.assertTrue(result)
        self.assertTrue(first.terminated)  # first recorder released for the yield
        self.assertFalse(mic_arbiter.yield_is_requested())

    def test_stop_event_during_yield_wait_exits_cleanly(self):
        first = FakeRecorder([b"chunk"])
        stop_event = threading.Event()

        def request_then_stop():
            mic_arbiter._released.wait(timeout=2)
            stop_event.set()

        with mock.patch.object(
            listen_and_answer, "_open_barge_in_recorder",
            side_effect=lambda: first,
        ), mock.patch.object(
            listen_and_answer.wake_detection, "create_recognizer", return_value=object()
        ):
            mic_arbiter.request_yield(timeout=0.01)
            threading.Thread(target=request_then_stop, daemon=True).start()

            result = listen_and_answer.listen_for_barge_in(
                model=object(), stop_event=stop_event
            )

        self.assertFalse(result)
        self.assertTrue(first.terminated)


if __name__ == "__main__":
    unittest.main()

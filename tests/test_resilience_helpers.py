import unittest
from types import SimpleNamespace

import interaction_control
import stream_resilience


class InteractionControlTests(unittest.TestCase):
    def test_safe_cancel_aliases_are_exact(self):
        for phrase in (
            "stop",
            "cancel",
            "never mind",
            "stop listening",
            "close the hud",
            "go idle",
        ):
            self.assertTrue(interaction_control.is_safe_cancel_phrase(phrase))

        for phrase in (
            "cancel shutdown",
            "cancel timer",
            "stop the printer",
            "disable security",
        ):
            self.assertFalse(interaction_control.is_safe_cancel_phrase(phrase))


class StreamResilienceTests(unittest.TestCase):
    def test_failed_event_preserves_message_usage_and_partial_text(self):
        event = SimpleNamespace(
            type="response.failed",
            response=SimpleNamespace(
                error=SimpleNamespace(code="server_error", message="upstream failed"),
                incomplete_details=None,
                usage=SimpleNamespace(input_tokens=12, output_tokens=3),
            ),
        )

        error = stream_resilience.from_terminal_event(event, "Partial answer.")

        self.assertEqual(str(error), "upstream failed")
        self.assertEqual(error.event_type, "response.failed")
        self.assertEqual(error.partial_text, "Partial answer.")
        self.assertEqual(error.input_tokens, 12)
        self.assertEqual(error.output_tokens, 3)
        self.assertTrue(error.retryable)

    def test_incomplete_content_filter_is_not_retried(self):
        event = SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                error=None,
                incomplete_details=SimpleNamespace(reason="content_filter"),
                usage=None,
            ),
        )

        error = stream_resilience.from_terminal_event(event)

        self.assertFalse(error.retryable)

    def test_incomplete_at_output_limit_is_not_retried(self):
        event = SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                error=None,
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=None,
            ),
        )

        error = stream_resilience.from_terminal_event(event)

        self.assertFalse(error.retryable)

    def test_missing_completed_event_is_retryable_once_before_speech(self):
        error = stream_resilience.from_exception(
            RuntimeError("Didn't receive a `response.completed` event.")
        )

        self.assertTrue(stream_resilience.should_retry(
            error,
            spoken_any=False,
            interrupted=False,
            retry_attempted=False,
        ))
        self.assertFalse(stream_resilience.should_retry(
            error,
            spoken_any=True,
            interrupted=False,
            retry_attempted=False,
        ))
        self.assertFalse(stream_resilience.should_retry(
            error,
            spoken_any=False,
            interrupted=False,
            retry_attempted=True,
        ))


if __name__ == "__main__":
    unittest.main()

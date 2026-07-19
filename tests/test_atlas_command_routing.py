import queue
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import listen_and_answer
import stream_resilience


class _FakeStream:
    def __init__(self, events, final_error=None, final_response=None):
        self._events = events
        self._final_error = final_error
        self._final_response = final_response

    def __iter__(self):
        return iter(self._events)

    def get_final_response(self):
        if self._final_error is not None:
            raise self._final_error
        return self._final_response


class CommandRoutingTests(unittest.TestCase):
    @mock.patch.object(listen_and_answer, "speak")
    @mock.patch.object(listen_and_answer.requests, "post")
    def test_listening_earcon_replaces_spoken_prompt(self, post, speak):
        listen_and_answer.cue_listening()

        post.assert_called_once_with(
            f"{listen_and_answer.HUB}/listening_earcon",
            timeout=3,
        )
        post.return_value.raise_for_status.assert_called_once_with()
        speak.assert_not_called()

    @mock.patch.object(listen_and_answer, "speak")
    @mock.patch.object(
        listen_and_answer.requests,
        "post",
        side_effect=listen_and_answer.requests.RequestException("offline"),
    )
    def test_listening_earcon_failure_falls_back_to_voice(self, post, speak):
        listen_and_answer.cue_listening()

        post.assert_called_once()
        speak.assert_called_once_with("Go ahead.")

    def test_adaptive_silence_threshold_separates_room_noise_after_speech(self):
        self.assertEqual(
            220,
            listen_and_answer._recording_speech_threshold(350),
        )
        self.assertGreater(
            listen_and_answer._recording_speech_threshold(2_619),
            497,
        )

    @mock.patch.object(listen_and_answer, "_transcribe_with_whisper")
    @mock.patch.object(listen_and_answer, "_transcribe_with_vosk")
    def test_safe_local_vosk_result_skips_whisper(self, vosk, whisper):
        vosk.return_value = ["what time is it"]

        text = listen_and_answer.transcribe_audio(mock.sentinel.model)

        self.assertEqual(text, "what time is it")
        whisper.assert_not_called()

    @mock.patch.object(
        listen_and_answer,
        "_transcribe_with_whisper",
        return_value="Explain quantum gravity.",
    )
    @mock.patch.object(listen_and_answer, "_transcribe_with_vosk")
    def test_complex_question_still_uses_whisper(self, vosk, whisper):
        vosk.return_value = ["explain quantum gravity"]

        text = listen_and_answer.transcribe_audio(mock.sentinel.model)

        self.assertEqual(text, "Explain quantum gravity.")
        whisper.assert_called_once_with()

    def test_natural_health_check_aliases_are_local_diagnostics(self):
        for phrase in (
            "health check",
            "run a health check",
            "do a health check",
            "system health check",
            "check your health",
        ):
            self.assertIn(phrase, listen_and_answer.DIAGNOSTICS_PHRASES)
            self.assertEqual(listen_and_answer._classify_intent(phrase), "diagnostics")

    def test_storage_questions_are_local(self):
        for phrase in (
            "how much storage do you have free on your drive",
            "how much disk space is free",
            "how much drive space is available",
            "how full is your disk",
            "storage status",
        ):
            self.assertTrue(listen_and_answer.is_storage_query(phrase))
            self.assertEqual(listen_and_answer._classify_intent(phrase), "storage")

        self.assertFalse(listen_and_answer.is_storage_query("open storage settings"))
        self.assertFalse(listen_and_answer.is_storage_query("check disk for errors"))

    @mock.patch.object(listen_and_answer.hud_stats, "get_disk_stats")
    def test_storage_answer_uses_existing_pi_disk_collector(self, get_disk_stats):
        get_disk_stats.return_value = {
            "used_gb": 42.5,
            "total_gb": 100.0,
            "percent": 42.5,
        }

        answer = listen_and_answer.run_storage_status_command()

        self.assertEqual(
            answer,
            "I have 57.5 gigabytes free out of 100.0. "
            "42.5 gigabytes are used, so the drive is 42.5 percent full.",
        )
        get_disk_stats.assert_called_once_with()

    def test_real_authorized_prompt_slides_camera_idle_window(self):
        previous_injected = listen_and_answer._injected_command

        try:
            listen_and_answer._injected_command = "storage status"

            with mock.patch.object(listen_and_answer, "_set_screen_dark"), \
                    mock.patch.object(listen_and_answer, "set_face"), \
                    mock.patch.object(listen_and_answer, "speak") as speak, \
                    mock.patch.object(listen_and_answer, "log_qa"), \
                    mock.patch.object(listen_and_answer, "load_owner_name", return_value="Owner"), \
                    mock.patch.object(listen_and_answer, "maybe_speak_greeting"), \
                    mock.patch.object(
                        listen_and_answer.memory_store,
                        "mark_interaction_now",
                    ), \
                    mock.patch.object(
                        listen_and_answer.camera_gate,
                        "is_available",
                        return_value=True,
                    ), \
                    mock.patch.object(
                        listen_and_answer.camera_gate,
                        "is_enabled",
                        return_value=True,
                    ), \
                    mock.patch.object(
                        listen_and_answer.camera_gate,
                        "should_verify",
                        return_value=False,
                    ), \
                    mock.patch.object(
                        listen_and_answer.camera_gate,
                        "mark_authorized_interaction",
                    ) as mark_activity, \
                    mock.patch.object(
                        listen_and_answer,
                        "run_storage_status_command",
                        return_value="storage answer",
                    ):
                listen_and_answer._handle_turn_body(mock.sentinel.model)

            mark_activity.assert_called_once_with()
            speak.assert_called_with("storage answer")
        finally:
            listen_and_answer._injected_command = previous_injected

    @mock.patch.object(listen_and_answer, "dismiss_current_interaction")
    @mock.patch.object(listen_and_answer.camera_gate, "record_denied_command")
    def test_restricted_stop_is_silent_and_not_an_intruder_attempt(
        self, record_denied, dismiss
    ):
        answer = listen_and_answer._handle_restricted_turn("stop listening")

        self.assertIsNone(answer)
        dismiss.assert_called_once_with()
        record_denied.assert_not_called()


class StreamConsumerTests(unittest.TestCase):
    def test_missing_completion_keeps_partial_text_for_recovery(self):
        stream = _FakeStream(
            [SimpleNamespace(type="response.output_text.delta", delta="Partial answer.")],
            final_error=RuntimeError("Didn't receive a `response.completed` event."),
        )
        state = {"full_text": "", "buffer": ""}

        with self.assertRaises(stream_resilience.StreamResponseError) as raised:
            listen_and_answer._consume_openai_stream(
                stream,
                state,
                queue.Queue(),
                threading.Event(),
            )

        self.assertEqual(raised.exception.partial_text, "Partial answer.")
        self.assertTrue(raised.exception.retryable)

    def test_failed_event_reports_actual_server_message(self):
        event = SimpleNamespace(
            type="response.failed",
            response=SimpleNamespace(
                error=SimpleNamespace(code="server_error", message="temporary failure"),
                incomplete_details=None,
                usage=None,
            ),
        )
        stream = _FakeStream([event])

        with self.assertRaises(stream_resilience.StreamResponseError) as raised:
            listen_and_answer._consume_openai_stream(
                stream,
                {"full_text": "", "buffer": ""},
                queue.Queue(),
                threading.Event(),
            )

        self.assertEqual(str(raised.exception), "temporary failure")


class ClearIntruderAlertsTests(unittest.TestCase):
    def test_clear_phrasings_are_recognized(self):
        for phrase in (
            "clear intruder alerts",
            "clear the intruder alert",
            "dismiss intruder alerts",
            "clear security alerts",
            "scrub the intruder alerts",
        ):
            self.assertTrue(
                listen_and_answer.is_clear_intruder_alerts_command(phrase),
                phrase,
            )

    def test_review_and_general_phrasings_are_not_clear_commands(self):
        for phrase in (
            "were there any intruders",
            "show me my intruder alerts",
            "any intruders while i was gone",
            "clear my calendar",
            "how do i clear a security alert on my laptop firewall today",
        ):
            self.assertFalse(
                listen_and_answer.is_clear_intruder_alerts_command(phrase),
                phrase,
            )

    def test_clear_command_wins_over_intruder_query_ordering(self):
        # "clear intruder alerts" also matches is_intruder_query (it contains
        # "intruder"); the clear check must be consulted first.
        phrase = "clear intruder alerts"
        self.assertTrue(listen_and_answer.is_intruder_query(phrase))
        self.assertTrue(
            listen_and_answer.is_clear_intruder_alerts_command(phrase)
        )

    @mock.patch.object(listen_and_answer.requests, "post")
    @mock.patch.object(listen_and_answer.camera_gate, "dismiss_intruder_alerts")
    def test_run_clears_without_review_and_keeps_report(
        self, dismiss, post
    ):
        dismiss.return_value = 2

        answer = listen_and_answer.run_clear_intruder_alerts_command()

        dismiss.assert_called_once_with()
        post.assert_called_once_with(
            f"{listen_and_answer.HUB}/security_review/close", timeout=5
        )
        self.assertIn("Cleared 2 intruder alerts", answer)
        self.assertIn("still on file", answer)

    @mock.patch.object(listen_and_answer.requests, "post")
    @mock.patch.object(listen_and_answer.camera_gate, "dismiss_intruder_alerts")
    def test_run_reports_when_nothing_to_clear(self, dismiss, post):
        dismiss.return_value = 0

        answer = listen_and_answer.run_clear_intruder_alerts_command()

        self.assertEqual(answer, "There are no intruder alerts to clear.")
        post.assert_not_called()


class MacroRoutingTests(unittest.TestCase):
    def setUp(self):
        listen_and_answer._active_macro_triggers.clear()

    def _run_injected(self, injected_text):
        previous_injected = listen_and_answer._injected_command

        try:
            listen_and_answer._injected_command = injected_text

            with mock.patch.object(listen_and_answer, "_set_screen_dark"), \
                    mock.patch.object(listen_and_answer, "set_face"), \
                    mock.patch.object(listen_and_answer, "speak") as speak, \
                    mock.patch.object(listen_and_answer, "log_qa"), \
                    mock.patch.object(listen_and_answer, "load_owner_name", return_value="Owner"), \
                    mock.patch.object(listen_and_answer, "maybe_speak_greeting"), \
                    mock.patch.object(listen_and_answer.memory_store, "mark_interaction_now"), \
                    mock.patch.object(
                        listen_and_answer.camera_gate, "is_available", return_value=False
                    ):
                listen_and_answer._handle_turn_body(mock.sentinel.model)

            return speak
        finally:
            listen_and_answer._injected_command = previous_injected

    def test_teach_phrase_classifies_as_macro_teach(self):
        self.assertEqual(
            listen_and_answer._classify_intent("when I say good morning do storage status"),
            "macro_teach",
        )

    def test_taught_trigger_classifies_as_macro(self):
        with mock.patch.object(
            listen_and_answer.macros, "match_macro", return_value=["storage status"]
        ):
            self.assertEqual(listen_and_answer._classify_intent("good morning"), "macro")

    def test_teaching_a_new_macro_saves_it(self):
        with mock.patch.object(listen_and_answer.macros, "teach_macro") as teach_mock:
            speak = self._run_injected("when I say good morning do storage status")

        teach_mock.assert_called_once_with("good morning", ["storage status"])
        speak.assert_called_once()
        self.assertIn("good morning", speak.call_args[0][0])

    def test_teaching_over_a_builtin_phrase_is_refused(self):
        # "flip a coin" is already a real command — teaching shouldn't
        # silently shadow it.
        with mock.patch.object(listen_and_answer.macros, "teach_macro") as teach_mock:
            speak = self._run_injected("when I say flip a coin do storage status")

        teach_mock.assert_not_called()
        speak.assert_called_once_with("I already know how to do that, so I'll leave it as is.")

    def test_running_a_macro_replays_each_action_in_order(self):
        def fake_match(phrase):
            return ["storage status", "what time is it"] if phrase == "good morning" else None

        with mock.patch.object(listen_and_answer.macros, "match_macro", side_effect=fake_match), \
                mock.patch.object(
                    listen_and_answer, "run_storage_status_command", return_value="storage answer"
                ):
            speak = self._run_injected("good morning")

        spoken = [call.args[0] for call in speak.call_args_list]
        self.assertIn("storage answer", spoken)

    def test_self_referential_macro_is_stopped_not_recursed_forever(self):
        with mock.patch.object(
            listen_and_answer.macros, "match_macro",
            side_effect=lambda phrase: ["good morning"] if phrase == "good morning" else None,
        ):
            speak = self._run_injected("good morning")

        spoken = [call.args[0] for call in speak.call_args_list]
        self.assertTrue(any("loops back" in s for s in spoken), spoken)
        self.assertEqual(listen_and_answer._active_macro_triggers, set())


class RunDiagnosticCapabilityTests(unittest.TestCase):
    """The AI-tool entry point (ai_tools.run_atlas_diagnostic_or_repair) so
    diagnostics/self-heal/etc. work from any surface — voice fallback or
    the phone link — not just the fixed voice trigger phrases."""

    def test_every_advertised_capability_dispatches_to_its_handler(self):
        import ai_tools

        advertised = next(
            t for t in ai_tools.TOOLS
            if isinstance(t, dict) and t.get("name") == "run_atlas_diagnostic_or_repair"
        )["parameters"]["properties"]["capability"]["enum"]

        for capability in advertised:
            with mock.patch.dict(
                listen_and_answer.DIAGNOSTIC_CAPABILITY_HANDLERS,
                {capability: lambda: "handled"},
            ):
                self.assertEqual(
                    listen_and_answer.run_diagnostic_capability(capability), "handled"
                )

    def test_unknown_capability_is_reported_without_raising(self):
        answer = listen_and_answer.run_diagnostic_capability("not_a_real_capability")
        self.assertIn("not_a_real_capability", answer)

    def test_handler_exception_is_caught_and_reported(self):
        def _boom():
            raise RuntimeError("disk on fire")

        with mock.patch.dict(
            listen_and_answer.DIAGNOSTIC_CAPABILITY_HANDLERS, {"diagnostics": _boom}
        ):
            answer = listen_and_answer.run_diagnostic_capability("diagnostics")

        self.assertIn("disk on fire", answer)

    def test_self_heal_capability_is_wired_to_the_real_self_heal_function(self):
        self.assertIs(
            listen_and_answer.DIAGNOSTIC_CAPABILITY_HANDLERS["self_heal"],
            listen_and_answer.run_self_heal_command,
        )


if __name__ == "__main__":
    unittest.main()

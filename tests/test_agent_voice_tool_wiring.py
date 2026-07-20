import json
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import ai_tools
import listen_and_answer


def agent_response(
    text="Agent result.",
    input_tokens=7,
    output_tokens=3,
):
    return SimpleNamespace(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class FakeOwner:
    def __init__(self, response=None):
        self.response = response or agent_response()
        self.calls = []
        self.closed = False

    def handle_goal(self, goal, *, source="voice"):
        self.calls.append((goal, source))
        return self.response

    def close(self):
        self.closed = True


class StreamContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class AgentVoiceToolWiringTests(unittest.TestCase):
    def setUp(self):
        self.original_factory = (
            ai_tools._AGENT_RUNTIME_OWNER_FACTORY
        )
        ai_tools.close_agent_runtime_owner()
        ai_tools.clear_agent_usage()

    def tearDown(self):
        ai_tools.close_agent_runtime_owner()
        ai_tools.clear_agent_usage()

        if self.original_factory is not None:
            ai_tools.configure_agent_runtime_owner_factory(
                self.original_factory
            )
        else:
            with ai_tools._AGENT_RUNTIME_LOCK:
                ai_tools._AGENT_RUNTIME_OWNER_FACTORY = None
                ai_tools._AGENT_RUNTIME_OWNER = None

    def test_agent_tool_schema_is_strict(self):
        tool = next(
            item
            for item in ai_tools.TOOLS
            if isinstance(item, dict)
            and item.get("name") == "run_atlas_agent"
        )

        self.assertTrue(tool["strict"])
        self.assertEqual(
            tool["parameters"]["required"],
            ["goal"],
        )
        self.assertFalse(
            tool["parameters"]["additionalProperties"]
        )
        self.assertEqual(
            set(tool["parameters"]["properties"]),
            {"goal"},
        )

    def test_runtime_owner_is_lazy_and_safe_text_is_returned(self):
        created = []
        owner = FakeOwner(
            agent_response(
                text="Done. I verified the transfer.",
                input_tokens=11,
                output_tokens=4,
            )
        )

        def factory():
            created.append(True)
            return owner

        ai_tools.configure_agent_runtime_owner_factory(factory)

        self.assertEqual(created, [])

        result = ai_tools.run_tool_call(
            "run_atlas_agent",
            {"goal": "Copy my newest Atlas file."},
            source="phone",
        )

        self.assertEqual(created, [True])
        self.assertEqual(
            result,
            "Done. I verified the transfer.",
        )
        self.assertEqual(
            owner.calls,
            [("Copy my newest Atlas file.", "phone")],
        )
        self.assertEqual(
            ai_tools.consume_agent_usage(),
            (11, 4),
        )
        self.assertEqual(
            ai_tools.consume_agent_usage(),
            (0, 0),
        )

    def test_concurrent_threads_cannot_mix_nested_usage(self):
        thread_count = 8
        barrier = threading.Barrier(thread_count)
        created = []
        results = {}
        errors = []

        class ConcurrentOwner:
            def handle_goal(self, goal, *, source="voice"):
                value = int(goal)
                barrier.wait(timeout=5)
                return agent_response(
                    text=f"result-{value}",
                    input_tokens=value,
                    output_tokens=value + 100,
                )

            def close(self):
                pass

        def factory():
            created.append(True)
            return ConcurrentOwner()

        ai_tools.configure_agent_runtime_owner_factory(factory)

        def worker(value):
            try:
                ai_tools.clear_agent_usage()
                text = ai_tools.run_tool_call(
                    "run_atlas_agent",
                    {"goal": str(value)},
                    source=(
                        "voice"
                        if value % 2
                        else "phone"
                    ),
                )
                results[value] = (
                    text,
                    ai_tools.consume_agent_usage(),
                )
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=worker, args=(value,))
            for value in range(1, thread_count + 1)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(created, [True])
        self.assertEqual(len(results), thread_count)

        for value in range(1, thread_count + 1):
            self.assertEqual(
                results[value],
                (
                    f"result-{value}",
                    (value, value + 100),
                ),
            )

    def test_phone_counts_nested_planner_tokens_once(self):
        owner = FakeOwner(
            agent_response(
                text="Agent tool finished.",
                input_tokens=7,
                output_tokens=3,
            )
        )
        ai_tools.configure_agent_runtime_owner_factory(
            lambda: owner
        )

        call = SimpleNamespace(
            type="function_call",
            name="run_atlas_agent",
            arguments=json.dumps(
                {"goal": "Check visible PC apps."}
            ),
            call_id="call-1",
        )
        first_response = SimpleNamespace(
            output=[call],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=10,
            ),
            id="response-1",
            output_text="",
        )
        final_response = SimpleNamespace(
            output=[],
            usage=SimpleNamespace(
                input_tokens=50,
                output_tokens=5,
            ),
            id="response-2",
            output_text="Done.",
        )
        fake_client = SimpleNamespace(
            responses=SimpleNamespace(
                create=mock.Mock(
                    side_effect=[
                        first_response,
                        final_response,
                    ]
                )
            )
        )
        usage = {
            "month": "2026-07",
            "spent_usd": 0.0,
            "requests": 0,
        }

        with (
            mock.patch.object(
                listen_and_answer,
                "OpenAI",
                return_value=fake_client,
            ),
            mock.patch.object(
                listen_and_answer,
                "load_api_key",
                return_value="test-key",
            ),
            mock.patch.object(
                listen_and_answer,
                "load_usage",
                return_value=usage,
            ),
            mock.patch.object(
                listen_and_answer,
                "save_usage",
            ) as save_usage,
            mock.patch.object(
                listen_and_answer,
                "build_instructions_and_limits",
                return_value=("instructions", 200),
            ),
            mock.patch.object(
                listen_and_answer.memory_store,
                "record_turn",
            ),
        ):
            answer = listen_and_answer.answer_text_only(
                "Check visible PC apps."
            )

        self.assertEqual(answer, "Done.")
        self.assertEqual(
            owner.calls,
            [("Check visible PC apps.", "phone")],
        )

        saved_usage = save_usage.call_args.args[0]
        expected_cost = (
            157 * listen_and_answer.INPUT_PRICE_PER_TOKEN
            + 18 * listen_and_answer.OUTPUT_PRICE_PER_TOKEN
        )
        self.assertAlmostEqual(
            saved_usage["spent_usd"],
            expected_cost,
        )
        self.assertEqual(saved_usage["requests"], 1)
        self.assertEqual(
            ai_tools.consume_agent_usage(),
            (0, 0),
        )

    def test_streaming_voice_counts_nested_tokens_once(self):
        owner = FakeOwner(
            agent_response(
                text="Agent tool finished.",
                input_tokens=7,
                output_tokens=3,
            )
        )
        ai_tools.configure_agent_runtime_owner_factory(
            lambda: owner
        )

        call = SimpleNamespace(
            type="function_call",
            name="run_atlas_agent",
            arguments=json.dumps(
                {"goal": "Open an approved app."}
            ),
            call_id="call-voice",
        )
        first_response = SimpleNamespace(
            output=[call],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=10,
            ),
            id="response-voice-1",
        )
        final_response = SimpleNamespace(
            output=[],
            usage=SimpleNamespace(
                input_tokens=50,
                output_tokens=5,
            ),
            id="response-voice-2",
        )
        fake_client = SimpleNamespace(
            responses=SimpleNamespace(
                stream=mock.Mock(
                    side_effect=[
                        StreamContext(),
                        StreamContext(),
                    ]
                )
            )
        )
        consume_count = {"value": 0}

        def consume_stream(
            stream,
            text_state,
            sentence_queue,
            stop_event,
        ):
            consume_count["value"] += 1

            if consume_count["value"] == 1:
                return first_response

            text_state["full_text"] = "Done."
            text_state["buffer"] = "Done."
            return final_response

        sentence_queue = queue.Queue()
        stop_event = threading.Event()

        with (
            mock.patch.object(
                listen_and_answer,
                "_consume_openai_stream",
                side_effect=consume_stream,
            ),
            mock.patch.object(
                listen_and_answer,
                "_set_activity_label",
            ),
        ):
            answer, input_tokens, output_tokens = (
                listen_and_answer._stream_answer_sentences(
                    "Open an approved app.",
                    "instructions",
                    200,
                    fake_client,
                    sentence_queue,
                    stop_event,
                )
            )

        self.assertEqual(answer, "Done.")
        self.assertEqual(input_tokens, 157)
        self.assertEqual(output_tokens, 18)
        self.assertEqual(
            owner.calls,
            [("Open an approved app.", "voice")],
        )
        self.assertEqual(
            ai_tools.consume_agent_usage(),
            (0, 0),
        )


if __name__ == "__main__":
    unittest.main()

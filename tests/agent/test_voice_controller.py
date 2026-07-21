from types import SimpleNamespace

from atlas_agent.voice_controller import (
    AgentVoiceController,
)
from atlas_agent.workflow import WorkflowStatus


class FakeRuntime:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def run_goal(
        self,
        goal,
        *,
        source,
        metadata,
    ):
        self.calls.append(
            {
                "goal": goal,
                "source": source,
                "metadata": metadata,
            }
        )

        if self.error is not None:
            raise self.error

        return self.result


class FakeBundle:
    def __init__(self, runtime):
        self.runtime = runtime
        self.closed = False

    def close(self):
        self.closed = True


def make_result(
    workflow,
    *,
    input_tokens=200,
    output_tokens=40,
):
    return SimpleNamespace(
        task=SimpleNamespace(
            task_id="task-123",
        ),
        planning=SimpleNamespace(
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
        ),
        workflow=workflow,
    )


def make_step(
    *,
    position=1,
    description="Complete the action.",
    tool_name="pc.ensure_online",
    arguments=None,
    call_id="call-123",
    output=None,
    result_error=None,
    step_error=None,
    verification_reason="Verified.",
):
    return SimpleNamespace(
        position=position,
        description=description,
        call=SimpleNamespace(
            tool_name=tool_name,
            arguments=arguments or {},
            call_id=call_id,
        ),
        result=SimpleNamespace(
            output=output,
            error=result_error,
        ),
        verification=SimpleNamespace(
            reason=verification_reason,
        ),
        error=step_error,
    )


def test_completed_download_returns_spoken_summary_and_usage():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                position=1,
                description="Copy the file.",
                tool_name="pc.download_file",
                output={
                    "verified": True,
                    "local_path": (
                        "/home/atlas/atlas-staging/"
                        "incoming/ATLAS.mp4"
                    ),
                },
            ),
        ),
    )
    runtime = FakeRuntime(
        result=make_result(
            workflow,
            input_tokens=1403,
            output_tokens=127,
        )
    )
    controller = AgentVoiceController(
        FakeBundle(runtime)
    )

    response = controller.handle_goal(
        "Find and copy my Atlas video.",
        source="voice",
    )

    assert response.ok is True
    assert response.task_id == "task-123"
    assert response.workflow_status == "completed"
    assert response.input_tokens == 1403
    assert response.output_tokens == 127
    assert response.confirmation_call_id is None
    assert response.error is None
    assert response.text == (
        "Done. I copied ATLAS.mp4 from your PC "
        "and verified the transfer."
    )
    assert runtime.calls == [
        {
            "goal": (
                "Find and copy my Atlas video."
            ),
            "source": "voice",
            "metadata": {
                "agent_surface": "voice",
            },
        }
    ]


def test_waiting_confirmation_names_the_action():
    workflow = SimpleNamespace(
        status=(
            WorkflowStatus.WAITING_CONFIRMATION
        ),
        confirmation_call_id="call-send",
        error=None,
        steps=(
            make_step(
                description=(
                    "Send the drafted email."
                ),
                tool_name="gmail.send",
                call_id="call-send",
                output=None,
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Reply to the email.",
    )

    assert response.ok is False
    assert response.workflow_status == (
        "waiting_confirmation"
    )
    assert response.confirmation_call_id == (
        "call-send"
    )
    assert response.text == (
        "I need your confirmation before I "
        "send the drafted email. Say confirm "
        "that action or cancel."
    )


def test_failed_workflow_returns_recorded_reason():
    workflow = SimpleNamespace(
        status=WorkflowStatus.FAILED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                step_error=(
                    "The Windows PC is offline."
                ),
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Check the PC.",
    )

    assert response.ok is False
    assert response.workflow_status == "failed"
    assert response.text == (
        "I couldn't complete the workflow. "
        "The Windows PC is offline."
    )


def test_runtime_exception_becomes_safe_spoken_failure():
    runtime = FakeRuntime(
        error=RuntimeError(
            "secret internal traceback"
        )
    )
    bundle = FakeBundle(runtime)
    controller = AgentVoiceController(bundle)

    response = controller.handle_goal(
        "Do the thing.",
        source="phone",
    )

    assert response.ok is False
    assert response.task_id is None
    assert response.workflow_status is None
    assert response.input_tokens == 0
    assert response.output_tokens == 0
    assert response.text == (
        "I couldn't complete that agent "
        "request. The failure was recorded."
    )
    assert response.error == (
        "RuntimeError: secret internal traceback"
    )

    controller.close()

    assert bundle.closed is True



def test_pi_directory_listing_speaks_actual_names():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.list_directory",
                output={
                    "path": "/home/atlas/atlas-robot",
                    "entries": [
                        {
                            "name": "atlas_agent",
                            "path": (
                                "/home/atlas/atlas-robot/"
                                "atlas_agent"
                            ),
                            "type": "directory",
                            "size": None,
                        },
                        {
                            "name": "robot_hub.py",
                            "path": (
                                "/home/atlas/atlas-robot/"
                                "robot_hub.py"
                            ),
                            "type": "file",
                            "size": 100,
                        },
                    ],
                    "count": 2,
                    "total_count": 2,
                    "truncated": False,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "List the files in the Atlas robot project folder."
    )

    assert response.ok is True
    assert response.text == (
        "I found 2 items in atlas-robot: "
        "atlas_agent, robot_hub.py."
    )


def test_windows_search_speaks_matching_names():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pc.search_files",
                output=[
                    {
                        "name": "ATLAS.f3d",
                        "path": (
                            r"C:\Users\wesle\ATLAS.f3d"
                        ),
                    },
                    {
                        "name": "ATLAS.stl",
                        "path": (
                            r"C:\Users\wesle\ATLAS.stl"
                        ),
                    },
                ],
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Find my Atlas files on the PC."
    )

    assert response.text == (
        "I found 2 matching files in the approved "
        "Windows folders: ATLAS.f3d, ATLAS.stl."
    )



def test_pi_text_file_read_speaks_bounded_content():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.read_text_file",
                output={
                    "path": (
                        "/home/atlas/atlas-robot/"
                        "status.txt"
                    ),
                    "content": (
                        "Wake phrase: Hey Atlas.\n"
                        "Service: active."
                    ),
                    "start_line": 3,
                    "end_line": 4,
                    "line_count": 2,
                    "total_lines": 4,
                    "char_count": 39,
                    "size_bytes": 39,
                    "truncated": False,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Read the requested status file."
    )

    assert response.ok is True
    assert response.text == (
        "I read lines 3 through 4 of status.txt. "
        "Here is the text: Wake phrase: Hey Atlas.\n"
        "Service: active."
    )


def test_pi_search_files_speaks_matching_names():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.search_files",
                output={
                    "root": "/home/atlas/atlas-robot",
                    "query": "planner",
                    "entries": [
                        {
                            "name": "openai_planner.py",
                            "path": (
                                "/home/atlas/atlas-robot/"
                                "atlas_agent/"
                                "openai_planner.py"
                            ),
                            "relative_path": (
                                "atlas_agent/"
                                "openai_planner.py"
                            ),
                            "type": "file",
                            "size": 100,
                        },
                        {
                            "name": "test_openai_planner.py",
                            "path": (
                                "/home/atlas/atlas-robot/"
                                "tests/agent/"
                                "test_openai_planner.py"
                            ),
                            "relative_path": (
                                "tests/agent/"
                                "test_openai_planner.py"
                            ),
                            "type": "file",
                            "size": 200,
                        },
                    ],
                    "count": 2,
                    "truncated": False,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Find openai_planner.py in your project."
    )

    assert response.ok is True
    assert response.text == (
        "I found 2 matching files in the Atlas "
        "project: atlas_agent/openai_planner.py, "
        "tests/agent/test_openai_planner.py."
    )


def test_pi_search_files_speaks_empty_result():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.search_files",
                output={
                    "root": "/home/atlas/atlas-robot",
                    "query": "nonexistent",
                    "entries": [],
                    "count": 0,
                    "truncated": False,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Find nonexistent.py in your project."
    )

    assert response.text == (
        "I didn't find any matching files in the "
        "Atlas project."
    )


def test_pi_search_text_speaks_matching_lines():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.search_text",
                output={
                    "root": "/home/atlas/atlas-robot",
                    "query": "pc.open_app",
                    "matches": [
                        {
                            "path": (
                                "/home/atlas/atlas-robot/"
                                "atlas_agent/"
                                "voice_controller.py"
                            ),
                            "relative_path": (
                                "atlas_agent/"
                                "voice_controller.py"
                            ),
                            "line_number": 322,
                            "line": (
                                'if tool_name == '
                                '"pc.open_app":'
                            ),
                        },
                    ],
                    "count": 1,
                    "truncated": False,
                    "files_scanned": 42,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Search your code for pc.open_app."
    )

    assert response.ok is True
    assert response.text == (
        "I found 1 matching line in the Atlas "
        "project. atlas_agent/voice_controller.py "
        'line 322: if tool_name == "pc.open_app":.'
    )


def test_pi_search_text_speaks_empty_result():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.search_text",
                output={
                    "root": "/home/atlas/atlas-robot",
                    "query": "nonexistent_symbol",
                    "matches": [],
                    "count": 0,
                    "truncated": False,
                    "files_scanned": 10,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Search your code for nonexistent_symbol."
    )

    assert response.text == (
        "I didn't find any matching text in the "
        "Atlas project."
    )


def test_pi_read_service_logs_speaks_bounded_excerpt():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.read_service_logs",
                output={
                    "service": "atlas-wake.service",
                    "minutes": 10,
                    "lines": [
                        "2026-07-20T10:00:00 wake ready",
                        "2026-07-20T10:00:05 heard hey atlas",
                    ],
                    "count": 2,
                    "truncated": False,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Read your wake logs."
    )

    assert response.ok is True
    assert response.text == (
        "I checked 2 recent log lines for the "
        "A.T.L.A.S. wake service. Here is the "
        "latest: 2026-07-20T10:00:00 wake ready "
        "2026-07-20T10:00:05 heard hey atlas"
    )


def test_pi_read_service_logs_speaks_empty_result():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.read_service_logs",
                output={
                    "service": "atlas-robot.service",
                    "minutes": 10,
                    "lines": [],
                    "count": 0,
                    "truncated": False,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Read the robot service logs."
    )

    assert response.text == (
        "I checked the A.T.L.A.S. robot service, and "
        "there were no recent log lines."
    )


def test_pi_get_service_status_speaks_active_running():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.get_service_status",
                output={
                    "service": "atlas-wake.service",
                    "description": (
                        "A.T.L.A.S. Wake Word Listener"
                    ),
                    "load_state": "loaded",
                    "active_state": "active",
                    "sub_state": "running",
                    "main_pid": 1234,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Is your wake service running?"
    )

    assert response.ok is True
    assert response.text == (
        "The A.T.L.A.S. wake service is active and "
        "running."
    )


def test_pi_get_service_status_speaks_failed_state_truthfully():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.get_service_status",
                output={
                    "service": "atlas-wake.service",
                    "description": (
                        "A.T.L.A.S. Wake Word Listener"
                    ),
                    "load_state": "loaded",
                    "active_state": "failed",
                    "sub_state": "failed",
                    "main_pid": None,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(
                result=make_result(workflow)
            )
        )
    )

    response = controller.handle_goal(
        "Is your wake service running?"
    )

    assert response.text == (
        "The A.T.L.A.S. wake service is failed, with "
        "substate failed."
    )

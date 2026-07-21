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
    def __init__(self, runtime, *, executor=None, verifier=None):
        self.runtime = runtime
        self.executor = executor
        self.verifier = verifier
        self.closed = False

    def close(self):
        self.closed = True


class FakeExecutor:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def execute(self, call, *, confirmed):
        self.calls.append((call, confirmed))

        if self.error is not None:
            raise self.error

        return self.result


class FakeVerifier:
    def __init__(self, verification):
        self.verification = verification
        self.calls = []

    def verify(self, call, result):
        self.calls.append((call, result))
        return self.verification


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


def test_pi_get_upgrade_status_speaks_bounded_summary():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.get_upgrade_status",
                output={
                    "scope": "summary",
                    "finished_count": 4,
                    "remaining_count": 12,
                    "blocked_count": 1,
                    "total_count": 17,
                    "last_updated_feature": (
                        "Storage monitoring, thresholds, and safe cleanup"
                    ),
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("What is your upgrade status?")

    assert response.ok is True
    assert response.text == (
        "4 of 17 upgrade items are finished, 12 remain, and 1 "
        "are blocked on something external. The last thing I "
        "finished was: Storage monitoring, thresholds, and safe "
        "cleanup."
    )


def test_pi_get_upgrade_status_speaks_bounded_blocked_list():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.get_upgrade_status",
                output={
                    "scope": "blocked",
                    "count": 7,
                    "items": [
                        {"feature_id": f"phase{i}", "title": f"Feature {i}"}
                        for i in range(7)
                    ],
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("What upgrades are blocked?")

    assert response.ok is True
    assert response.text.startswith("7 upgrade items are blocked: ")
    assert "Feature 0" in response.text
    assert "Feature 4" in response.text
    assert "Feature 5" not in response.text
    assert "plus 2 more" in response.text


def test_pi_get_upgrade_status_speaks_empty_scope():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.get_upgrade_status",
                output={"scope": "blocked", "count": 0, "items": []},
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("What upgrades are blocked?")

    assert response.text == "No upgrade items are currently blocked."


def test_pi_get_mission_history_speaks_last_mission():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.get_mission_history",
                output={
                    "scope": "last",
                    "missions": [
                        {
                            "goal": "Read the hub logs",
                            "source": "voice",
                            "status": "completed",
                            "created_at": "2026-07-19T10:00:00+00:00",
                            "updated_at": "2026-07-19T10:00:05+00:00",
                            "note": None,
                        },
                    ],
                    "count": 1,
                    "total_count": 6,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("What was your last mission?")

    assert response.ok is True
    assert response.text == (
        "My last recorded mission was: Read the hub logs, "
        "which completed."
    )


def test_pi_get_mission_history_speaks_bounded_failed_list():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.get_mission_history",
                output={
                    "scope": "failed",
                    "missions": [
                        {
                            "goal": f"Mission {index}",
                            "source": "voice",
                            "status": "failed",
                            "created_at": "2026-07-19T10:00:00+00:00",
                            "updated_at": "2026-07-19T10:00:05+00:00",
                            "note": None,
                        }
                        for index in range(5)
                    ],
                    "count": 5,
                    "total_count": 9,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Did any missions fail recently?")

    assert response.ok is True
    assert response.text.startswith(
        "I have 5 recorded failed missions: "
    )
    assert "Mission 0, which failed" in response.text
    assert "Mission 2, which failed" in response.text
    assert "Mission 3" not in response.text
    assert "plus 2 more" in response.text


def test_pi_get_mission_history_speaks_empty_store():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.get_mission_history",
                output={
                    "scope": "failed",
                    "missions": [],
                    "count": 0,
                    "total_count": 4,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Did any missions fail recently?")

    assert response.text == "No recorded missions have failed."


def test_pi_explain_last_failure_speaks_recorded_evidence():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.explain_last_failure",
                output={
                    "window": 25,
                    "failed_mission": {
                        "goal": "Read the hub logs",
                        "source": "voice",
                        "status": "failed",
                        "created_at": "2026-07-19T10:00:00+00:00",
                        "updated_at": "2026-07-19T10:00:05+00:00",
                        "note": (
                            "Task was interrupted before completion."
                        ),
                    },
                    "last_error_interaction": {
                        "transcript": "read the hub logs",
                        "intent": "agent_goal",
                        "errors": [
                            "TimeoutError: planner timed out"
                        ],
                        "outcome": "error",
                        "timestamp": 1000.0,
                    },
                    "recent_incidents": [
                        {
                            "component": "hud",
                            "cause": "the HUD kiosk was not active",
                            "action": "restarted atlas-hud.service",
                            "verification": "service still not active",
                            "resolved": False,
                            "timestamp": 900.0,
                        },
                    ],
                    "incident_count": 1,
                    "evidence_found": True,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal(
        "Why did the last command fail?"
    )

    assert response.ok is True
    assert "My last failed mission was: Read the hub logs." in (
        response.text
    )
    assert (
        "The recorded reason is: Task was interrupted before "
        "completion." in response.text
    )
    assert (
        "The last logged error was: TimeoutError: planner "
        "timed out." in response.text
    )
    assert (
        "My latest unresolved incident is hud: service still "
        "not active." in response.text
    )


def test_pi_explain_last_failure_speaks_no_evidence():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.explain_last_failure",
                output={
                    "window": 25,
                    "failed_mission": None,
                    "last_error_interaction": None,
                    "recent_incidents": [],
                    "incident_count": 0,
                    "evidence_found": False,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal(
        "Why did the last command fail?"
    )

    assert response.text == (
        "I checked my mission history and logs, and I found no "
        "recorded failure to explain."
    )


def test_spoken_summary_for_diagnostics_all_healthy():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.run_diagnostics",
                output={
                    "components": ["disk", "wifi"],
                    "findings": [
                        {
                            "component": "disk",
                            "ok": True,
                            "detail": "disk 27% used",
                        },
                        {
                            "component": "wifi",
                            "ok": True,
                            "detail": "connected",
                        },
                    ],
                    "count": 2,
                    "ok_count": 2,
                    "problem_count": 0,
                    "all_ok": True,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Run diagnostics")

    assert "2" in response.text
    assert "pass" in response.text.lower()


def test_spoken_summary_for_diagnostics_reports_problems():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.run_diagnostics",
                output={
                    "components": ["wifi", "camera"],
                    "findings": [
                        {
                            "component": "wifi",
                            "ok": True,
                            "detail": "connected",
                        },
                        {
                            "component": "camera",
                            "ok": False,
                            "detail": (
                                "no camera device connected"
                            ),
                        },
                    ],
                    "count": 2,
                    "ok_count": 1,
                    "problem_count": 1,
                    "all_ok": False,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Run diagnostics")

    assert "camera" in response.text
    assert "no camera device connected" in response.text


def test_spoken_summary_for_resolved_recovery():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.recover_component",
                output={
                    "component": "hud",
                    "cause": "the HUD kiosk was not active",
                    "action": "restarted atlas-hud.service",
                    "verification": "service now active",
                    "resolved": True,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Fix the HUD")

    assert "hud" in response.text.lower()
    assert "restarted atlas-hud.service" in response.text
    assert "service now active" in response.text


def test_spoken_summary_for_recovery_noop_when_healthy():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.recover_component",
                output={
                    "component": "hud",
                    "cause": "the HUD kiosk was already active",
                    "action": "none",
                    "verification": "service reports active",
                    "resolved": True,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Fix the HUD")

    assert "already" in response.text.lower()


def test_spoken_summary_for_unresolved_recovery_is_truthful():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.recover_component",
                output={
                    "component": "camera",
                    "cause": "camera capture was failing",
                    "action": "re-probed the USB camera node",
                    "verification": (
                        "camera still not responding "
                        "(check USB connection)"
                    ),
                    "resolved": False,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Fix the camera")

    assert "couldn't" in response.text.lower()
    assert "camera still not responding" in response.text


def test_failure_explanation_mentions_retry_suggestion():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pi.explain_last_failure",
                output={
                    "window": 25,
                    "failed_mission": {
                        "goal": "Read the hub logs",
                        "status": "failed",
                        "note": None,
                    },
                    "last_error_interaction": None,
                    "recent_incidents": [
                        {
                            "component": "hud",
                            "cause": "kiosk down",
                            "action": "restart failed",
                            "verification": (
                                "service still not active"
                            ),
                            "resolved": False,
                            "timestamp": 1000.0,
                        },
                    ],
                    "incident_count": 1,
                    "evidence_found": True,
                    "suggested_retries": [
                        {
                            "action": "recover_component",
                            "component": "hud",
                            "reason": (
                                "service still not active"
                            ),
                        },
                        {
                            "action": "retry_mission",
                            "goal": "Read the hub logs",
                            "reason": "mission failed",
                        },
                    ],
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal(
        "Why did the last command fail?"
    )

    assert "hud" in response.text.lower()
    assert "recover" in response.text.lower()


def test_spoken_summary_for_focus_or_open_app_launched():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pc.focus_or_open_app",
                arguments={"app": "spotify"},
                output={
                    "ok": True,
                    "data": {
                        "ok": True,
                        "app": "spotify",
                        "action": "launched",
                    },
                    "error": None,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Open Spotify")

    assert response.text == "Done. I opened spotify."


def test_spoken_summary_for_focus_or_open_app_focused():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pc.focus_or_open_app",
                arguments={"app": "claude"},
                output={
                    "ok": True,
                    "data": {
                        "ok": True,
                        "app": "claude",
                        "action": "focused",
                    },
                    "error": None,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Open Claude")

    assert response.text == (
        "claude was already open — I brought it to the front."
    )


def test_spoken_summary_for_active_window_with_title():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pc.active_window",
                output={
                    "ok": True,
                    "data": {
                        "ok": True,
                        "title": "Fusion 360 - ATLAS.f3d",
                    },
                    "error": None,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal(
        "What's focused on my PC?"
    )

    assert response.text == (
        "You're focused on Fusion 360 - ATLAS.f3d on your PC."
    )


def test_spoken_summary_for_active_window_with_no_title():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="pc.active_window",
                output={
                    "ok": True,
                    "data": {"ok": True, "title": None},
                    "error": None,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal(
        "What's focused on my PC?"
    )

    assert response.text == (
        "I couldn't tell what's focused on your PC right now."
    )


def test_record_self_showcase_summary_names_file_and_invites_posting():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="content.record_self_showcase",
                output={
                    "ok": True,
                    "video_path": (
                        "/home/atlas/atlas-staging/incoming/"
                        "reel_123.mp4"
                    ),
                    "caption": "A quick look at my HUD.\n\n#atlas",
                    "mission": None,
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Record a promo Reel.")

    assert response.ok is True
    assert "reel_123.mp4" in response.text
    assert (
        "/home/atlas/atlas-staging/incoming/reel_123.mp4"
        in response.text
    )
    assert "A quick look at my HUD." in response.text
    assert "post it" in response.text.lower()


def test_record_self_showcase_summary_reports_real_failure():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="content.record_self_showcase",
                output={
                    "ok": False,
                    "error": "no HUD frames were captured",
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Record a promo Reel.")

    assert "no HUD frames were captured" in response.text


def test_publish_to_instagram_summary_success():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="content.publish_to_instagram",
                output={
                    "ok": True,
                    "permalink": (
                        "https://www.instagram.com/reel/ABC123/"
                    ),
                    "media_id": "999",
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Publish the reel.")

    assert response.ok is True
    assert (
        "https://www.instagram.com/reel/ABC123/" in response.text
    )


def test_publish_to_instagram_summary_reports_real_failure():
    workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(
                tool_name="content.publish_to_instagram",
                output={
                    "ok": False,
                    "error": "video file not found: /tmp/gone.mp4",
                },
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Publish the reel.")

    assert "video file not found: /tmp/gone.mp4" in response.text


def test_handle_goal_remembers_pending_confirmation():
    pending_call = SimpleNamespace(
        tool_name="content.publish_to_instagram",
        arguments={"video_path": "/tmp/reel.mp4", "caption": "hi"},
        call_id="call-publish",
    )
    workflow = SimpleNamespace(
        status=WorkflowStatus.WAITING_CONFIRMATION,
        confirmation_call_id="call-publish",
        error=None,
        steps=(
            SimpleNamespace(
                position=1,
                description="publish the Reel to Instagram",
                call=pending_call,
                result=None,
                verification=None,
                error=None,
            ),
        ),
    )
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime(result=make_result(workflow)))
    )

    response = controller.handle_goal("Record and post a Reel.")

    assert response.workflow_status == "waiting_confirmation"
    assert controller._pending is not None
    assert controller._pending.call is pending_call


def test_confirm_pending_with_nothing_pending():
    controller = AgentVoiceController(
        FakeBundle(FakeRuntime())
    )

    response = controller.confirm_pending(confirm=True)

    assert response.ok is False
    assert "nothing waiting" in response.text.lower()


def test_confirm_pending_false_cancels_without_executing():
    pending_call = SimpleNamespace(
        tool_name="content.publish_to_instagram",
        arguments={},
        call_id="call-publish",
    )
    workflow = SimpleNamespace(
        status=WorkflowStatus.WAITING_CONFIRMATION,
        confirmation_call_id="call-publish",
        error=None,
        steps=(
            SimpleNamespace(
                position=1,
                description="publish the Reel to Instagram.",
                call=pending_call,
                result=None,
                verification=None,
                error=None,
            ),
        ),
    )
    executor = FakeExecutor()
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(result=make_result(workflow)),
            executor=executor,
        )
    )
    controller.handle_goal("Record and post a Reel.")

    response = controller.confirm_pending(confirm=False)

    assert response.ok is True
    assert executor.calls == []
    assert controller._pending is None
    assert "won't" in response.text.lower()


def test_confirm_pending_true_executes_and_reports_success():
    pending_call = SimpleNamespace(
        tool_name="content.publish_to_instagram",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "caption": "hi",
            "mission": None,
        },
        call_id="call-publish",
    )
    workflow = SimpleNamespace(
        status=WorkflowStatus.WAITING_CONFIRMATION,
        confirmation_call_id="call-publish",
        error=None,
        steps=(
            SimpleNamespace(
                position=1,
                description="publish the Reel to Instagram.",
                call=pending_call,
                result=None,
                verification=None,
                error=None,
            ),
        ),
    )
    confirmed_result = SimpleNamespace(
        output={
            "ok": True,
            "permalink": "https://www.instagram.com/reel/XYZ/",
            "media_id": "1",
        },
        error=None,
    )
    executor = FakeExecutor(result=confirmed_result)
    verifier = FakeVerifier(
        SimpleNamespace(verified=True, reason="Verified live.")
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(result=make_result(workflow)),
            executor=executor,
            verifier=verifier,
        )
    )
    controller.handle_goal("Record and post a Reel.")

    response = controller.confirm_pending(confirm=True)

    assert executor.calls == [(pending_call, True)]
    assert verifier.calls == [(pending_call, confirmed_result)]
    assert response.ok is True
    assert response.workflow_status == "completed"
    assert (
        "https://www.instagram.com/reel/XYZ/" in response.text
    )
    assert controller._pending is None


def test_confirm_pending_true_reports_real_verification_failure():
    pending_call = SimpleNamespace(
        tool_name="content.publish_to_instagram",
        arguments={
            "video_path": "/tmp/reel.mp4",
            "caption": "hi",
            "mission": None,
        },
        call_id="call-publish",
    )
    workflow = SimpleNamespace(
        status=WorkflowStatus.WAITING_CONFIRMATION,
        confirmation_call_id="call-publish",
        error=None,
        steps=(
            SimpleNamespace(
                position=1,
                description="publish the Reel to Instagram.",
                call=pending_call,
                result=None,
                verification=None,
                error=None,
            ),
        ),
    )
    confirmed_result = SimpleNamespace(
        output={
            "ok": False,
            "error": "container processing failed with status ERROR",
        },
        error=None,
    )
    executor = FakeExecutor(result=confirmed_result)
    verifier = FakeVerifier(
        SimpleNamespace(
            verified=False,
            reason="The publish did not return a verified permalink.",
        )
    )
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(result=make_result(workflow)),
            executor=executor,
            verifier=verifier,
        )
    )
    controller.handle_goal("Record and post a Reel.")

    response = controller.confirm_pending(confirm=True)

    assert response.ok is False
    assert response.workflow_status == "failed"
    assert (
        "container processing failed with status ERROR"
        in response.text
    )


def test_confirm_pending_true_handles_executor_exception():
    pending_call = SimpleNamespace(
        tool_name="content.publish_to_instagram",
        arguments={},
        call_id="call-publish",
    )
    workflow = SimpleNamespace(
        status=WorkflowStatus.WAITING_CONFIRMATION,
        confirmation_call_id="call-publish",
        error=None,
        steps=(
            SimpleNamespace(
                position=1,
                description="publish the Reel to Instagram.",
                call=pending_call,
                result=None,
                verification=None,
                error=None,
            ),
        ),
    )
    executor = FakeExecutor(error=RuntimeError("boom"))
    controller = AgentVoiceController(
        FakeBundle(
            FakeRuntime(result=make_result(workflow)),
            executor=executor,
        )
    )
    controller.handle_goal("Record and post a Reel.")

    response = controller.confirm_pending(confirm=True)

    assert response.ok is False
    assert "RuntimeError: boom" in response.error


def test_new_goal_clears_stale_pending_confirmation():
    pending_call = SimpleNamespace(
        tool_name="content.publish_to_instagram",
        arguments={},
        call_id="call-publish",
    )
    waiting_workflow = SimpleNamespace(
        status=WorkflowStatus.WAITING_CONFIRMATION,
        confirmation_call_id="call-publish",
        error=None,
        steps=(
            SimpleNamespace(
                position=1,
                description="publish the Reel to Instagram.",
                call=pending_call,
                result=None,
                verification=None,
                error=None,
            ),
        ),
    )
    completed_workflow = SimpleNamespace(
        status=WorkflowStatus.COMPLETED,
        confirmation_call_id=None,
        error=None,
        steps=(
            make_step(tool_name="pc.ensure_online", output=None),
        ),
    )
    runtime = FakeRuntime(result=make_result(waiting_workflow))
    controller = AgentVoiceController(FakeBundle(runtime))
    controller.handle_goal("Record and post a Reel.")
    assert controller._pending is not None

    runtime.result = make_result(completed_workflow)
    controller.handle_goal("What's the weather?")

    assert controller._pending is None

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_agent.runtime_factory import RuntimeBundle
from atlas_agent.workflow import (
    WorkflowResult,
    WorkflowStatus,
)


@dataclass(frozen=True, slots=True)
class AgentVoiceResponse:
    text: str
    ok: bool
    task_id: str | None
    workflow_status: str | None
    confirmation_call_id: str | None
    input_tokens: int
    output_tokens: int
    error: str | None


class AgentVoiceController:
    """Serialized voice/phone entry point for the agent runtime."""

    def __init__(
        self,
        bundle: RuntimeBundle,
    ) -> None:
        self.bundle = bundle
        self._lock = threading.RLock()

    def handle_goal(
        self,
        goal: str,
        *,
        source: str = "voice",
    ) -> AgentVoiceResponse:
        with self._lock:
            try:
                result = self.bundle.runtime.run_goal(
                    goal,
                    source=source,
                    metadata={
                        "agent_surface": source,
                    },
                )
            except Exception as error:
                return AgentVoiceResponse(
                    text=(
                        "I couldn't complete that agent "
                        "request. The failure was recorded."
                    ),
                    ok=False,
                    task_id=None,
                    workflow_status=None,
                    confirmation_call_id=None,
                    input_tokens=0,
                    output_tokens=0,
                    error=(
                        f"{type(error).__name__}: "
                        f"{error}"
                    ),
                )

            workflow = result.workflow
            text = self._spoken_summary(workflow)

            return AgentVoiceResponse(
                text=text,
                ok=(
                    workflow.status
                    is WorkflowStatus.COMPLETED
                ),
                task_id=result.task.task_id,
                workflow_status=workflow.status.value,
                confirmation_call_id=(
                    workflow.confirmation_call_id
                ),
                input_tokens=(
                    result.planning.total_input_tokens
                ),
                output_tokens=(
                    result.planning.total_output_tokens
                ),
                error=workflow.error,
            )

    def close(self) -> None:
        with self._lock:
            self.bundle.close()

    def _spoken_summary(
        self,
        workflow: WorkflowResult,
    ) -> str:
        if (
            workflow.status
            is WorkflowStatus.COMPLETED
        ):
            return self._completed_summary(
                workflow
            )

        if (
            workflow.status
            is WorkflowStatus.WAITING_CONFIRMATION
        ):
            return self._confirmation_summary(
                workflow
            )

        return self._failure_summary(workflow)

    def _completed_summary(
        self,
        workflow: WorkflowResult,
    ) -> str:
        if not workflow.steps:
            return (
                "Done. The workflow completed "
                "and verified successfully."
            )

        final_step = workflow.steps[-1]
        tool_name = final_step.call.tool_name
        output = (
            final_step.result.output
            if final_step.result is not None
            else None
        )

        if (
            tool_name == "pc.download_file"
            and isinstance(output, dict)
            and output.get("verified") is True
        ):
            local_path = output.get(
                "local_path"
            )
            filename = (
                Path(local_path).name
                if isinstance(local_path, str)
                and local_path
                else "the file"
            )
            return (
                f"Done. I copied {filename} from "
                "your PC and verified the transfer."
            )

        if (
            tool_name == "pc.search_files"
            and isinstance(output, list)
        ):
            names = [
                item.get("name")
                for item in output
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item.get("name")
            ]
            count = len(output)
            noun = "file" if count == 1 else "files"

            if names:
                spoken_names = ", ".join(names[:10])
                remaining = count - len(names[:10])
                extra = (
                    f", plus {remaining} more"
                    if remaining > 0
                    else ""
                )
                return (
                    f"I found {count} matching {noun} "
                    "in the approved Windows folders: "
                    f"{spoken_names}{extra}."
                )

            return (
                f"I found {count} matching {noun} "
                "in the approved Windows folders."
            )

        if (
            tool_name == "pi.list_directory"
            and isinstance(output, dict)
        ):
            entries = output.get("entries")
            path = output.get("path")
            total_count = output.get("total_count")

            if isinstance(entries, list):
                names = [
                    item.get("name")
                    for item in entries
                    if isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and item.get("name")
                ]
                count = (
                    total_count
                    if isinstance(total_count, int)
                    else len(names)
                )
                noun = "item" if count == 1 else "items"

                if names:
                    spoken_names = ", ".join(names[:20])
                    remaining = count - len(names[:20])
                    extra = (
                        f", plus {remaining} more"
                        if remaining > 0
                        else ""
                    )
                    location = (
                        Path(path).name
                        if isinstance(path, str) and path
                        else "that folder"
                    )
                    return (
                        f"I found {count} {noun} in "
                        f"{location}: {spoken_names}{extra}."
                    )

                return "That Raspberry Pi folder is empty."

        if (
            tool_name == "pi.read_text_file"
            and isinstance(output, dict)
        ):
            path = output.get("path")
            content = output.get("content")
            start_line = output.get("start_line")
            end_line = output.get("end_line")
            truncated = output.get("truncated")

            filename = (
                Path(path).name
                if isinstance(path, str) and path
                else "the requested file"
            )

            if not isinstance(content, str) or not content.strip():
                return (
                    f"I read {filename}, but the requested "
                    "section is empty."
                )

            excerpt = content.strip()
            maximum_spoken_characters = 1500
            excerpt_was_truncated = (
                len(excerpt) > maximum_spoken_characters
            )

            if excerpt_was_truncated:
                excerpt = excerpt[
                    :maximum_spoken_characters
                ].rstrip()

            if (
                isinstance(start_line, int)
                and isinstance(end_line, int)
            ):
                if start_line == end_line:
                    location = f"line {start_line}"
                else:
                    location = (
                        f"lines {start_line} through "
                        f"{end_line}"
                    )
            else:
                location = "the requested section"

            continuation = (
                " The requested section continues beyond "
                "what I read aloud."
                if truncated is True
                or excerpt_was_truncated
                else ""
            )

            return (
                f"I read {location} of {filename}. "
                f"Here is the text: {excerpt}"
                f"{continuation}"
            )

        if (
            tool_name == "pi.search_files"
            and isinstance(output, dict)
        ):
            entries = output.get("entries")
            count = output.get("count")
            truncated = output.get("truncated")

            if isinstance(entries, list):
                names = [
                    item.get("relative_path")
                    for item in entries
                    if isinstance(item, dict)
                    and isinstance(
                        item.get("relative_path"), str
                    )
                    and item.get("relative_path")
                ]
                total = (
                    count
                    if isinstance(count, int)
                    else len(names)
                )
                noun = "file" if total == 1 else "files"

                if not names:
                    return (
                        "I didn't find any matching files "
                        "in the Atlas project."
                    )

                maximum_spoken_names = 10
                spoken_names = ", ".join(
                    names[:maximum_spoken_names]
                )
                remaining = total - len(
                    names[:maximum_spoken_names]
                )
                extra = (
                    f", plus {remaining} more"
                    if remaining > 0
                    else ""
                )
                continuation = (
                    " There may be more matches beyond "
                    "what I searched."
                    if truncated is True
                    else ""
                )

                return (
                    f"I found {total} matching {noun} in "
                    f"the Atlas project: {spoken_names}"
                    f"{extra}.{continuation}"
                )

        if (
            tool_name == "pi.search_text"
            and isinstance(output, dict)
        ):
            matches = output.get("matches")
            count = output.get("count")
            truncated = output.get("truncated")

            if isinstance(matches, list):
                if not matches:
                    return (
                        "I didn't find any matching text "
                        "in the Atlas project."
                    )

                total = (
                    count
                    if isinstance(count, int)
                    else len(matches)
                )
                noun = "line" if total == 1 else "lines"
                maximum_spoken_matches = 5
                spoken_matches = []

                for match in matches[
                    :maximum_spoken_matches
                ]:
                    if not isinstance(match, dict):
                        continue

                    relative_path = match.get(
                        "relative_path"
                    )
                    line_number = match.get(
                        "line_number"
                    )
                    line = match.get("line")

                    if (
                        isinstance(relative_path, str)
                        and isinstance(line_number, int)
                        and isinstance(line, str)
                    ):
                        spoken_matches.append(
                            f"{relative_path} line "
                            f"{line_number}: {line.strip()}"
                        )

                remaining = total - len(spoken_matches)
                extra = (
                    f" I found {remaining} more matches "
                    "I didn't read aloud."
                    if remaining > 0
                    else ""
                )
                continuation = (
                    " There may be more matches beyond "
                    "what I searched."
                    if truncated is True and remaining <= 0
                    else ""
                )
                joined = "; ".join(spoken_matches)

                return (
                    f"I found {total} matching {noun} in "
                    f"the Atlas project. {joined}."
                    f"{extra}{continuation}"
                )

        if (
            tool_name == "pi.read_service_logs"
            and isinstance(output, dict)
        ):
            service = output.get("service")
            lines = output.get("lines")
            count = output.get("count")

            service_label = self._service_spoken_label(
                service
            )

            if (
                not isinstance(lines, list)
                or not lines
            ):
                return (
                    f"I checked the {service_label}, and "
                    "there were no recent log lines."
                )

            total = (
                count
                if isinstance(count, int)
                else len(lines)
            )
            maximum_spoken_lines = 5
            excerpt_lines = [
                line.strip()
                for line in lines[-maximum_spoken_lines:]
                if isinstance(line, str)
            ]
            excerpt = " ".join(excerpt_lines)

            return (
                f"I checked {total} recent log lines for "
                f"the {service_label}. Here is the "
                f"latest: {excerpt}"
            )

        if (
            tool_name == "pi.get_service_status"
            and isinstance(output, dict)
        ):
            service = output.get("service")
            active_state = output.get("active_state")
            sub_state = output.get("sub_state")

            service_label = self._service_spoken_label(
                service
            )

            if not (
                isinstance(active_state, str)
                and isinstance(sub_state, str)
            ):
                return (
                    f"I checked the {service_label}, but "
                    "its status was incomplete."
                )

            if (
                active_state == "active"
                and sub_state == "running"
            ):
                return (
                    f"The {service_label} is active "
                    "and running."
                )

            return (
                f"The {service_label} is {active_state}, "
                f"with substate {sub_state}."
            )

        if (
            tool_name == "pc.ensure_online"
            and isinstance(output, dict)
        ):
            message = output.get("message")

            if isinstance(message, str):
                return message

        if (
            tool_name == "pc.active_apps"
            and isinstance(output, dict)
        ):
            data = output.get("data")
            windows = (
                data.get("windows")
                if isinstance(data, dict)
                else None
            )

            if isinstance(windows, list):
                count = len(windows)
                return (
                    f"I checked your PC. It has "
                    f"{count} visible windows open."
                )

        if tool_name == "pc.open_app":
            app = final_step.call.arguments.get(
                "app"
            )

            if isinstance(app, str) and app:
                return f"Done. I opened {app}."

        step_count = len(workflow.steps)
        noun = (
            "step"
            if step_count == 1
            else "steps"
        )
        return (
            f"Done. I completed and verified "
            f"all {step_count} {noun}."
        )

    @staticmethod
    def _service_spoken_label(service: Any) -> str:
        if not isinstance(service, str) or not service:
            return "requested A.T.L.A.S. service"

        short_name = service

        if short_name.endswith(".service"):
            short_name = short_name[: -len(".service")]

        if short_name.startswith("atlas-"):
            short_name = short_name[len("atlas-"):]

        return f"A.T.L.A.S. {short_name} service"

    def _confirmation_summary(
        self,
        workflow: WorkflowResult,
    ) -> str:
        call_id = workflow.confirmation_call_id
        description = None

        if call_id:
            for step in workflow.steps:
                if step.call.call_id == call_id:
                    description = step.description
                    break

        if description:
            return (
                f"I need your confirmation before I "
                f"{description.rstrip('.').lower()}. "
                "Say confirm that action or cancel."
            )

        return (
            "I need your confirmation before I "
            "continue. Say confirm that action "
            "or cancel."
        )

    def _failure_summary(
        self,
        workflow: WorkflowResult,
    ) -> str:
        reason = self._failure_reason(
            workflow
        )

        if reason:
            return (
                "I couldn't complete the workflow. "
                f"{reason}"
            )

        return (
            "I couldn't complete the workflow. "
            "The failure was recorded."
        )

    @staticmethod
    def _failure_reason(
        workflow: WorkflowResult,
    ) -> str | None:
        candidates: list[Any] = [
            workflow.error,
        ]

        for step in reversed(workflow.steps):
            candidates.append(step.error)

            if step.result is not None:
                candidates.append(
                    step.result.error
                )

            if step.verification is not None:
                candidates.append(
                    step.verification.reason
                )

        for candidate in candidates:
            if (
                isinstance(candidate, str)
                and candidate.strip()
            ):
                cleaned = " ".join(
                    candidate.split()
                )
                return cleaned[:240]

        return None

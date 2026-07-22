from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_agent.events import AtlasEvent
from atlas_agent.runtime_factory import RuntimeBundle
from atlas_agent.tasks import ToolCall
from atlas_agent.workflow import (
    StepOutcome,
    WorkflowResult,
    WorkflowStatus,
)

_MISSION_STATUS_PHRASES = {
    "queued": "is queued",
    "running": "is still running",
    "waiting_confirmation": (
        "is waiting for confirmation"
    ),
    "completed": "completed",
    "failed": "failed",
    "cancelled": "was cancelled",
}

_REEL_PUBLISH_TOOLS = frozenset({
    "content.publish_to_instagram",
    "content.publish_to_facebook",
    "content.publish_to_youtube",
    "content.publish_to_socials",
})


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


@dataclass(frozen=True, slots=True)
class _PendingConfirmation:
    """One paused CONFIRMATION_REQUIRED call, durably stored when it is
    a social publish, so a
    later "yes, post it" turn can actually resume the exact call that
    asked for confirmation instead of starting a brand new, unrelated
    mission with no idea what file/caption it's supposed to act on.

    Confirmed live 2026-07-21: without this, "post the video" as a
    separate follow-up utterance always failed -- AgentRuntime.run_goal
    always builds a fresh plan from scratch for a new goal string, so a
    second, independent mission has no access to the first mission's
    concrete tool output (e.g. content.record_self_showcase's actual
    video_path), and each social publishing schema requires an
    exact non-null path -- there was never a way for that second
    mission to know it."""

    task_id: str
    call: Any
    description: str


class AgentVoiceController:
    """Serialized voice/phone entry point for the agent runtime."""

    def __init__(
        self,
        bundle: RuntimeBundle,
    ) -> None:
        self.bundle = bundle
        self._lock = threading.RLock()
        staging_directory = getattr(
            bundle,
            "staging_directory",
            None,
        )
        self._staging_directory = (
            Path(staging_directory).expanduser().resolve()
            if staging_directory is not None
            else None
        )
        self._pending_path = (
            self._staging_directory
            / "pending_instagram_publish.json"
            if self._staging_directory is not None
            else None
        )
        registry = getattr(bundle, "registry", None)
        self._facebook_publish_enabled = bool(
            registry is not None
            and "content.publish_to_facebook" in registry
        )
        self._youtube_publish_enabled = bool(
            registry is not None
            and "content.publish_to_youtube" in registry
        )
        self._combined_social_publish_enabled = bool(
            registry is not None
            and "content.publish_to_socials" in registry
        )
        self._pending = self._load_pending()

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
            pending = self._extract_pending(result.task, workflow)
            synthetic_confirmation = False

            if pending is None:
                pending = self._pending_for_completed_reel(
                    result.task,
                    workflow,
                    requested_goal=goal,
                    facebook_publish_enabled=(
                        self._facebook_publish_enabled
                    ),
                    youtube_publish_enabled=(
                        self._youtube_publish_enabled
                    ),
                    combined_social_publish_enabled=(
                        self._combined_social_publish_enabled
                    ),
                )
                synthetic_confirmation = pending is not None

            self._set_pending(pending)

            if synthetic_confirmation:
                self._publish_waiting_confirmation(
                    result.task,
                    workflow,
                    pending,
                )
                if (
                    pending.call.tool_name
                    == "content.publish_to_youtube"
                ):
                    question = (
                        "Do you want me to upload it privately to YouTube "
                        "and copy it to your Desktop, save it to your Desktop "
                        "without posting, or delete it?"
                    )
                    if "Do you want me to" in text:
                        text = text.split("Do you want me to", 1)[0] + question
                    else:
                        text = f"{text.rstrip()} {question}"
                elif (
                    pending.call.tool_name
                    == "content.publish_to_facebook"
                ):
                    question = (
                        "Do you want me to publish it to the ATLAS AI "
                        "Robot Facebook Page and copy it to your Desktop, "
                        "save it to your Desktop without posting, or delete it?"
                    )
                    if "Do you want me to" in text:
                        text = text.split("Do you want me to", 1)[0] + question
                    else:
                        text = f"{text.rstrip()} {question}"
                elif (
                    pending.call.tool_name
                    == "content.publish_to_socials"
                ):
                    question = (
                        "Do you want me to publish this exact Reel to both "
                        "Instagram and Facebook and copy it to your Desktop, "
                        "save it to your Desktop without posting, or delete it?"
                    )
                    if "Do you want me to" in text:
                        text = text.split("Do you want me to", 1)[0] + question
                    else:
                        text = f"{text.rstrip()} {question}"

            response_status = (
                WorkflowStatus.WAITING_CONFIRMATION
                if synthetic_confirmation
                else workflow.status
            )
            confirmation_call_id = (
                pending.call.call_id
                if synthetic_confirmation
                else workflow.confirmation_call_id
            )

            return AgentVoiceResponse(
                text=text,
                ok=(
                    workflow.status
                    is WorkflowStatus.COMPLETED
                ),
                task_id=result.task.task_id,
                workflow_status=response_status.value,
                confirmation_call_id=confirmation_call_id,
                input_tokens=(
                    result.planning.total_input_tokens
                ),
                output_tokens=(
                    result.planning.total_output_tokens
                ),
                error=workflow.error,
            )

    def confirm_pending(self, *, confirm: bool) -> AgentVoiceResponse:
        """Backward-compatible yes/no wrapper (no now means save)."""
        return self.resolve_pending(action="post" if confirm else "save")

    def resolve_pending(self, *, action: str) -> AgentVoiceResponse:
        """Resolve a finished Reel as post, save-to-Desktop, or delete."""
        choice = str(action or "").strip().casefold()
        if choice not in {"post", "save", "delete"}:
            return AgentVoiceResponse(
                text="Choose post, save, or delete for the finished Reel.",
                ok=False,
                task_id=None,
                workflow_status=None,
                confirmation_call_id=None,
                input_tokens=0,
                output_tokens=0,
                error="Invalid pending Reel action.",
            )

        with self._lock:
            pending = self._pending

            if pending is None:
                pending = self._recover_latest_reel()

            if pending is None:
                return AgentVoiceResponse(
                    text=(
                        "There's nothing waiting for "
                        "confirmation right now."
                    ),
                    ok=False,
                    task_id=None,
                    workflow_status=None,
                    confirmation_call_id=None,
                    input_tokens=0,
                    output_tokens=0,
                    error=None,
                )

            is_reel = pending.call.tool_name in _REEL_PUBLISH_TOOLS
            if not is_reel and choice != "post":
                self._set_pending(None)
                self._publish_confirmation_terminal(
                    pending,
                    status=WorkflowStatus.COMPLETED,
                    error=None,
                )
                return AgentVoiceResponse(
                    text=(
                        "Okay, I won't "
                        f"{pending.description.rstrip('.').lower()}."
                    ),
                    ok=True,
                    task_id=pending.task_id,
                    workflow_status="cancelled",
                    confirmation_call_id=None,
                    input_tokens=0,
                    output_tokens=0,
                    error=None,
                )

            if is_reel and choice == "delete":
                # Consume before the destructive action so a crash or partial
                # delete cannot leave a stale prompt that targets missing data.
                self._set_pending(None)
                file_action = self._execute_reel_file_action(
                    pending,
                    tool_name="content.delete_showcase",
                    confirmed=True,
                )
                if file_action is None:
                    return AgentVoiceResponse(
                        text="I couldn't delete that Reel because the delete tool is unavailable.",
                        ok=False,
                        task_id=pending.task_id,
                        workflow_status="failed",
                        confirmation_call_id=None,
                        input_tokens=0,
                        output_tokens=0,
                        error="Reel delete tool unavailable.",
                    )
                _, verification = file_action
                self._publish_confirmation_terminal(
                    pending,
                    status=(
                        WorkflowStatus.COMPLETED
                        if verification.verified
                        else WorkflowStatus.FAILED
                    ),
                    error=None if verification.verified else verification.reason,
                )
                return AgentVoiceResponse(
                    text=(
                        "Deleted. I removed the new Reel and its local package; it was not posted or copied to your Desktop."
                        if verification.verified
                        else f"I couldn't fully delete that Reel: {verification.reason}"
                    ),
                    ok=verification.verified,
                    task_id=pending.task_id,
                    workflow_status=("completed" if verification.verified else "failed"),
                    confirmation_call_id=None,
                    input_tokens=0,
                    output_tokens=0,
                    error=None if verification.verified else verification.reason,
                )

            if is_reel:
                file_action = self._execute_reel_file_action(
                    pending,
                    tool_name="content.save_showcase",
                    confirmed=False,
                )
                if file_action is not None:
                    save_result, save_verification = file_action
                    if not save_verification.verified:
                        # Keep the pending choice so the owner can retry after
                        # the PC/Desktop transfer problem is repaired.
                        return AgentVoiceResponse(
                            text=(
                                "I kept the Reel safely on Atlas, but I couldn't verify the required Desktop copy: "
                                f"{save_verification.reason}"
                            ),
                            ok=False,
                            task_id=pending.task_id,
                            workflow_status="failed",
                            confirmation_call_id=pending.call.call_id,
                            input_tokens=0,
                            output_tokens=0,
                            error=save_verification.reason,
                        )
                    if choice == "save":
                        self._set_pending(None)
                        self._publish_confirmation_terminal(
                            pending,
                            status=WorkflowStatus.COMPLETED,
                            error=None,
                        )
                        output = save_result.output if save_result is not None else {}
                        folder = output.get("folder") if isinstance(output, dict) else None
                        return AgentVoiceResponse(
                            text=(
                                "Saved. I verified the new Reel on your Windows Desktop"
                                + (f" in {folder}" if folder else "")
                                + ". I did not post it."
                            ),
                            ok=True,
                            task_id=pending.task_id,
                            workflow_status="completed",
                            confirmation_call_id=None,
                            input_tokens=0,
                            output_tokens=0,
                            error=None,
                        )
                elif choice == "save":
                    # Compatibility for older/minimal runtimes without the new
                    # Desktop tool. Production Atlas always registers it.
                    self._set_pending(None)
                    return AgentVoiceResponse(
                        text="Saved locally. I won't post it.",
                        ok=True,
                        task_id=pending.task_id,
                        workflow_status="completed",
                        confirmation_call_id=None,
                        input_tokens=0,
                        output_tokens=0,
                        error=None,
                    )

            # Consume the durable confirmation before the irreversible post.
            # A process crash after a platform accepts the Reel must never
            # leave a stale token that can publish it twice.
            self._set_pending(None)

            try:
                result = self.bundle.executor.execute(
                    pending.call, confirmed=True
                )
            except Exception as error:
                return AgentVoiceResponse(
                    text=(
                        "I couldn't complete that confirmed "
                        "action. The failure was recorded."
                    ),
                    ok=False,
                    task_id=pending.task_id,
                    workflow_status=None,
                    confirmation_call_id=None,
                    input_tokens=0,
                    output_tokens=0,
                    error=(
                        f"{type(error).__name__}: {error}"
                    ),
                )

            verification = self.bundle.verifier.verify(
                pending.call, result
            )
            synthetic_workflow = WorkflowResult(
                task_id=pending.task_id,
                plan_id="confirmation",
                status=(
                    WorkflowStatus.COMPLETED
                    if verification.verified
                    else WorkflowStatus.FAILED
                ),
                steps=(
                    StepOutcome(
                        position=1,
                        description=pending.description,
                        call=pending.call,
                        result=result,
                        verification=verification,
                        error=(
                            None
                            if verification.verified
                            else verification.reason
                        ),
                    ),
                ),
                failed_step=(
                    None if verification.verified else 1
                ),
                confirmation_call_id=None,
                error=(
                    None
                    if verification.verified
                    else verification.reason
                ),
            )
            text = self._spoken_summary(synthetic_workflow)
            self._publish_confirmation_terminal(
                pending,
                status=synthetic_workflow.status,
                error=synthetic_workflow.error,
            )

            return AgentVoiceResponse(
                text=text,
                ok=verification.verified,
                task_id=pending.task_id,
                workflow_status=synthetic_workflow.status.value,
                confirmation_call_id=None,
                input_tokens=0,
                output_tokens=0,
                error=synthetic_workflow.error,
            )

    def _execute_reel_file_action(
        self,
        pending: _PendingConfirmation,
        *,
        tool_name: str,
        confirmed: bool,
    ) -> tuple[Any, Any] | None:
        registry = getattr(self.bundle, "registry", None)
        if registry is None or tool_name not in registry:
            return None

        video_path = pending.call.arguments.get("video_path")
        if not isinstance(video_path, str) or not video_path:
            return None
        reel = Path(video_path).expanduser()
        package = reel.with_name(f"{reel.stem}_package")
        call = ToolCall(
            tool_name=tool_name,
            arguments={
                "video_path": video_path,
                "package_path": str(package) if package.is_dir() else None,
            },
            task_id=pending.task_id,
        )
        try:
            result = self.bundle.executor.execute(call, confirmed=confirmed)
            verification = self.bundle.verifier.verify(call, result)
        except Exception as error:
            result = type("FailedReelFileAction", (), {
                "output": {"ok": False, "error": f"{type(error).__name__}: {error}"},
                "error": str(error),
            })()
            verification = type("FailedReelFileVerification", (), {
                "verified": False,
                "reason": f"{type(error).__name__}: {error}",
            })()
        return result, verification

    @staticmethod
    def _extract_pending(
        task: Any,
        workflow: WorkflowResult,
    ) -> _PendingConfirmation | None:
        if (
            workflow.status
            is not WorkflowStatus.WAITING_CONFIRMATION
        ):
            return None

        for step in workflow.steps:
            if step.call.call_id == workflow.confirmation_call_id:
                return _PendingConfirmation(
                    task_id=task.task_id,
                    call=step.call,
                    description=step.description,
                )

        return None

    @staticmethod
    def _pending_for_completed_reel(
        task: Any,
        workflow: WorkflowResult,
        *,
        requested_goal: str | None = None,
        facebook_publish_enabled: bool = False,
        youtube_publish_enabled: bool = False,
        combined_social_publish_enabled: bool = False,
    ) -> _PendingConfirmation | None:
        """Turn a successful record-only Reel workflow into the exact
        publish confirmation its spoken summary promises.

        The planner is allowed to produce only the recording step for a
        goal such as "record yourself for Instagram". Previously that
        completed workflow said "say post it" but stored no pending call,
        so the follow-up could never work. Build the Level-2 publish call
        directly from the verified recording output instead of asking a
        later planner to rediscover an in-memory path and caption.
        """
        if (
            workflow.status is not WorkflowStatus.COMPLETED
            or not workflow.steps
        ):
            return None

        reel_step = next(
            (
                step
                for step in reversed(workflow.steps)
                if step.call.tool_name == "content.record_self_showcase"
                and step.result is not None
                and isinstance(step.result.output, dict)
                and step.result.output.get("ok") is True
            ),
            None,
        )

        if reel_step is None:
            return None

        output = (
            reel_step.result.output
            if reel_step.result is not None
            else None
        )

        if not isinstance(output, dict) or output.get("ok") is not True:
            return None

        video_path = output.get("video_path")
        caption = output.get("caption")

        if (
            not isinstance(video_path, str)
            or not video_path.strip()
            or not isinstance(caption, str)
        ):
            return None

        goal = str(
            requested_goal
            if requested_goal is not None
            else getattr(task, "goal", "")
        ).casefold()
        youtube_requested = youtube_publish_enabled and (
            "youtube" in goal or "you tube" in goal
        )
        facebook_requested = facebook_publish_enabled and (
            "facebook" in goal or "face book" in goal
        )
        growth_plan = output.get("growth_plan")
        growth_title = (
            growth_plan.get("title")
            if isinstance(growth_plan, dict)
            else None
        )
        first_line = next(
            (
                line.strip()
                for line in caption.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ),
            "",
        )
        title = str(
            growth_title
            or first_line
            or output.get("mission")
            or "A.T.L.A.S. Raspberry Pi Project"
        ).strip()
        if combined_social_publish_enabled:
            tool_name = "content.publish_to_socials"
            arguments = {
                "video_path": video_path,
                "title": title[:255],
                "caption": caption,
                "mission": output.get("mission"),
            }
            description = (
                "Publish the finished Reel to both Instagram and the ATLAS AI "
                "Robot Facebook Page."
            )
        elif facebook_requested:
            tool_name = "content.publish_to_facebook"
            arguments = {
                "video_path": video_path,
                "title": title[:255],
                "description": caption,
                "mission": output.get("mission"),
            }
            description = (
                "Publish the finished Reel to the ATLAS AI Robot Facebook Page."
            )
        elif youtube_requested:
            tool_name = "content.publish_to_youtube"
            arguments = {
                "video_path": video_path,
                "title": title[:100],
                "description": caption,
                # Google's current API policy forces uploads from an
                # unaudited project to private regardless. Keep Atlas's
                # default honest and safe until that audit is complete.
                "privacy_status": "private",
                "mission": output.get("mission"),
            }
            description = "Upload the finished Short privately to YouTube."
        else:
            tool_name = "content.publish_to_instagram"
            arguments = {
                "video_path": video_path,
                "caption": caption,
                "mission": output.get("mission"),
            }
            description = "Publish the finished Reel to Instagram."

        call = ToolCall(
            tool_name=tool_name,
            arguments=arguments,
            task_id=task.task_id,
        )
        return _PendingConfirmation(
            task_id=task.task_id,
            call=call,
            description=description,
        )

    def _set_pending(
        self,
        pending: _PendingConfirmation | None,
    ) -> None:
        self._pending = pending
        path = self._pending_path

        if path is None:
            return

        if pending is None:
            path.unlink(missing_ok=True)
            return

        if pending.call.tool_name not in {
            "content.publish_to_instagram",
            "content.publish_to_facebook",
            "content.publish_to_youtube",
            "content.publish_to_socials",
        }:
            path.unlink(missing_ok=True)
            return
        if (
            pending.call.tool_name == "content.publish_to_facebook"
            and not self._facebook_publish_enabled
        ):
            path.unlink(missing_ok=True)
            return
        if (
            pending.call.tool_name == "content.publish_to_youtube"
            and not self._youtube_publish_enabled
        ):
            path.unlink(missing_ok=True)
            return
        if (
            pending.call.tool_name == "content.publish_to_socials"
            and not self._combined_social_publish_enabled
        ):
            path.unlink(missing_ok=True)
            return
        if (
            self._combined_social_publish_enabled
            and pending.call.tool_name != "content.publish_to_socials"
        ):
            path.unlink(missing_ok=True)
            return

        arguments = self._validated_publish_arguments(
            pending.call.arguments,
            pending.call.tool_name,
        )
        if arguments is None:
            path.unlink(missing_ok=True)
            return

        payload = {
            "version": 2,
            "task_id": pending.task_id,
            "call_id": pending.call.call_id,
            "description": pending.description,
            "tool_name": pending.call.tool_name,
            "arguments": arguments,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    def _load_pending(self) -> _PendingConfirmation | None:
        path = self._pending_path
        if path is None or not path.is_file():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("version") not in {1, 2}
            ):
                raise ValueError("invalid pending publish record")
            tool_name = payload.get("tool_name")
            if tool_name not in {
                "content.publish_to_instagram",
                "content.publish_to_facebook",
                "content.publish_to_youtube",
                "content.publish_to_socials",
            }:
                raise ValueError("invalid pending publish tool")
            if (
                tool_name == "content.publish_to_facebook"
                and not self._facebook_publish_enabled
            ):
                raise ValueError("Facebook publishing is disabled")
            if (
                tool_name == "content.publish_to_youtube"
                and not self._youtube_publish_enabled
            ):
                raise ValueError("YouTube publishing is disabled")
            if (
                tool_name == "content.publish_to_socials"
                and not self._combined_social_publish_enabled
            ):
                raise ValueError("Combined social publishing is disabled")
            if (
                self._combined_social_publish_enabled
                and tool_name != "content.publish_to_socials"
            ):
                raise ValueError("Old single-platform confirmation discarded")
            task_id = payload.get("task_id")
            call_id = payload.get("call_id")
            description = payload.get("description")
            arguments = self._validated_publish_arguments(
                payload.get("arguments"),
                tool_name,
            )
            if (
                not isinstance(task_id, str)
                or not task_id
                or not isinstance(call_id, str)
                or not call_id
                or not isinstance(description, str)
                or not description
                or arguments is None
            ):
                raise ValueError("invalid pending publish fields")
        except (OSError, ValueError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return None

        return _PendingConfirmation(
            task_id=task_id,
            call=ToolCall(
                tool_name=tool_name,
                arguments=arguments,
                task_id=task_id,
                call_id=call_id,
            ),
            description=description,
        )

    def _recover_latest_reel(self) -> _PendingConfirmation | None:
        """Recover the newest locally verified Reel after an upgrade.

        Older builds did not persist pending confirmations. This fallback
        runs only after an explicit affirmative publish command and accepts
        only a sidecar whose MP4 resolves inside the configured staging
        directory.
        """
        staging = self._staging_directory
        if staging is None or not staging.is_dir():
            return None

        manifests = sorted(
            staging.glob("reel_*.mp4.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for manifest in manifests:
            try:
                payload = json.loads(
                    manifest.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            caption = payload.get("caption")
            mission = payload.get("mission")
            if self._combined_social_publish_enabled:
                first_line = next(
                    (
                        line.strip()
                        for line in str(caption or "").splitlines()
                        if line.strip() and not line.lstrip().startswith("#")
                    ),
                    "A.T.L.A.S. Raspberry Pi Project",
                )
                tool_name = "content.publish_to_socials"
                candidate_arguments = {
                    "video_path": payload.get("video_path"),
                    "title": first_line[:255],
                    "caption": caption,
                    "mission": mission,
                }
            else:
                tool_name = "content.publish_to_instagram"
                candidate_arguments = {
                    "video_path": payload.get("video_path"),
                    "caption": caption,
                    "mission": mission,
                }
            arguments = self._validated_publish_arguments(
                candidate_arguments, tool_name
            )
            if arguments is None:
                continue
            task_id = f"recovered-{Path(arguments['video_path']).stem}"
            pending = _PendingConfirmation(
                task_id=task_id,
                call=ToolCall(
                    tool_name=tool_name,
                    arguments=arguments,
                    task_id=task_id,
                ),
                description=(
                    "Publish the finished Reel to both Instagram and Facebook."
                    if self._combined_social_publish_enabled
                    else "Publish the finished Reel to Instagram."
                ),
            )
            self._pending = pending
            return pending

        return None

    def _validated_publish_arguments(
        self,
        arguments: Any,
        tool_name: str = "content.publish_to_instagram",
    ) -> dict[str, Any] | None:
        staging = self._staging_directory
        if staging is None or not isinstance(arguments, dict):
            return None
        if tool_name == "content.publish_to_instagram":
            expected_fields = {"video_path", "caption", "mission"}
        elif tool_name == "content.publish_to_socials":
            expected_fields = {
                "video_path", "title", "caption", "mission",
            }
        elif tool_name == "content.publish_to_facebook":
            expected_fields = {
                "video_path", "title", "description", "mission",
            }
        else:
            expected_fields = {
                "video_path",
                "title",
                "description",
                "privacy_status",
                "mission",
            }
        if set(arguments) != expected_fields:
            return None

        video_path = arguments.get("video_path")
        mission = arguments.get("mission")
        if (
            not isinstance(video_path, str)
            or (mission is not None and not isinstance(mission, str))
        ):
            return None

        if tool_name == "content.publish_to_instagram":
            caption = arguments.get("caption")
            if not isinstance(caption, str) or not caption.strip():
                return None
        elif tool_name == "content.publish_to_socials":
            title = arguments.get("title")
            caption = arguments.get("caption")
            if (
                not isinstance(title, str)
                or not title.strip()
                or len(title) > 255
                or not isinstance(caption, str)
                or not caption.strip()
            ):
                return None
        elif tool_name == "content.publish_to_facebook":
            title = arguments.get("title")
            description = arguments.get("description")
            if (
                not isinstance(title, str)
                or not title.strip()
                or len(title) > 255
                or not isinstance(description, str)
                or not description.strip()
            ):
                return None
        else:
            title = arguments.get("title")
            description = arguments.get("description")
            privacy_status = arguments.get("privacy_status")
            if (
                not isinstance(title, str)
                or not title.strip()
                or len(title) > 100
                or not isinstance(description, str)
                or not description.strip()
                or privacy_status not in {"private", "unlisted", "public"}
            ):
                return None

        try:
            resolved_video = Path(video_path).expanduser().resolve(
                strict=True
            )
            resolved_video.relative_to(staging)
        except (OSError, ValueError):
            return None

        if resolved_video.suffix.casefold() != ".mp4":
            return None

        validated = {
            "video_path": str(resolved_video),
            "mission": mission,
        }
        if tool_name == "content.publish_to_instagram":
            validated["caption"] = caption
        elif tool_name == "content.publish_to_socials":
            validated.update({"title": title, "caption": caption})
        elif tool_name == "content.publish_to_facebook":
            validated.update({
                "title": title,
                "description": description,
            })
        else:
            validated.update({
                "title": title,
                "description": description,
                "privacy_status": privacy_status,
            })
        return validated

    def _publish_waiting_confirmation(
        self,
        task: Any,
        workflow: WorkflowResult,
        pending: _PendingConfirmation,
    ) -> None:
        self._publish_event(
            "agent.workflow.waiting_confirmation",
            {
                "task_id": task.task_id,
                "plan_id": getattr(workflow, "plan_id", None),
                "status": WorkflowStatus.WAITING_CONFIRMATION.value,
                "completed_steps": len(workflow.steps),
                "failed_step": None,
                "confirmation_call_id": pending.call.call_id,
                "error": "Owner confirmation required before publishing.",
            },
        )

    def _publish_confirmation_terminal(
        self,
        pending: _PendingConfirmation,
        *,
        status: WorkflowStatus,
        error: str | None,
    ) -> None:
        self._publish_event(
            f"agent.workflow.{status.value}",
            {
                "task_id": pending.task_id,
                "plan_id": "confirmation",
                "status": status.value,
                "completed_steps": 1,
                "failed_step": (
                    None if status is WorkflowStatus.COMPLETED else 1
                ),
                "confirmation_call_id": None,
                "error": error,
            },
        )

    def _publish_event(
        self,
        name: str,
        data: dict[str, Any],
    ) -> None:
        event_bus = getattr(self.bundle, "event_bus", None)

        if event_bus is None:
            return

        try:
            event_bus.publish(AtlasEvent(
                name=name,
                source="voice_controller",
                data=data,
            ))
        except Exception as error:
            # The Reel and pending action remain valid even if the HUD
            # bridge is temporarily unavailable; never discard them over
            # a best-effort presentation update.
            print(
                "Could not publish Reel confirmation HUD state: "
                f"{type(error).__name__}: {error}",
                flush=True,
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

        # A recording workflow can legitimately finish with a diagnostic or
        # packaging step. Prefer the successful Reel result wherever it sits
        # in the completed workflow so Atlas never tells the owner that a
        # valid video on disk failed merely because it was not the last step.
        final_step = next(
            (
                step
                for step in reversed(workflow.steps)
                if step.call.tool_name == "content.record_self_showcase"
                and step.result is not None
                and isinstance(step.result.output, dict)
                and step.result.output.get("ok") is True
            ),
            workflow.steps[-1],
        )
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
            tool_name == "pi.get_upgrade_status"
            and isinstance(output, dict)
        ):
            scope = output.get("scope")

            if scope == "summary":
                finished = output.get("finished_count")
                remaining = output.get("remaining_count")
                blocked = output.get("blocked_count")
                total = output.get("total_count")
                last = output.get("last_updated_feature")

                if not all(
                    isinstance(value, int)
                    for value in (finished, remaining, blocked, total)
                ):
                    return (
                        "I checked the upgrade ledger, but its "
                        "summary was incomplete."
                    )

                message = (
                    f"{finished} of {total} upgrade items are "
                    f"finished, {remaining} remain, and {blocked} "
                    "are blocked on something external."
                )

                if isinstance(last, str) and last:
                    message += f" The last thing I finished was: {last}."

                return message

            items = output.get("items")
            count = output.get("count")

            if not isinstance(items, list):
                return (
                    "I checked the upgrade ledger, but its result "
                    "was incomplete."
                )

            if not items:
                return f"No upgrade items are currently {scope}."

            total = count if isinstance(count, int) else len(items)
            maximum_spoken_items = 5
            titles = [
                item.get("title")
                for item in items[:maximum_spoken_items]
                if isinstance(item, dict) and isinstance(item.get("title"), str)
            ]
            remaining_count = total - len(titles)
            extra = (
                f", plus {remaining_count} more"
                if remaining_count > 0
                else ""
            )

            return (
                f"{total} upgrade items are {scope}: "
                f"{'; '.join(titles)}{extra}."
            )

        if (
            tool_name == "pi.get_mission_history"
            and isinstance(output, dict)
        ):
            scope = output.get("scope")
            missions = output.get("missions")

            if not isinstance(missions, list):
                return (
                    "I checked my mission history, but the "
                    "result was incomplete."
                )

            if not missions:
                if scope == "failed":
                    return (
                        "No recorded missions have failed."
                    )

                return "I have no recorded missions yet."

            descriptions = []
            maximum_spoken_missions = 3

            for mission in missions[
                :maximum_spoken_missions
            ]:
                if not isinstance(mission, dict):
                    continue

                goal_text = mission.get("goal")
                status = mission.get("status")

                if not (
                    isinstance(goal_text, str)
                    and isinstance(status, str)
                ):
                    continue

                status_phrase = (
                    _MISSION_STATUS_PHRASES.get(
                        status,
                        f"is {status}",
                    )
                )
                descriptions.append(
                    f"{goal_text}, which {status_phrase}"
                )

            if not descriptions:
                return (
                    "I checked my mission history, but the "
                    "result was incomplete."
                )

            if scope == "last":
                return (
                    "My last recorded mission was: "
                    f"{descriptions[0]}."
                )

            total = output.get("count")
            total = (
                total
                if isinstance(total, int)
                else len(descriptions)
            )
            remaining_count = total - len(descriptions)
            extra = (
                f", plus {remaining_count} more"
                if remaining_count > 0
                else ""
            )
            label = (
                "failed missions"
                if scope == "failed"
                else "recent missions"
            )

            return (
                f"I have {total} recorded {label}: "
                f"{'; '.join(descriptions)}{extra}."
            )

        if (
            tool_name == "pi.explain_last_failure"
            and isinstance(output, dict)
        ):
            if output.get("evidence_found") is not True:
                return (
                    "I checked my mission history and "
                    "logs, and I found no recorded "
                    "failure to explain."
                )

            parts = []
            failed_mission = output.get("failed_mission")

            if isinstance(
                failed_mission, dict
            ) and isinstance(
                failed_mission.get("goal"), str
            ):
                sentence = (
                    "My last failed mission was: "
                    f"{failed_mission['goal']}."
                )
                note = failed_mission.get("note")

                if isinstance(note, str) and note:
                    sentence += (
                        f" The recorded reason is: {note}"
                    )

                parts.append(sentence)

            interaction = output.get(
                "last_error_interaction"
            )

            if isinstance(interaction, dict):
                errors = interaction.get("errors")

                if (
                    isinstance(errors, list)
                    and errors
                    and isinstance(errors[0], str)
                ):
                    parts.append(
                        "The last logged error was: "
                        f"{errors[0]}."
                    )

            incidents = output.get("recent_incidents")

            if isinstance(incidents, list) and incidents:
                unresolved = [
                    incident
                    for incident in incidents
                    if isinstance(incident, dict)
                    and incident.get("resolved") is False
                ]

                if unresolved:
                    latest = unresolved[0]
                    component = latest.get("component")
                    verification = latest.get(
                        "verification"
                    )

                    if isinstance(
                        component, str
                    ) and isinstance(verification, str):
                        parts.append(
                            "My latest unresolved "
                            f"incident is {component}: "
                            f"{verification}."
                        )

            suggestions = output.get(
                "suggested_retries"
            )

            if (
                isinstance(suggestions, list)
                and suggestions
                and isinstance(suggestions[0], dict)
            ):
                first = suggestions[0]
                action = first.get("action")
                component = first.get("component")
                goal_text = first.get("goal")

                if (
                    action == "recover_component"
                    and isinstance(component, str)
                ):
                    spoken = component.replace("_", " ")
                    parts.append(
                        "You can ask me to recover "
                        f"the {spoken}."
                    )
                elif (
                    action == "retry_mission"
                    and isinstance(goal_text, str)
                ):
                    parts.append(
                        "You can ask me to retry "
                        "that mission."
                    )

            if not parts:
                return (
                    "I found recorded incident evidence, "
                    "but no failed mission or logged "
                    "error to explain."
                )

            return " ".join(parts)

        if (
            tool_name == "pi.run_diagnostics"
            and isinstance(output, dict)
        ):
            findings = output.get("findings")
            count = output.get("count")

            if not isinstance(findings, list):
                return (
                    "I ran diagnostics, but the result "
                    "was incomplete."
                )

            total = (
                count
                if isinstance(count, int)
                else len(findings)
            )
            problems = [
                finding
                for finding in findings
                if isinstance(finding, dict)
                and finding.get("ok") is False
            ]

            if not problems:
                return (
                    f"I ran {total} diagnostic checks. "
                    "All of them pass."
                )

            maximum_spoken_problems = 3
            described = []

            for finding in problems[
                :maximum_spoken_problems
            ]:
                component = finding.get("component")
                detail = finding.get("detail")

                if not isinstance(component, str):
                    continue

                spoken_component = component.replace(
                    "_", " "
                )

                if isinstance(detail, str) and detail:
                    described.append(
                        f"{spoken_component}: {detail}"
                    )
                else:
                    described.append(spoken_component)

            remaining_count = len(problems) - len(
                described
            )
            extra = (
                f", plus {remaining_count} more"
                if remaining_count > 0
                else ""
            )

            return (
                f"I ran {total} diagnostic checks. "
                f"{len(problems)} reported problems: "
                f"{'; '.join(described)}{extra}."
            )

        if (
            tool_name == "pi.recover_component"
            and isinstance(output, dict)
        ):
            component = output.get("component")
            action = output.get("action")
            verification = output.get("verification")
            resolved = output.get("resolved")

            if not (
                isinstance(component, str)
                and isinstance(action, str)
                and isinstance(verification, str)
            ):
                return (
                    "I ran the recovery playbook, but "
                    "its report was incomplete."
                )

            spoken_component = component.replace(
                "_", " "
            )

            if action.startswith("none"):
                return (
                    f"The {spoken_component} was "
                    f"already healthy: {verification}."
                )

            if action.startswith("skipped"):
                return (
                    f"I skipped repairing the "
                    f"{spoken_component} to avoid a "
                    f"restart loop: {verification}."
                )

            if resolved is True:
                return (
                    f"I recovered the "
                    f"{spoken_component}. I "
                    f"{action}, and verified: "
                    f"{verification}."
                )

            return (
                f"I couldn't fully recover the "
                f"{spoken_component}. I {action}, "
                f"but: {verification}."
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

        if (
            tool_name == "pc.focus_or_open_app"
            and isinstance(output, dict)
        ):
            data = output.get("data")
            app = (
                data.get("app")
                if isinstance(data, dict)
                else None
            )
            action = (
                data.get("action")
                if isinstance(data, dict)
                else None
            )
            spoken_app = (
                app
                if isinstance(app, str) and app
                else "that app"
            )

            if action == "focused":
                return (
                    f"{spoken_app} was already open — "
                    "I brought it to the front."
                )

            if action == "launched":
                return f"Done. I opened {spoken_app}."

        if (
            tool_name == "pc.active_window"
            and isinstance(output, dict)
        ):
            data = output.get("data")
            title = (
                data.get("title")
                if isinstance(data, dict)
                else None
            )

            if isinstance(title, str) and title:
                return (
                    f"You're focused on {title} on "
                    "your PC."
                )

            return (
                "I couldn't tell what's focused on "
                "your PC right now."
            )

        if (
            tool_name == "content.record_self_showcase"
            and isinstance(output, dict)
        ):
            video_path = output.get("video_path")

            if not output.get("ok") or not video_path:
                return (
                    "I tried to record a self-showcase Reel, "
                    "but it didn't produce a usable file: "
                    f"{output.get('error') or 'no reason given'}."
                )

            filename = Path(video_path).name
            caption = output.get("caption")
            caption_line = (
                caption.strip().splitlines()[0]
                if isinstance(caption, str) and caption.strip()
                else "no caption"
            )

            preview_line = (
                "I played it back for you with audio. "
                if output.get("previewed") is not False
                else "I couldn't play the local preview, but the file is safe. "
            )
            package_line = (
                "I also prepared its cover, subtitles, two Trial variants, "
                "translation files, collaboration draft, and platform exports. "
                if output.get("growth_package")
                else ""
            )
            desktop_export = output.get("desktop_export")
            if (
                isinstance(desktop_export, dict)
                and desktop_export.get("ok")
            ):
                desktop_line = (
                    "I also saved the Reel package to your Windows Desktop "
                    f"in {desktop_export.get('folder')}. "
                )
            elif isinstance(desktop_export, dict):
                desktop_line = (
                    "The Reel is safe on Atlas, but the Desktop copy failed: "
                    f"{desktop_export.get('error') or 'unknown error'}. "
                )
            else:
                desktop_line = ""
            publish_question = (
                "Do you want me to post this exact Reel to both Instagram "
                "and Facebook and copy it to your Desktop, save it to your "
                "Desktop without posting, or delete it?"
                if self._combined_social_publish_enabled
                else (
                    "Do you want me to post it to Instagram and copy it to "
                    "your Desktop, save it to your Desktop without posting, "
                    "or delete it?"
                )
            )
            return (
                "Done. I recorded and edited a self-showcase "
                f"Reel, saved as {filename}. {preview_line}"
                f"{package_line}"
                f"{desktop_line}"
                f"Draft caption: {caption_line}. {publish_question}"
            )

        if (
            tool_name == "content.publish_to_instagram"
            and isinstance(output, dict)
        ):
            if output.get("ok") and output.get("permalink"):
                return (
                    "Done. Published the Reel to Instagram: "
                    f"{output['permalink']}"
                )

            return (
                "I couldn't publish the Reel to Instagram: "
                f"{output.get('error') or 'no reason given'}."
            )

        if (
            tool_name == "content.publish_to_youtube"
            and isinstance(output, dict)
        ):
            if output.get("ok") and output.get("permalink"):
                privacy = output.get("privacy_status") or "unknown"
                return (
                    "Done. Uploaded the Short to YouTube as "
                    f"{privacy}: {output['permalink']}"
                )
            return (
                "I couldn't upload the Short to YouTube: "
                f"{output.get('error') or 'no reason given'}."
            )

        if (
            tool_name == "content.publish_to_facebook"
            and isinstance(output, dict)
        ):
            if output.get("ok") and output.get("permalink"):
                return (
                    "Done. Published the Reel to the ATLAS AI Robot "
                    f"Facebook Page: {output['permalink']}"
                )
            return (
                "I couldn't publish the Reel to Facebook: "
                f"{output.get('error') or 'no reason given'}."
            )

        if (
            tool_name == "content.publish_to_socials"
            and isinstance(output, dict)
        ):
            instagram = output.get("instagram") or {}
            facebook = output.get("facebook") or {}
            if output.get("ok"):
                return (
                    "Done. I published and verified the Reel on both "
                    f"Instagram ({instagram.get('permalink')}) and Facebook "
                    f"({facebook.get('permalink')})."
                )
            return (
                "The coordinated publish was not fully verified. "
                f"Instagram: {instagram.get('permalink') or instagram.get('error') or 'unknown'}. "
                f"Facebook: {facebook.get('permalink') or facebook.get('error') or 'unknown'}."
            )

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
        pending_tool = None

        if call_id:
            for step in workflow.steps:
                if step.call.call_id == call_id:
                    description = step.description
                    pending_tool = step.call.tool_name
                    break

        if pending_tool == "content.publish_to_instagram":
            return (
                "I played the finished Reel back for you with audio. "
                "Do you want me to post it to Instagram and copy it to your "
                "Desktop, save it to your Desktop without posting, or delete it?"
            )

        if pending_tool == "content.publish_to_youtube":
            return (
                "I played the finished Short back for you with audio. "
                "Do you want me to upload this exact video privately to "
                "YouTube and copy it to your Desktop, save it to your Desktop "
                "without posting, or delete it?"
            )

        if pending_tool == "content.publish_to_facebook":
            return (
                "I played the finished Reel back for you with audio. "
                "Do you want me to publish this exact video to the ATLAS AI "
                "Robot Facebook Page and copy it to your Desktop, save it to "
                "your Desktop without posting, or delete it?"
            )

        if pending_tool == "content.publish_to_socials":
            return (
                "I played the finished Reel back for you with audio. Do you "
                "want me to publish this exact Reel to both Instagram and the "
                "ATLAS AI Robot Facebook Page and copy it to your Desktop, "
                "save it to your Desktop without posting, or delete it?"
            )

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
        # Tools like content.publish_to_instagram catch their own
        # errors and return them inside output["error"] rather than
        # raising -- that's the specific, actionable reason (e.g.
        # "video file not found: ..."). Checked first, ahead of
        # workflow.error, because WorkflowRunner sets workflow.error to
        # the same generic verification.reason text below on every
        # verification failure -- if checked in the old order, that
        # generic text always won and the tool's own specific reason
        # was never reachable.
        specific_candidates: list[Any] = []
        generic_candidates: list[Any] = [workflow.error]

        for step in reversed(workflow.steps):
            if step.result is not None and isinstance(
                step.result.output, dict
            ):
                output_error = step.result.output.get("error")

                if (
                    isinstance(output_error, str)
                    and output_error.strip()
                ):
                    specific_candidates.append(output_error)

            generic_candidates.append(step.error)

            if step.result is not None:
                generic_candidates.append(
                    step.result.error
                )

            if step.verification is not None:
                generic_candidates.append(
                    step.verification.reason
                )

        candidates = specific_candidates + generic_candidates

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

"""Vision-driven observe/act loop for Atlas's general Windows control."""

from __future__ import annotations

import json
import time
from typing import Any

from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool
from atlas_agent.verifier import ResultVerifier, VerificationCheck


SUBMIT_DESKTOP_STEP = "submit_desktop_step"
ACTION_ENDPOINTS = {
    "input": "desktop_input",
    "window": "window_control",
    "clipboard": "clipboard",
    "file": "file_operation",
    "launch": "launch_process",
    "process": "process_control",
}

# The companion endpoints accept small, explicit JSON contracts. The desktop
# model previously received only endpoint names and an untyped arguments_json
# string, so it guessed fields such as ``path``, ``command``, and ``mode``.
# Those guesses were rejected correctly, but each rejection consumed one of a
# recording's scarce visible actions.
ACTION_ARGUMENT_GUIDE = """Exact arguments_json contracts:
- input mouse: {"action":"click|double_click|move|scroll","x":123,"y":456,"button":"left","delta":-120}. Use delta only for scroll.
- input drag: {"action":"drag","path":[[120,300],[160,340],[210,360]],"button":"left"}. Holds the button down and moves through every point, then releases. This is the ONLY action that draws: a click leaves no visible mark on a canvas, so in Paint or any drawing app every stroke must be a drag with several points along it. Give at least three points per stroke and one drag per turn.
- input keys: {"action":"keys","keys":"%{F4}"}. WScript SendKeys uses ^ for Ctrl, % for Alt, + for Shift, and braces for named keys such as {ENTER}.
- input text: {"action":"text","text":"literal text to type"}.
- window: {"action":"list"}, or {"action":"focus|minimize|maximize|restore|close","title":"visible title substring"}.
- clipboard: {"action":"read"}, or {"action":"write","text":"literal text"}.
- file: {"operation":"stat|list|read|write|append|mkdir|copy|move|delete","path":"absolute user path"}. copy/move also requires "destination"; write/append uses "text" or "data_b64".
- launch: {"executable":"mspaint.exe","arguments":[],"working_directory":null,"hidden":false}. Never use path, command, args, or a shell command string.
- process: {"action":"list"}, or {"action":"stop","pid":1234}.
Use exactly one endpoint action per turn. If the previous result has ok=false,
correct its arguments on the next turn. Never relaunch an application that is
already visible; use window list/focus or interact with the visible window.
"""


def _attribute(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


class DesktopAutonomyError(RuntimeError):
    pass


def _normalize_action_arguments(
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Repair harmless argument aliases observed in live model output.

    The exact contracts in the prompt are the primary fix. This is a local
    backstop so a familiar synonym cannot waste an on-camera action or make
    the recording layer repeat a partially completed task. It grants no new
    endpoint or permission.
    """
    normalized = dict(arguments)

    if action == "input" and not normalized.get("action"):
        if normalized.get("keys"):
            normalized["action"] = "keys"
        elif normalized.get("text"):
            normalized["action"] = "text"
        elif normalized.get("path"):
            normalized["action"] = "drag"

    elif action == "window" and not normalized.get("action"):
        for alias in ("mode", "operation", "state"):
            value = normalized.pop(alias, None)
            if value:
                normalized["action"] = value
                break

    elif action == "launch":
        if not normalized.get("executable"):
            normalized["executable"] = (
                normalized.pop("path", None)
                or normalized.pop("command", None)
            )
        if "arguments" not in normalized:
            normalized["arguments"] = normalized.pop("args", [])
        if not normalized.get("arguments"):
            normalized["arguments"] = []

    return normalized


class DesktopAutonomyAgent:
    def __init__(self, client: Any, model: str, pc_client: Any) -> None:
        self.client = client
        self.model = model
        self.pc_client = pc_client

    def run(self, goal: str, *, max_steps: int = 20) -> dict[str, Any]:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        if not isinstance(max_steps, int) or not 1 <= max_steps <= 60:
            raise ValueError("max_steps must be between 1 and 60")

        started = time.monotonic()
        trace: list[dict[str, Any]] = []
        previous = "No actions have been taken yet."
        input_tokens = 0
        output_tokens = 0

        # One extra observe/decide turn lets the model verify the result of
        # its final permitted action. Previously a goal that needed exactly
        # max_steps actions could never report success because there was no
        # subsequent screenshot on which to say it was complete.
        for position in range(1, max_steps + 2):
            observation = self.pc_client.execute(
                "observe_desktop", timeout_seconds=45
            )
            data = observation.data or {}
            if not observation.ok or not data.get("ok"):
                raise DesktopAutonomyError(
                    observation.error or data.get("error")
                    or "desktop observation failed"
                )

            response = self.client.responses.create(
                model=self.model,
                reasoning={"effort": "none"},
                instructions=(
                    "You are Atlas directly operating the owner's current "
                    "Windows desktop. Work autonomously until the goal is "
                    "actually complete. Inspect every screenshot carefully; "
                    "use one small verifiable action per turn. You may control "
                    "the mouse, keyboard, windows, clipboard, user processes, "
                    "and every non-system personal file. Never request or "
                    "accept administrator elevation, never modify protected "
                    "Windows/program/control files, and immediately report "
                    "complete when the goal is satisfied. Coordinates refer "
                    "to the screenshot's native desktop dimensions. For keys, "
                    "use WScript SendKeys notation such as ^c, ^v, %{TAB}, or "
                    "{ENTER}. arguments_json must be a JSON object accepted by "
                    "the chosen action. Do not merely describe an action.\n\n"
                    + ACTION_ARGUMENT_GUIDE
                ),
                input=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"GOAL: {goal.strip()}\n"
                                f"ACTIONS USED: {len(trace)}/{max_steps}\n"
                                + (
                                    "ACTION BUDGET EXHAUSTED: inspect this final "
                                    "screenshot and report complete if the goal "
                                    "is satisfied; no further action is allowed.\n"
                                    if position > max_steps else ""
                                )
                                + f"ACTIVE WINDOW: {data.get('active_window')}\n"
                                f"OPEN WINDOWS: {data.get('windows')}\n"
                                f"CURSOR: {data.get('cursor')}\n"
                                f"PREVIOUS RESULT: {previous}"
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": (
                                "data:image/png;base64," + data["image_b64"]
                            ),
                            "detail": "low",
                        },
                    ],
                }],
                tools=[{
                    "type": "function",
                    "name": SUBMIT_DESKTOP_STEP,
                    "description": "Submit the next desktop action or completion.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["act", "complete"],
                            },
                            "action": {
                                "type": ["string", "null"],
                                "enum": [*ACTION_ENDPOINTS, None],
                            },
                            "arguments_json": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                        "required": [
                            "status", "action", "arguments_json", "summary"
                        ],
                        "additionalProperties": False,
                    },
                }],
                tool_choice={"type": "function", "name": SUBMIT_DESKTOP_STEP},
                max_output_tokens=1000,
            )
            usage = _attribute(response, "usage")
            input_tokens += int(_attribute(usage, "input_tokens", 0) or 0)
            output_tokens += int(_attribute(usage, "output_tokens", 0) or 0)
            call = next(
                (
                    item for item in _attribute(response, "output", [])
                    if _attribute(item, "type") == "function_call"
                    and _attribute(item, "name") == SUBMIT_DESKTOP_STEP
                ),
                None,
            )
            if call is None:
                raise DesktopAutonomyError("desktop model returned no action")
            try:
                decision = json.loads(_attribute(call, "arguments", "{}"))
            except json.JSONDecodeError as error:
                raise DesktopAutonomyError("desktop model returned malformed JSON") from error

            summary = str(decision.get("summary") or "").strip()
            if decision.get("status") == "complete":
                return {
                    "ok": True,
                    "completed": True,
                    "summary": summary or "The desktop goal is complete.",
                    "steps": trace,
                    "step_count": len(trace),
                    "duration_seconds": round(time.monotonic() - started, 2),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }

            if position > max_steps:
                break

            action = decision.get("action")
            if action not in ACTION_ENDPOINTS:
                raise DesktopAutonomyError(f"unsupported desktop action: {action}")
            try:
                arguments = json.loads(decision.get("arguments_json") or "{}")
            except json.JSONDecodeError as error:
                raise DesktopAutonomyError("desktop action arguments were malformed") from error
            if not isinstance(arguments, dict):
                raise DesktopAutonomyError("desktop action arguments were not an object")
            arguments = _normalize_action_arguments(action, arguments)

            result = self.pc_client.execute(
                ACTION_ENDPOINTS[action], arguments, timeout_seconds=150
            )
            result_data = result.data or {}
            trace_entry = {
                "position": position,
                "action": action,
                "arguments": arguments,
                "ok": bool(result.ok and result_data.get("ok")),
                "result": result_data if result.ok else result.error,
            }
            trace.append(trace_entry)
            previous = json.dumps(trace_entry, default=str)[:3000]

        return {
            "ok": False,
            "completed": False,
            "error": (
                "desktop goal did not verify completion within "
                f"{max_steps} actions"
            ),
            "steps": trace,
            "step_count": len(trace),
            "duration_seconds": round(time.monotonic() - started, 2),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }


def register_desktop_autonomy_tool(
    registry: ToolRegistry,
    verifier: ResultVerifier,
    *,
    client: Any,
    model: str,
    pc_client: Any,
    agent: DesktopAutonomyAgent | None = None,
) -> AtlasTool:
    agent = agent or DesktopAutonomyAgent(client, model, pc_client)

    tool = AtlasTool(
        name="pc.autonomous_desktop",
        description=(
            "Give Atlas full vision-driven control of the owner's current "
            "interactive Windows desktop for a concrete goal. Atlas repeatedly "
            "observes the real screen, chooses mouse/keyboard/window/process/"
            "clipboard/non-system-file actions, executes them, and verifies the "
            "result until complete. Use this instead of a canned app action "
            "whenever the goal requires general PC interaction."
        ),
        runs_on="pc",
        handler=agent.run,
        permission_level=1,
        timeout_seconds=900,
        metadata={
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "minLength": 1},
                    "max_steps": {
                        "type": "integer", "minimum": 1, "maximum": 60
                    },
                },
                "required": ["goal", "max_steps"],
                "additionalProperties": False,
            }
        },
    )
    registry.register(tool)
    verifier.register(
        tool.name,
        lambda call, result: VerificationCheck(
            verified=(
                isinstance(result.output, dict)
                and result.output.get("ok") is True
                and result.output.get("completed") is True
            ),
            reason=(
                "Atlas completed and visually verified the desktop goal."
                if isinstance(result.output, dict)
                and result.output.get("ok") is True
                and result.output.get("completed") is True
                else "Atlas did not complete the desktop goal."
            ),
            evidence=result.output if isinstance(result.output, dict) else {},
        ),
    )
    return tool

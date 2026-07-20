from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from atlas_agent.tasks import utc_now


class ResultStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    TIMEOUT = "timeout"


@dataclass(slots=True)
class ToolResult:
    tool_name: str
    call_id: str
    status: ResultStatus
    task_id: str | None = None
    output: Any = None
    error: str | None = None
    started_at: str = field(default_factory=utc_now)
    finished_at: str = field(default_factory=utc_now)
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_name.strip():
            raise ValueError("tool_name cannot be empty")

        if not self.call_id.strip():
            raise ValueError("call_id cannot be empty")

        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")

    @property
    def success(self) -> bool:
        return self.status is ResultStatus.SUCCESS

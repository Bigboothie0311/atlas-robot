from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


ToolHandler = Callable[..., Any]


@dataclass(slots=True)
class AtlasTool:
    name: str
    description: str
    runs_on: str
    handler: ToolHandler
    permission_level: int = 0
    timeout_seconds: int = 30
    metadata: dict[str, Any] = field(default_factory=dict)

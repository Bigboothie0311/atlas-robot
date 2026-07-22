from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import logbook

from atlas_agent.content_tools import register_content_tools
from atlas_agent.event_bus import EventBus
from atlas_agent.executor import ToolExecutor
from atlas_agent.local_tools import register_local_tools
from atlas_agent.mission_store import MissionStore
from atlas_agent.openai_planner import OpenAIPlanGenerator
from atlas_agent.pc_client import PCClient
from atlas_agent.pc_tools import register_pc_tools
from atlas_agent.permissions import PermissionPolicy
from atlas_agent.pi_tools import register_pi_capture_tools
from atlas_agent.planner import AgentPlanner
from atlas_agent.planning_service import (
    NaturalLanguagePlanningService,
)
from atlas_agent.router import ToolRouter
from atlas_agent.runtime import AgentRuntime
from atlas_agent.sftp_client import SFTPClient
from atlas_agent.showcase_script import generate_showcase_tour
from atlas_agent.task_queue import TaskQueue
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.verifier import ResultVerifier
from atlas_agent.windows_file_search import (
    WindowsFileSearch,
)
from atlas_agent.workflow import WorkflowRunner


@dataclass(slots=True)
class RuntimeBundle:
    runtime: AgentRuntime
    executor: ToolExecutor
    registry: ToolRegistry
    verifier: ResultVerifier
    event_bus: EventBus
    task_queue: TaskQueue
    mission_store: MissionStore

    def close(self) -> None:
        self.executor.close()


def build_pc_agent_runtime(
    *,
    openai_client: Any,
    model: str,
    host: str,
    username: str,
    identity_file: str | Path,
    approved_remote_roots: Iterable[str],
    staging_directory: str | Path,
    mission_store_path: str | Path,
    recordings_remote_root: str | None = None,
    ssh_port: int = 22,
    planning_attempts: int = 2,
) -> RuntimeBundle:
    normalized_host = host.strip()
    normalized_username = username.strip()
    roots = tuple(approved_remote_roots)

    if not normalized_host:
        raise ValueError("host must not be empty")

    if not normalized_username:
        raise ValueError(
            "username must not be empty"
        )

    if not roots:
        raise ValueError(
            "approved_remote_roots must not be empty"
        )

    registry = ToolRegistry()
    verifier = ResultVerifier()
    event_bus = EventBus()
    task_queue = TaskQueue()
    mission_store = MissionStore(
        mission_store_path
    )

    recovered_tasks = mission_store.load(
        recover_interrupted=True,
    )

    for task in recovered_tasks:
        task_queue.restore(task)

    if recovered_tasks:
        mission_store.save(
            task_queue.list_tasks()
        )

    sftp_client = SFTPClient(
        host=normalized_host,
        username=normalized_username,
        identity_file=identity_file,
        staging_directory=staging_directory,
        approved_remote_roots=roots,
        port=ssh_port,
    )

    pc_client = PCClient()

    register_pc_tools(
        registry,
        verifier,
        pc_client=pc_client,
        file_search=WindowsFileSearch(
            host=normalized_host,
            username=normalized_username,
            identity_file=identity_file,
            approved_remote_roots=roots,
            port=ssh_port,
        ),
        sftp_client=sftp_client,
    )
    register_local_tools(
        registry,
        verifier,
        approved_roots=(
            Path("/home/atlas/atlas-robot"),
        ),
        mission_store_path=mission_store_path,
    )
    def write_showcase_script(**kwargs: Any):
        return generate_showcase_tour(
            openai_client,
            model,
            **kwargs,
        )

    register_content_tools(
        registry,
        verifier,
        staging_directory=staging_directory,
        pc_client=pc_client,
        sftp_client=sftp_client,
        script_writer=write_showcase_script,
    )

    if recordings_remote_root is not None:
        register_pi_capture_tools(
            registry,
            verifier,
            sftp_client=sftp_client,
            recordings_remote_root=recordings_remote_root,
            staging_directory=staging_directory,
        )

    planning_service = (
        NaturalLanguagePlanningService(
            generator=OpenAIPlanGenerator(
                client=openai_client,
                model=model,
            ),
            planner=AgentPlanner(
                registry,
                ToolRouter(registry),
            ),
            registry=registry,
            max_attempts=planning_attempts,
        )
    )
    executor = ToolExecutor(
        registry,
        PermissionPolicy(),
        audit_sink=logbook.record_tool_audit,
    )
    workflow_runner = WorkflowRunner(
        executor,
        verifier,
        event_bus=event_bus,
    )
    runtime = AgentRuntime(
        planning_service=planning_service,
        workflow_runner=workflow_runner,
        task_queue=task_queue,
        mission_store=mission_store,
        event_bus=event_bus,
    )

    return RuntimeBundle(
        runtime=runtime,
        executor=executor,
        registry=registry,
        verifier=verifier,
        event_bus=event_bus,
        task_queue=task_queue,
        mission_store=mission_store,
    )

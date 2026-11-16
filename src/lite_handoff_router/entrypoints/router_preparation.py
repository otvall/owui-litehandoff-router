"""Mandatory request preparation for the public Router Workspace Model."""

from pydantic import BaseModel, Field

from ..owui_adapter import OWUIAdapter
from ..registry import discover, discover_folder, ids
from ..runtime import begin
from ..parallel import prepare_worker

AGENTS: dict[str, str] = {}


class Filter:
    class Valves(BaseModel):
        priority: int = 0
        routing_skill_id: str = Field(default="", description="Multi-file Skill containing routing.json and routing profiles")
        base_tool_ids: list[str] = Field(default_factory=list)
        base_skill_ids: list[str] = Field(default_factory=list)
        max_parallel_tasks: int = Field(default=4, ge=0, strict=True)
        parallel_timeout_seconds: int = Field(default=300, gt=0, strict=True)


    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __request__, __user__: dict, __metadata__: dict = None):
        if __request__.scope.get("lite_handoff_preparing"):
            return body
        adapter = OWUIAdapter()
        user = await adapter.user(__user__["id"])
        metadata = __metadata__ if __metadata__ is not None else body.setdefault("metadata", {})
        if await prepare_worker(adapter, __request__, body, metadata, user):
            return body
        if self.valves.routing_skill_id.strip():
            registry, diagnostics = await discover_folder(
                adapter, __request__, user, self.valves.routing_skill_id.strip(), body["model"])
        else:
            registry, diagnostics = await discover(adapter, __request__, user, AGENTS, body["model"])
        begin(__request__, body, metadata, user.id, registry, diagnostics,
              ids(self.valves.base_tool_ids), ids(self.valves.base_skill_ids),
              max_parallel_tasks=self.valves.max_parallel_tasks,
              parallel_timeout_seconds=self.valves.parallel_timeout_seconds)
        return body

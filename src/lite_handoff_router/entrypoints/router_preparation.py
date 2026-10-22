"""Mandatory request preparation for the public Router Workspace Model."""

from pydantic import BaseModel, Field, field_validator

from ..owui_adapter import OWUIAdapter
from ..registry import discover, ids
from ..runtime import begin
from ..parallel import prepare_worker

AGENTS: dict[str, str] = {}


class Filter:
    class Valves(BaseModel):
        priority: int = 0
        base_tool_ids: list[str] = Field(default_factory=list)
        base_skill_ids: list[str] = Field(default_factory=list)
        max_parallel_tasks: int = Field(default=4, ge=0, strict=True)
        parallel_timeout_seconds: int = Field(default=300, gt=0, strict=True)

        @field_validator("max_parallel_tasks")
        @classmethod
        def parallel_minimum(cls, value):
            if value == 1:
                raise ValueError("max_parallel_tasks must be 0 or >= 2")
            return value

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
        registry, diagnostics = await discover(adapter, __request__, user, AGENTS, body["model"])
        begin(__request__, body, metadata, user.id, registry, diagnostics,
              ids(self.valves.base_tool_ids), ids(self.valves.base_skill_ids),
              max_parallel_tasks=self.valves.max_parallel_tasks,
              parallel_timeout_seconds=self.valves.parallel_timeout_seconds)
        return body

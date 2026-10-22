"""Mandatory request preparation for the public Router Workspace Model."""

from pydantic import BaseModel, Field

from ..owui_adapter import OWUIAdapter
from ..registry import discover, ids
from ..runtime import begin

AGENTS: dict[str, str] = {}


class Filter:
    class Valves(BaseModel):
        priority: int = 0
        base_tool_ids: list[str] = Field(default_factory=list)
        base_skill_ids: list[str] = Field(default_factory=list)

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __request__, __user__: dict, __metadata__: dict = None):
        if __request__.scope.get("lite_handoff_preparing"):
            return body
        adapter = OWUIAdapter()
        user = await adapter.user(__user__["id"])
        metadata = __metadata__ if __metadata__ is not None else body.setdefault("metadata", {})
        registry, diagnostics = await discover(adapter, __request__, user, AGENTS, body["model"])
        begin(__request__, body, metadata, user.id, registry, diagnostics,
              ids(self.valves.base_tool_ids), ids(self.valves.base_skill_ids))
        return body

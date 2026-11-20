"""Mandatory request preparation for the public Router Workspace Model."""

from pydantic import ConfigDict, Field, field_validator

from ..owui_adapter import OWUIAdapter
from ..registry import discover, ids
from ..runtime import begin
from ..parallel import prepare_worker
from ..context_policy import ContextPolicy

AGENTS: dict[str, str] = {}


class Filter:
    class Valves(ContextPolicy):
        # OWUI posts text inputs; use Pydantic's standard numeric conversion
        # here. The runtime policy validates the converted value strictly.
        model_config = ConfigDict(strict=False)

        priority: int = 0
        base_tool_ids: list[str] = Field(default_factory=list)
        base_skill_ids: list[str] = Field(default_factory=list)
        max_parallel_tasks: int = Field(default=4, ge=0,
                                        description="Maximum parallel tasks; 0 disables delegate_task and keeps ordinary handoff.")
        parallel_timeout_seconds: int = Field(default=300, gt=0)

        @field_validator(
            "priority", "max_parallel_tasks", "parallel_timeout_seconds",
            "tool_result_history_limit", "router_summary_trigger_tokens", mode="before")
        @classmethod
        def reject_boolean_number(cls, value):
            if isinstance(value, bool):
                raise ValueError("Boolean is not a numeric setting")
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
        chat_skill_ids = await adapter.chat_skill_ids(body)
        begin(__request__, body, metadata, user.id, registry, diagnostics,
              ids(self.valves.base_tool_ids), ids(self.valves.base_skill_ids),
              max_parallel_tasks=self.valves.max_parallel_tasks,
              parallel_timeout_seconds=self.valves.parallel_timeout_seconds,
              context_policy=self.valves.model_dump(include=set(ContextPolicy.model_fields)),
              chat_skill_ids=chat_skill_ids)
        return body

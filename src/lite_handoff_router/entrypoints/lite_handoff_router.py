"""Public pipe; continuations remain in OWUI's native tool loop."""

from pydantic import BaseModel, Field

from ..model_execution import prepare_model
from ..owui_adapter import OWUIAdapter
from ..protocol import confirmed_target
from ..runtime import require, metadata_draft, publish
from ..history import refresh_source
from ..observation import observe_response, check_profile_calls


class Pipe:
    class Valves(BaseModel):
        orchestrator_model_id: str = Field(default="", description="Real Router LLM ID")

    def __init__(self):
        self.valves = self.Valves()

    async def pipe(self, body: dict, __request__, __user__: dict, __metadata__: dict,
                   __event_emitter__=None, __event_call__=None, __oauth_token__=None):
        adapter = OWUIAdapter()
        user = await adapter.user(__user__["id"])
        state = require(__request__, __metadata__, user.id)
        check_profile_calls(state)
        refresh_source(state, body.get("messages", []))
        target = confirmed_target(state, state["source"])
        sessions_before = set(__request__.scope.get("lite_handoff_mcp", {}))
        try:
            draft, tools = await prepare_model(
                adapter, __request__, body, user, metadata_draft(__metadata__), state,
                self.valves.orchestrator_model_id,
                {"__event_emitter__": __event_emitter__, "__event_call__": __event_call__,
                 "__oauth_token__": __oauth_token__}, target=target,
            )
        except BaseException:
            sessions = __request__.scope.get("lite_handoff_mcp", {})
            for key in list(sessions):
                if key not in sessions_before:
                    await sessions.pop(key).close()
            raise
        publish(__request__, __metadata__, draft["metadata"], tools)
        if target:
            was_active = state["active_agent"]
            state["active_agent"] = target
            state["phase"] = "agent"
            if not was_active and __event_emitter__:
                agent = state["registry"][target]
                await __event_emitter__({"type": "status", "data": {
                    "action": "handoff", "description": "Передача: " + agent.get("agent_name", target), "done": True,
                }})
        response = await adapter.complete(__request__, draft, user)
        executor = {"kind": "agent", "agent_id": target} if target else {"kind": "router"}
        return observe_response(response, state, executor)

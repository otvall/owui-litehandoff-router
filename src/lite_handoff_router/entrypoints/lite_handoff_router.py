"""Public pipe; continuations remain in OWUI's native tool loop."""

from pydantic import BaseModel, Field

from ..model_execution import prepare_model
from ..completion_context import CompletionContext
from ..context_policy import summary_model_id
from ..owui_adapter import OWUIAdapter
from ..protocol import confirmed_target
from ..runtime import require, metadata_draft, publish
from ..history import refresh_source
from ..capabilities import close_mcp_sessions
from ..native_context import capture_native_context


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
        refresh_source(state, body.get("messages", []))
        if state.get("native_context") is None:
            converted = (adapter.native_context_messages(__request__, __metadata__, state)
                         if __request__.scope["lite_handoff_metadata"].get("initial_output") else [])
            if not converted and getattr(getattr(__request__, "state", None), "tool_approval_resume", None):
                converted = await adapter.native_resume_context(__request__, __metadata__, state, user)
                state["source"].extend(converted)
            capture_native_context(__request__, __metadata__, state, converted)
        target = confirmed_target(state, state["source"])
        sessions_before = set(__request__.scope.get("lite_handoff_mcp", {}))
        try:
            if target and summary_model_id(state) and state.get("historical_handoff_statuses") is None:
                state["historical_handoff_statuses"] = await adapter.historical_handoff_statuses(
                    __request__, __metadata__, state, user)
            draft, tools = await prepare_model(
                adapter, __request__, body, user, metadata_draft(__metadata__), state,
                self.valves.orchestrator_model_id,
                {"__event_emitter__": __event_emitter__, "__event_call__": __event_call__,
                 "__oauth_token__": __oauth_token__}, target=target,
            )
            completion = CompletionContext(adapter, __request__, user, state, draft, target, emitter=__event_emitter__)
            await completion.prepare()
        except BaseException:
            sessions = __request__.scope.get("lite_handoff_mcp", {})
            await close_mcp_sessions(sessions.pop(key) for key in list(sessions) if key not in sessions_before)
            raise

        publish(__request__, __metadata__, draft["metadata"], tools)
        if target:
            was_active = state["active_agent"]
            state["active_agent"] = target
            state["phase"] = "agent"
            if not was_active and __event_emitter__:
                await __event_emitter__({"type": "status", "data": {
                    "action": "handoff", "description": "Передача: " + state["registry"][target].get("agent_name", target),
                    "done": True,
                }})
        return await completion.complete()

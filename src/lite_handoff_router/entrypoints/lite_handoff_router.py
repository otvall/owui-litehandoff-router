"""Public pipe; continuations remain in OWUI's native tool loop."""

from copy import deepcopy

from pydantic import BaseModel, Field

from ..model_execution import prepare_model
from ..agent_history_block import pack_agent_history
from ..owui_adapter import OWUIAdapter
from ..protocol import confirmed_target
from ..runtime import require, metadata_draft, publish
from ..history import refresh_source
from ..capabilities import close_mcp_sessions
from ..observation import observe_response
from ..native_context import capture_native_context
from ..shared_summary import (ProviderContextOverflow, SharedSummary, check_initial_stream,
                              last_router_prompt_tokens, prompt_token_count)


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
        policy = state["context_policy"]
        summary_enabled = bool(policy["shared_summary_model_id"].strip()) and not state.get("worker")
        sessions_before = set(__request__.scope.get("lite_handoff_mcp", {}))
        try:
            draft, tools = await prepare_model(
                adapter, __request__, body, user, metadata_draft(__metadata__), state,
                self.valves.orchestrator_model_id,
                {"__event_emitter__": __event_emitter__, "__event_call__": __event_call__,
                 "__oauth_token__": __oauth_token__}, target=target,
            )
            if summary_enabled:
                summary = SharedSummary(adapter, __request__, user, state, emitter=__event_emitter__)
                packet = state.get("shared_summary_packet")
                full_messages = deepcopy(draft["messages"])
                await summary.load()
                summary.apply(draft, packet)
                previous_tokens = last_router_prompt_tokens(state, draft["model"]) if not target else None
                if previous_tokens is not None and previous_tokens > policy["router_summary_trigger_tokens"]:
                    await summary.extend(draft, packet, full_messages)
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
                agent = state["registry"][target]
                await __event_emitter__({"type": "status", "data": {
                    "action": "handoff", "description": "Передача: " + agent.get("agent_name", target), "done": True,
                }})
        track_usage = summary_enabled and not target
        async def complete_prepared():
            if target and not state.get("worker"):
                pack_agent_history(
                    draft["messages"], state, state.get("agent_history_question"),
                    covered_turns=summary.applied_turns if summary_enabled else 0,
                    packet=state.get("shared_summary_packet"),
                )
                if policy["agent_token_count_enabled"]:
                    await adapter.measure_agent_tokens(__request__, draft, user)
            response = await adapter.complete(__request__, draft, user)
            return await check_initial_stream(response) if summary_enabled else response

        try:
            response = await complete_prepared()
        except ProviderContextOverflow:
            if not summary_enabled or not await summary.extend(draft, packet, full_messages):
                raise
            response = await complete_prepared()
        executor = {"kind": "agent", "agent_id": target} if target else {"kind": "router"}
        on_usage = None
        if track_usage:
            async def record_router_usage(usage):
                tokens = prompt_token_count(usage)
                if tokens is None:
                    return
                sample = {"prompt_tokens": tokens}
                model = draft["model"]
                state.setdefault("router_usage", {})[model] = sample
                await adapter.save_router_usage(state, user, model, sample)

            if isinstance(response, dict):
                await record_router_usage(response.get("usage"))
            else:
                on_usage = record_router_usage
        return observe_response(response, state, executor, on_usage=on_usage)

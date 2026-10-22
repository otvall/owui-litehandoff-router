import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lite_handoff_router.entrypoints import lite_handoff_router
from lite_handoff_router.protocol import delegate
from lite_handoff_router.runtime import begin
from tests.test_isolation import IsolatedAdapter
from tests.test_protocol import call
from tests.test_router import User


class AtomicAdapter(IsolatedAdapter):
    fail = False

    async def tools(self, request, tool_ids, user, extra):
        tools = await super().tools(request, tool_ids, user, extra)
        if "a_tool" in tools:
            async def echo(__messages__=None):
                return __messages__
            tools["a_tool"]["callable"] = echo
        return tools

    async def inlets(self, request, body, model, extra):
        assert model["id"] == "a" and body["model"] == "a"
        assert set(body["metadata"]["tools"]) == {"a_tool"}
        assert "A_PROMPT" in str(body["messages"]) and "ROUTER_PROMPT" not in str(body["messages"])
        assert request.state.metadata is body["metadata"]
        body["messages"] = body["messages"] + [{"role": "system", "content": "INLET_EDIT"}]
        if self.fail:
            body["metadata"]["tools"].clear()
            raise ValueError("Destination filter failed")
        return body


class AtomicTest(unittest.IsolatedAsyncioTestCase):
    async def test_filter_effective_context_and_rollback(self):
        adapter = AtomicAdapter()
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={
            "router": {"id": "router", "info": {"params": {"system": "ROUTER_PROMPT"}, "meta": {}}},
            "a": {"id": "a", "owned_by": "openai", "info": {"params": {"system": "A_PROMPT"}, "meta": {"toolIds": ["a"]}}},
            "llm": {"id": "llm", "owned_by": "openai"},
        })))
        body = {"model": "router", "messages": [{"role": "user", "content": "task"}]}
        metadata = {"message_id": "message"}
        state = begin(request, body, metadata, "u", {"a": {"agent_id": "a", "skill_id": "route-a", "name": "A"}}, [], ["base", "delegate"], [])
        pipe = lite_handoff_router.Pipe()
        pipe.valves.orchestrator_model_id = "llm"
        with patch.object(lite_handoff_router, "OWUIAdapter", return_value=adapter):
            await pipe.pipe(body, request, {"id": "u"}, metadata)
            old_tools, old_names = metadata["tools"], set(metadata["tools"])
            marker = await delegate(adapter, request, User(), state, "a")
            continuation = {**body, "messages": body["messages"] + [
                {"role": "assistant", "tool_calls": [call("lite_delegate", "d", {"agent_id": "a"})]},
                {"role": "tool", "tool_call_id": "d", "content": marker},
            ]}
            adapter.fail = True
            with self.assertRaisesRegex(ValueError, "filter failed"):
                await pipe.pipe(continuation, request, {"id": "u"}, metadata)
            self.assertIsNone(state["active_agent"])
            self.assertIs(metadata["tools"], old_tools)
            self.assertEqual(set(metadata["tools"]), old_names)
            self.assertNotIn("lite_handoff_preparing", request.scope)
            adapter.fail = False
            await pipe.pipe(continuation, request, {"id": "u"}, metadata)
            observed = await metadata["tools"]["a_tool"]["callable"]()
            self.assertIn("INLET_EDIT", str(observed))
            self.assertNotIn("ROUTER_PROMPT", str(observed))
            self.assertEqual(state["active_agent"], "a")

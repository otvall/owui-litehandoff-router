import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lite_handoff_router.entrypoints import lite_handoff_router
from lite_handoff_router.protocol import delegate
from lite_handoff_router.runtime import begin
from tests.test_protocol import call
from tests.test_router import Adapter, User


class IsolatedAdapter(Adapter):
    async def model(self, request, model_id, user):
        return copy.deepcopy(request.app.state.MODELS[model_id])

    async def tools(self, request, tool_ids, user, extra):
        result = {}
        for identifier in tool_ids:
            name = "lite_delegate" if identifier == "delegate" else identifier + "_tool"

            async def operation():
                return "result"
            spec = {"name": name, "parameters": {"type": "object", "properties": {}}}
            if name == "lite_delegate":
                spec["parameters"]["properties"]["agent_id"] = {"type": "string"}
            result[name] = {"tool_id": identifier, "spec": spec, "callable": operation}
        return result

    async def complete(self, request, body, user):
        self.calls.append({"model": body["model"], "tools": set(request.state.metadata["tools"]),
                           "native": request.state.metadata["params"]["function_calling"]})
        return {"choices": [{"message": {"content": "answer"}}]}


class IsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_user_valves_and_router_card_cannot_union_or_disable_sets(self):
        models = {
            "router": {"id": "router", "info": {"params": {"system": "Router"}, "meta": {"toolIds": ["forbidden"], "skillIds": ["forbidden-skill"]}}},
            "a": {"id": "a", "owned_by": "openai", "info": {"params": {"system": "A"}, "meta": {"toolIds": ["a", "delegate"]}}},
            "b": {"id": "b", "owned_by": "openai", "info": {"params": {"system": "B"}, "meta": {"toolIds": ["b"]}}},
            "llm": {"id": "llm", "owned_by": "openai"},
        }
        adapter = IsolatedAdapter()
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
        pipe = lite_handoff_router.Pipe()
        pipe.valves.orchestrator_model_id = "llm"
        registry = {key: {"agent_id": key, "skill_id": "route-" + key, "name": key} for key in ("a", "b")}
        with patch.object(lite_handoff_router, "OWUIAdapter", return_value=adapter):
            for index, target in enumerate(("a", "b")):
                metadata = {"message_id": str(index), "params": {"function_calling": "legacy"}}
                body = {"model": "router", "messages": [{"role": "user", "content": "task"}],
                        "tool_ids": ["b"], "skill_ids": ["forbidden-skill"], "tools": [],
                        "base_tool_ids": [], "base_skill_ids": ["client"], "params": {"function_calling": "legacy"}}
                state = begin(request, body, metadata, "u", registry, [], ["base", "delegate"], [])
                fake_user_valves = {"id": "u", "valves": {"base_tool_ids": [], "skill_ids": ["foreign"]}}
                await pipe.pipe(body, request, fake_user_valves, metadata)
                self.assertTrue(adapter.calls[-1]["native"] == "native")
                self.assertIn("base_tool", adapter.calls[-1]["tools"])
                self.assertNotIn("forbidden_tool", adapter.calls[-1]["tools"])
                marker = await delegate(adapter, request, User(), state, target)
                body["messages"] += [
                    {"role": "assistant", "tool_calls": [call("lite_delegate", "d", {"agent_id": target})]},
                    {"role": "tool", "tool_call_id": "d", "content": marker},
                ]
                await pipe.pipe(body, request, fake_user_valves, metadata)
                self.assertEqual(adapter.calls[-1]["tools"], {target + "_tool"})
                self.assertNotIn("tool_ids", metadata)
                self.assertNotIn("skill_ids", metadata)

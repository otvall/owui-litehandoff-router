import copy
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lite_handoff_router.entrypoints import lite_handoff_router
from lite_handoff_router.runtime import begin
from lite_handoff_router.model_execution import prepare_model


class User:
    id = "u"

    def model_dump(self):
        return {"id": self.id, "role": "user"}


class Adapter:
    def __init__(self):
        self.calls = []

    async def user(self, user_id):
        return User()

    async def model(self, request, model_id, user):
        return {"id": model_id, "info": {"params": {"system": "Router"}, "meta": {"toolIds": [], "skillIds": []}}}

    async def prompt_and_params(self, body, model, user, metadata):
        body["messages"].insert(0, {"role": "system", "content": model["info"]["params"]["system"]})
        return body

    async def skill(self, skill_id, user):
        return {"name": "Route", "description": "Profiled questions"}

    async def tools(self, request, ids, user, extra):
        async def base_helper():
            return "base works"

        async def delegate(agent_id):
            return "pending"

        return {
            "base_helper": {"tool_id": "base", "spec": {"name": "base_helper", "parameters": {}}, "callable": base_helper},
            "lite_delegate": {"tool_id": "delegate", "spec": {"name": "lite_delegate", "parameters": {
                "type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"],
            }}, "callable": delegate},
        }

    async def builtins(self, request, model, extra):
        return {name: {"spec": {"name": name, "parameters": {}}, "callable": lambda: None}
                for name in ("view_skill", "read_skill_file", "grep_skill_files")}

    async def inlets(self, request, body, model, extra):
        return body

    async def complete(self, request, body, user):
        self.calls.append(copy.deepcopy(body["messages"]))
        self.assertion = await request.state.metadata["tools"]["base_helper"]["callable"]()
        return {"choices": [{"message": {"role": "assistant", "content": "General answer"}}]}


class RouterTest(unittest.IsolatedAsyncioTestCase):
    async def test_personal_manifest_enum_and_empty_registry_do_not_mutate_shared_schema(self):
        adapter = Adapter()
        shared = await adapter.tools(None, [], User(), {})

        async def shared_tools(*args):
            await asyncio.sleep(0)
            return shared
        adapter.tools = shared_tools
        request = SimpleNamespace(scope={}, app=SimpleNamespace(state=SimpleNamespace(MODELS={"llm": {"owned_by": "openai"}})))

        async def prepare(agent, base_skills):
            registry = {agent: {"agent_id": agent, "skill_id": "route-" + agent, "name": agent, "description": "Purpose"}} if agent else {}
            state = {"request_id": "request", "router_id": "router", "source": [{"role": "user", "content": "Hello"}],
                     "registry": registry, "base_tool_ids": ["base", "delegate"], "base_skill_ids": base_skills}
            return await prepare_model(adapter, request, {}, User(), {}, state, "llm", {})

        one, two = await asyncio.gather(prepare("agent-a", []), prepare("agent-b", []))
        for agent, (body, tools) in zip(("agent-a", "agent-b"), (one, two)):
            self.assertEqual(tools["lite_delegate"]["spec"]["parameters"]["properties"]["agent_id"]["enum"], [agent])
            self.assertIn('"agent_id": "' + agent + '"', body["messages"][0]["content"])
            self.assertIn('"skill_id": "route-' + agent + '"', body["messages"][0]["content"])
        self.assertNotIn("enum", shared["lite_delegate"]["spec"]["parameters"]["properties"]["agent_id"])
        body, tools = await prepare(None, ["base-skill"])
        self.assertNotIn("lite_delegate", tools)
        self.assertIn("base_helper", tools)
        self.assertIn("base-skill", body["messages"][0]["content"])

    async def test_empty_card_uses_valves_in_one_normal_generation(self):
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(
            MODELS={"llm": {"id": "llm", "owned_by": "openai"}}
        )))
        body = {"model": "router", "messages": [{"role": "user", "content": "Hi"}],
                "tool_ids": [], "skill_ids": [], "temperature": 99}
        metadata = {}
        begin(request, body, metadata, "u", {"agent-a": {
            "agent_id": "agent-a", "skill_id": "route-a", "name": "Route", "description": "Profiled questions",
        }}, [], ["base", "delegate"], [])
        original = copy.deepcopy(body)
        adapter = Adapter()
        pipe = lite_handoff_router.Pipe()
        pipe.valves.orchestrator_model_id = "llm"
        with patch.object(lite_handoff_router, "OWUIAdapter", return_value=adapter):
            result = await pipe.pipe(body, request, {"id": "u"}, metadata)
        self.assertEqual(adapter.assertion, "base works")
        self.assertEqual(len(adapter.calls), 1)
        self.assertIn('"skill_id": "route-a"', str(adapter.calls))
        self.assertNotIn("tool_ids", body)
        self.assertNotIn("skill_ids", body)
        self.assertNotIn("tool_ids", metadata)
        self.assertEqual(result["choices"][0]["message"]["content"], "General answer")

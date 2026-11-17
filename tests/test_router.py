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

    async def owner(self, model):
        return User()

    async def tool_access(self, request, identifier, owner):
        pass

    async def model(self, request, model_id, user):
        return {"id": model_id, "info": {"params": {"system": "Router"}, "meta": {"toolIds": ["base"], "skillIds": []}}}

    async def prompt_and_params(self, body, model, user, metadata):
        body["messages"].insert(0, {"role": "system", "content": model["info"]["params"]["system"]})
        return body

    async def skill(self, skill_id, user):
        return {"name": "Route", "description": "Profiled questions"}

    async def tools(self, request, ids, user, extra):
        async def base_helper():
            return "base works"

        return {
            "base_helper": {"tool_id": "base", "spec": {"name": "base_helper", "parameters": {}}, "callable": base_helper},
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
    async def test_router_uses_chat_selected_terminal_with_capability_enabled(self):
        adapter = Adapter()
        model = await adapter.model(None, "router", User())
        model["info"]["meta"].update(capabilities={"terminal": True}, terminalId=None)
        async def get_model(*args):
            return model
        adapter.model = get_model
        async def terminal_tools(request, identifier, owner, extra):
            return {"run_command": {"tool_id": "terminal:" + identifier, "type": "terminal",
                    "spec": {"name": "run_command", "parameters": {}}, "callable": lambda: "works"}}, "Terminal prompt"
        adapter.terminal_tools = terminal_tools
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(
            MODELS={"llm": {"owned_by": "openai"}})))
        body = {"model": "router", "messages": [], "terminal_id": "chat-terminal"}
        metadata = {}
        state = begin(request, body, metadata, "u", {}, [], [], [])
        # Native middleware moves the UI selection from the body to metadata.
        metadata["terminal_id"] = body.pop("terminal_id")
        draft, tools = await prepare_model(adapter, request, body, User(), metadata, state, "llm", {})
        self.assertIn("run_command", tools)
        self.assertEqual(draft["metadata"]["terminal_id"], "chat-terminal")
        model["info"]["meta"]["terminalId"] = "default-terminal"
        draft, tools = await prepare_model(adapter, request, body, User(), metadata, state, "llm", {})
        self.assertEqual(draft["metadata"]["terminal_id"], "chat-terminal")
        model["info"]["meta"]["capabilities"]["terminal"] = False
        draft, tools = await prepare_model(adapter, request, body, User(), metadata, state, "llm", {})
        self.assertNotIn("run_command", tools)
        self.assertNotIn("terminal_id", draft["metadata"])
        # A copied metadata selection cannot enable a terminal in a new request.
        model["info"]["meta"].update(capabilities={"terminal": True}, terminalId=None)
        state = begin(request, body, metadata, "u", {}, [], [], [])
        metadata["terminal_id"] = "copied-selection"
        draft, tools = await prepare_model(adapter, request, body, User(), metadata, state, "llm", {})
        self.assertNotIn("run_command", tools)

    async def test_card_resources_add_to_router_routing_and_valve_resources(self):
        adapter = Adapter()
        model = await adapter.model(None, "router", User())
        model["info"]["meta"].update(toolIds=["card-tool"], skillIds=["card-skill"], terminalId="term")
        async def get_model(*args):
            return model
        adapter.model = get_model
        original_tools = adapter.tools
        async def load_tools(request, identifiers, owner, extra):
            self.assertEqual(set(identifiers), {"base", "card-tool"})
            loaded = await original_tools(request, identifiers, owner, extra)
            loaded["card_helper"] = {"tool_id": "card-tool", "spec": {"name": "card_helper", "parameters": {}},
                                     "callable": lambda: "card works"}
            return loaded
        adapter.tools = load_tools
        async def terminal_tools(request, identifier, owner, extra):
            self.assertEqual(identifier, "term")
            return {"run_command": {"tool_id": "terminal:term", "type": "terminal",
                    "spec": {"name": "run_command", "parameters": {}}, "callable": lambda: "terminal works"}}, "Terminal prompt"
        async def terminal_access(*args):
            pass
        adapter.terminal_tools = terminal_tools
        adapter.terminal_access = terminal_access
        request = SimpleNamespace(scope={}, app=SimpleNamespace(state=SimpleNamespace(MODELS={"llm": {"owned_by": "openai"}})))
        state = {"request_id": "request", "router_id": "router", "source": [],
                 "registry": {"a": {"agent_id": "a", "skill_id": "route-a", "name": "A"}},
                 "base_tool_ids": ["base"], "base_skill_ids": ["base-skill"]}
        body, tools = await prepare_model(adapter, request, {}, User(), {}, state, "llm", {})
        self.assertTrue({"base_helper", "card_helper", "run_command", "lite_delegate", "view_skill"} <= tools.keys())
        systems = "\n".join(m["content"] for m in body["messages"] if m["role"] == "system")
        for expected in ("card-skill", "base-skill", "route-a", "Terminal prompt", "Router"):
            self.assertIn(expected, systems)
        self.assertEqual(await tools["card_helper"]["callable"](), "card works")
        self.assertEqual(await tools["run_command"]["callable"](), "terminal works")
        model["info"]["meta"]["toolIds"] = []
        with self.assertRaisesRegex(ValueError, "detached"):
            await tools["card_helper"]["callable"]()

    async def test_separate_delegate_tool_cannot_replace_the_inline_callable(self):
        adapter = Adapter()
        loaded = await adapter.tools(None, [], User(), {})
        loaded["lite_delegate"] = {"spec": {"name": "lite_delegate"}, "callable": lambda: "untrusted"}

        async def tools(*args):
            return loaded
        adapter.tools = tools
        request = SimpleNamespace(scope={}, app=SimpleNamespace(state=SimpleNamespace(MODELS={"llm": {"owned_by": "openai"}})))
        state = {"request_id": "request", "router_id": "router", "source": [], "registry": {"a": {"agent_id": "a", "skill_id": "route-a", "name": "A"}},
                 "base_tool_ids": ["base"], "base_skill_ids": []}
        with self.assertRaisesRegex(ValueError, "Reserved delegate Tool collision"):
            await prepare_model(adapter, request, {}, User(), {}, state, "llm", {})

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
                     "registry": registry, "base_tool_ids": ["base"], "base_skill_ids": base_skills}
            return await prepare_model(adapter, request, {}, User(), {}, state, "llm", {})

        one, two = await asyncio.gather(prepare("agent-a", []), prepare("agent-b", []))
        for agent, (body, tools) in zip(("agent-a", "agent-b"), (one, two)):
            self.assertEqual(tools["lite_delegate"]["spec"]["parameters"]["properties"]["agent_id"]["enum"], [agent])
            self.assertIn('agent_id="' + agent + '"', body["messages"][0]["content"])
            self.assertIn('skill_id="route-' + agent + '"', body["messages"][0]["content"])
        self.assertNotIn("lite_delegate", shared)
        self.assertIsNot(one[1]["lite_delegate"]["spec"], two[1]["lite_delegate"]["spec"])
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
        }}, [], ["base"], [])
        original = copy.deepcopy(body)
        adapter = Adapter()
        pipe = lite_handoff_router.Pipe()
        pipe.valves.orchestrator_model_id = "llm"
        with patch.object(lite_handoff_router, "OWUIAdapter", return_value=adapter):
            result = await pipe.pipe(body, request, {"id": "u"}, metadata)
        self.assertEqual(adapter.assertion, "base works")
        self.assertEqual(len(adapter.calls), 1)
        self.assertIn('skill_id="route-a"', str(adapter.calls))
        self.assertNotIn("tool_ids", body)
        self.assertNotIn("skill_ids", body)
        self.assertNotIn("tool_ids", metadata)
        self.assertEqual(result["choices"][0]["message"]["content"], "General answer")

import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from lite_handoff_router.model_execution import AGENT_HISTORY_PROMPT, prepare_model
from lite_handoff_router.runtime import begin, metadata_draft
from tests.context_helpers import catalog_ids, catalog_result, last_question
from tests.test_history import turn
from tests.test_isolation import IsolatedAdapter
from tests.test_protocol import call
from tests.test_router import User


class CatalogAdapter(IsolatedAdapter):
    def __init__(self):
        super().__init__()
        self.seen = []
        self.attack = None

    async def skill(self, identifier, user):
        return {"name": identifier, "description": "Description " + identifier}

    async def tools(self, request, tool_ids, user, extra):
        async def inspect_context(__messages__=None):
            self.seen.append(copy.deepcopy(__messages__))
            return "ok"
        return {"inspect_context": {"tool_id": "work", "spec": {"name": "inspect_context", "parameters": {}},
                                    "callable": inspect_context}}

    async def inlets(self, request, body, model, extra):
        body = copy.deepcopy(body)
        ids = catalog_ids(body["messages"])
        if self.attack == "remove":
            body["messages"] = [m for m in body["messages"] if not
                (m.get("tool_call_id") in ids or any(c.get("id") in ids for c in m.get("tool_calls", [])))]
        elif self.attack == "edit":
            next(m for m in body["messages"] if m.get("tool_call_id") in ids)["content"] = "FOREIGN_CATALOG"
        elif self.attack == "duplicate":
            body["messages"].extend(copy.deepcopy(body["messages"][-2:]))
        elif self.attack == "schema":
            body["tools"].append({"type": "function", "function": {"name": "list_available_skills", "parameters": {}}})
        elif self.attack == "create":
            body["messages"].append({"role": "assistant", "tool_calls": [call("list_available_skills", "fake", {})]})
        return body


class SkillCatalogContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_shared_id_is_never_an_ordinary_skill_even_when_selected_and_attached(self):
        request, body, metadata, state = self.setup(0)
        state.update(routing_skill_id="shared", base_skill_ids=["shared"],
                     chat_skill_ids=["shared", "ordinary"],
                     registry={"a": {"agent_id": "a", "skill_id": "shared", "name": "Shared",
                                     "description": "Forecast", "path": "profiles/a.md", "skill_version_id": "v1"}})
        request.app.state.MODELS["router"]["info"]["meta"]["skillIds"].append("shared")
        request.app.state.MODELS["a"]["info"]["meta"]["skillIds"].append("SHARED")
        adapter = CatalogAdapter()
        builtins = await adapter.builtins(request, {}, {})

        async def view_skill(id, __user__=None):
            return "Unexpected unguarded read"

        builtins["view_skill"]["callable"] = view_skill
        adapter.builtins = AsyncMock(return_value=builtins)
        draft, _ = await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {})
        catalog = catalog_result(draft["messages"])
        self.assertEqual(catalog.count("<id>shared</id>"), 1)
        self.assertIn("<path>profiles/a.md</path>", catalog)
        self.assertIn("<id>ordinary</id>", catalog)
        agent, tools = await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {},
                                           target="a")
        self.assertNotIn("shared", catalog_result(agent["messages"]).lower())
        self.assertIn("<id>agent-guide</id>", catalog_result(agent["messages"]))
        with self.assertRaisesRegex(ValueError, "current model scope"):
            await tools["view_skill"]["callable"](id="shared")
        state["registry"] = {}
        empty, tools = await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {})
        self.assertNotIn("<id>shared</id>", catalog_result(empty["messages"]))
        self.assertNotIn("lite_delegate", tools)

    def setup(self, limit):
        models = {
            "router": {"id": "router", "info": {"params": {"system": "ROUTER"},
                       "meta": {"toolIds": ["work"], "skillIds": ["router-guide"]}}},
            "a": {"id": "a", "owned_by": "openai", "info": {"params": {"system": "AGENT"},
                  "meta": {"toolIds": ["work"], "skillIds": ["agent-guide"]}}},
            "llm": {"id": "llm", "owned_by": "openai"},
        }
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
        source = turn("a", "OLD_WORKING_HTML") + [
            {"role": "assistant", "tool_calls": [call("list_available_skills", "stale", {})]},
            {"role": "tool", "tool_call_id": "stale", "content": "OLD_WIDE_CATALOG"},
            {"role": "user", "content": "Actual question"}]
        body, metadata = {"model": "router", "messages": source}, {}
        state = begin(request, body, metadata, "u", {"a": {"agent_id": "a", "skill_id": "route-a"}}, [], [], [],
                      chat_skill_ids=["chat-guide"], context_policy={"tool_result_history_limit": limit})
        return request, body, metadata, state

    async def test_catalog_is_fresh_protected_context_and_does_not_consume_n(self):
        for limit in (None, 0, 1):
            with self.subTest(limit=limit):
                request, body, metadata, state = self.setup(limit)
                original = copy.deepcopy(state["source"])
                adapter = CatalogAdapter()
                draft, tools = await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {})
                self.assertNotIn("<routing_skills", draft["messages"][0]["content"])
                catalog = catalog_result(draft["messages"])
                for identifier in ("route-a", "router-guide", "chat-guide"):
                    self.assertIn("<id>" + identifier + "</id>", catalog)
                self.assertNotIn("OLD_WIDE_CATALOG", str(draft))
                self.assertEqual("OLD_WORKING_HTML" in str(draft), limit != 0)
                self.assertNotIn("list_available_skills", tools)
                self.assertNotIn("list_available_skills", [t["function"]["name"] for t in draft["tools"]])
                self.assertEqual(last_question(draft["messages"]), original[-1])
                await tools["inspect_context"]["callable"]()
                self.assertEqual(adapter.seen[-1], draft["messages"])
                first_ids = catalog_ids(draft["messages"])
                request.app.state.MODELS["router"]["info"]["meta"]["skillIds"] = ["replacement-guide"]
                second, _ = await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {})
                self.assertEqual(catalog_ids(second["messages"]), first_ids)
                self.assertIn("replacement-guide", catalog_result(second["messages"]))
                self.assertNotIn("router-guide", catalog_result(second["messages"]))
                agent, _ = await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {}, target="a")
                self.assertNotEqual(catalog_ids(agent["messages"]), first_ids)
                self.assertIn("agent-guide", catalog_result(agent["messages"]))
                for forbidden in ("routing_skills", "router-guide", "route-a", "chat-guide"):
                    self.assertNotIn(forbidden, catalog_result(agent["messages"]))
                self.assertEqual(state["source"], original)
                self.assertEqual(state["audit"], [])
                self.assertEqual(state["pending"], [])

    async def test_agents_without_skills_receive_neither_catalog_nor_reading_instructions(self):
        for worker in (False, True):
            with self.subTest(worker=worker):
                request, body, metadata, state = self.setup(0)
                request.app.state.MODELS["a"]["info"]["meta"]["skillIds"] = []
                state["worker"] = worker
                draft, tools = await prepare_model(
                    CatalogAdapter(), request, body, User(), metadata_draft(metadata), state, "llm", {}, target="a")
                self.assertEqual(catalog_ids(draft["messages"]), set())
                for name in ("list_available_skills", "view_skill", "read_skill_file", "grep_skill_files"):
                    self.assertNotIn(name, str(draft["messages"]))
                    self.assertNotIn(name, tools)
                self.assertNotIn("OLD_WIDE_CATALOG", str(draft))
                self.assertEqual(last_question(draft["messages"])["content"], "Actual question")

    async def test_disabled_builtin_skills_do_not_break_model_preparation(self):
        for target, worker in ((None, False), ("a", False), ("a", True)):
            with self.subTest(target=target, worker=worker):
                request, body, metadata, state = self.setup(0)
                model = request.app.state.MODELS[target or "router"]
                model["info"]["meta"].update(capabilities={"builtin_tools": True}, builtinTools={"skills": False})
                state["worker"] = worker
                adapter = CatalogAdapter()
                adapter.builtins = AsyncMock(return_value={})
                draft, tools = await prepare_model(
                    adapter, request, body, User(), metadata_draft(metadata), state, "llm", {}, target=target)
                self.assertEqual(catalog_ids(draft["messages"]), set())
                self.assertNotIn("view_skill", str(draft["messages"]))
                self.assertEqual(draft["metadata"]["lite_skill_scope"]["skill_ids"], [])
                self.assertEqual(set(tools), {"inspect_context"})
                self.assertEqual(last_question(draft["messages"])["content"], "Actual question")

    async def test_inlet_cannot_create_catalog_for_agent_without_skills(self):
        request, body, metadata, state = self.setup(None)
        request.app.state.MODELS["a"]["info"]["meta"]["skillIds"] = []
        adapter = CatalogAdapter()
        adapter.attack = "create"
        with self.assertRaisesRegex(ValueError, "protected Skill catalog"):
            await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {}, target="a")

    async def test_agent_prompt_precedes_role_and_mcp_instructions_with_catalog_as_tool_result(self):
        request, body, metadata, state = self.setup(1)
        instructions = ["MCP server first instructions:\nFirst", "MCP server second instructions:\nSecond"]
        with patch("lite_handoff_router.model_execution.load_capabilities",
                   AsyncMock(return_value=({}, instructions))):
            draft, tools = await prepare_model(
                CatalogAdapter(), request, body, User(), metadata_draft(metadata), state, "llm", {}, target="a")
        systems = [message["content"] for message in draft["messages"] if message["role"] == "system"]
        self.assertEqual(systems, ["\n\n".join(["AGENT", AGENT_HISTORY_PROMPT, *instructions])])
        self.assertIn("agent-guide", catalog_result(draft["messages"]))
        self.assertIn("view_skill", tools)

    async def test_inlets_cannot_mutate_catalog_or_make_it_callable_even_without_n(self):
        for limit in (None, 0):
            for attack in ("remove", "edit", "duplicate", "schema"):
                with self.subTest(limit=limit, attack=attack):
                    request, body, metadata, state = self.setup(limit)
                    adapter = CatalogAdapter()
                    adapter.attack = attack
                    with self.assertRaisesRegex(ValueError, "protected Skill catalog|reserved Tool"):
                        await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {}, target="a")
                    self.assertEqual(metadata["tools"], {})

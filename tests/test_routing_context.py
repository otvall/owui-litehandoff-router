import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

from lite_handoff_router.history import context_handoff_key, router_projection
from lite_handoff_router.model_execution import compose_router_system, prepare_model
from lite_handoff_router.owui_adapter import saved_handoff_statuses
from lite_handoff_router.skills import manifest
from lite_handoff_router.tool_history import ToolResultWindow
from lite_handoff_router.protocol import object_value
from tests.test_history import turn
from tests.test_protocol import call
from tests.test_router import Adapter, User


def packets(view):
    return [object_value(m["content"]) for m in view if m.get("role") == "tool"
            and "tool_results" in object_value(m.get("content"))]


class HistoricalContextTest(unittest.TestCase):
    def test_three_questions_two_agents_preserve_roles_and_receipts(self):
        source = turn("a", "A_HTML") + turn("b", "B_HTML") + [{"role": "user", "content": "Third question"}]
        original = copy.deepcopy(source)
        state = {"source": source, "user_boundary": 12, "audit": []}
        for limit in (None, 0, 1, 2):
            with self.subTest(limit=limit):
                window = ToolResultWindow(limit)
                view = router_projection(state, set(), set(), window=window)
                window.apply(view)
                self.assertEqual([m["content"] for m in view if m["role"] == "user"],
                                 ["task a", "task b", "Third question"])
                self.assertEqual([m["role"] for m in view], ["user", "assistant", "tool", "assistant"] * 2 + ["user"])
                self.assertEqual([c["function"]["name"] for m in view for c in m.get("tool_calls", [])],
                                 ["lite_delegate", "lite_delegate"])
                one, two = packets(view)
                self.assertEqual((one["agent_id"], two["agent_id"]), ("a", "b"))
                self.assertEqual(one["status"], "unknown")
                self.assertEqual("A_HTML" in str(view), limit in (None, 2))
                self.assertEqual("B_HTML" in str(view), limit in (None, 1, 2))
                self.assertNotIn("server-receipt", str(view))
                self.assertEqual(view[-1], source[-1])
                self.assertEqual(state["source"], original)

    def test_new_work_evicts_old_nested_results_without_counting_handoff(self):
        source = turn("a", "A_HTML") + turn("b", "B_HTML") + [{"role": "user", "content": "Current"}]
        state = {"source": source, "user_boundary": 12, "audit": []}
        for count in (0, 1, 2):
            if count:
                source.extend([{"role": "assistant", "tool_calls": [call("lookup", "new-" + str(count), {})]},
                               {"role": "tool", "tool_call_id": "new-" + str(count), "content": "CURRENT_" + str(count)}])
                state["audit"].append({"id": "new-" + str(count), "name": "lookup", "arguments": {},
                                       "executor": {"kind": "router"}, "generation": count})
            window = ToolResultWindow(2)
            view = router_projection(state, {"lookup"}, set(), window=window)
            window.apply(view)
            self.assertEqual("A_HTML" in str(view), count == 0)
            self.assertEqual("B_HTML" in str(view), count < 2)
            self.assertEqual(len(packets(view)), 2)

    def test_no_tool_handoff_unknown_work_and_router_calls_remain_distinct(self):
        source = turn("a", "unused")
        del source[3:5]
        source += [{"role": "user", "content": "own task"},
                   {"role": "assistant", "tool_calls": [call("delegate_task", "parallel", {"agent_id": "b", "task": "work"})]},
                   {"role": "tool", "tool_call_id": "parallel", "content": "PARALLEL_RESULT"},
                   {"role": "user", "content": "next"}]
        state = {"source": source, "user_boundary": 7, "audit": []}
        view = router_projection(state, set(), set())
        self.assertEqual(packets(view)[0]["tool_results"], [])
        self.assertEqual(sum(c["function"]["name"] == "lite_delegate" for m in view for c in m.get("tool_calls", [])), 1)
        self.assertIn("PARALLEL_RESULT", str(view))
        self.assertEqual(view[-1], source[-1])

    def test_only_agents_work_enters_its_packet_in_grouped_owui_history(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/owui_0123_grouped.json").read_text())
        source = fixture["messages"] + [{"role": "user", "content": "next"}]
        state = {"source": source, "user_boundary": len(source) - 1, "audit": []}
        view = router_projection(state, set(), set())
        nested = packets(view)[0]["tool_results"]
        self.assertEqual([r["result"] for r in nested], ["AGENT_RESULT_42"])
        self.assertNotIn("FULL_ROUTING_INSTRUCTIONS", str(view))
        self.assertIn("ROUTER_BASE_RESULT", str(view))

    def test_saved_terminal_state_overrides_ack_and_does_not_mutate_output(self):
        output = turn("a", "HTML")[1:]
        original = copy.deepcopy(output)
        convert = lambda data, **kwargs: data
        key = context_handoff_key(json.loads(output[1]["content"]))
        for fields, expected in (({}, "unknown"), ({"done": False}, "unknown"),
                                 ({"done": True}, "completed"), ({"done": True, "error": "error"}, "failed"),
                                 ({"status": "cancelled"}, "cancelled"), ({"status": "timed_out"}, "timed_out")):
            with self.subTest(fields=fields):
                statuses = saved_handoff_statuses([{"role": "assistant", "output": output, **fields}], convert)
                self.assertEqual(statuses[key], expected)
                state = {"source": turn("a", "HTML"), "user_boundary": 6, "audit": [],
                         "historical_handoff_statuses": statuses}
                self.assertEqual(packets(router_projection(state, set(), set()))[0]["status"], expected)
        self.assertEqual(output, original)
        for item, expected in (({"type": "message", "status": "failed"}, "failed"),
                               ({"type": "function_call", "status": "failed"}, "completed")):
            statuses = saved_handoff_statuses(
                [{"role": "assistant", "output": output + [item], "done": True}], convert)
            self.assertEqual(statuses[key], expected)


class RoutingCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_xml_retains_scope_fields_and_escapes_metadata(self):
        class CatalogAdapter(Adapter):
            async def skill(self, skill_id, user):
                return {"name": 'Name <&> "' + skill_id, "description": "Desc <skill> & data"}
        registry = {"agent<&\"": {"agent_id": "agent<&\"", "skill_id": "route<&\"", "agent_name": "Name <agent>"}}
        text = await manifest(CatalogAdapter(), ["base", "route<&\""], User(), registry)
        root = ET.fromstring("<catalogs>" + text[text.index("<routing_skills"):] + "</catalogs>")
        routing = root.find("routing_skills")
        entry = routing.find("skill")
        self.assertEqual(entry.attrib, {})
        self.assertEqual([child.tag for child in entry], ["id", "agent_id", "agent_name", "name", "description"])
        self.assertEqual(entry.findtext("id"), "route<&\"")
        self.assertEqual(entry.findtext("agent_id"), "agent<&\"")
        self.assertEqual(entry.findtext("agent_name"), "Name <agent>")
        self.assertEqual(entry.findtext("name"), 'Name <&> "route<&\"')
        self.assertEqual(entry.findtext("description"), "Desc <skill> & data")
        self.assertEqual([child.tag for child in root], ["routing_skills", "available_skills"])
        base = root.find("available_skills/skill")
        self.assertEqual([child.tag for child in base], ["id", "name", "description"])
        self.assertEqual(base.findtext("id"), "base")
        self.assertIn("view_skill(id=id)", text)
        self.assertNotIn("skill_path", text)

    async def test_empty_catalog_and_one_system_keep_actual_question_and_tools(self):
        request = SimpleNamespace(scope={}, app=SimpleNamespace(state=SimpleNamespace(
            MODELS={"llm": {"owned_by": "openai"}})))
        source = [{"role": "user", "content": "Question"}]
        state = {"request_id": "r", "router_id": "router", "source": source,
                 "registry": {}, "base_tool_ids": [], "base_skill_ids": []}
        draft, tools = await prepare_model(Adapter(), request, {}, User(), {}, state, "llm", {})
        self.assertEqual(sum(m["role"] == "system" for m in draft["messages"]), 1)
        self.assertTrue(draft["messages"][0]["content"].startswith("Router\n\n"))
        self.assertIn("<routing_skills />", draft["messages"][0]["content"])
        self.assertEqual(draft["messages"][-1], source[-1])
        self.assertEqual([t["function"]["name"] for t in draft["tools"]], list(tools))

    def test_system_composition_preserves_section_order_and_user_data(self):
        core = {"role": "system", "content": "CORE"}
        rules = {"role": "system", "content": "RULES"}
        catalog = {"role": "system", "content": "CATALOG"}
        user = {"role": "user", "content": "USER_DATA"}
        messages = [{"role": "system", "content": "TERMINAL"}, catalog, rules, core, user,
                    {"role": "system", "content": "INLET"}]
        compose_router_system(messages, [core, rules, catalog])
        self.assertEqual(messages, [{"role": "system", "content": "CORE\n\nRULES\n\nCATALOG\n\nTERMINAL\n\nINLET"}, user])

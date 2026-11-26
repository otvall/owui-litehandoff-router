import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from lite_handoff_router.history import HISTORICAL_AGENT_TURN, agent_projection, router_projection
from lite_handoff_router.model_execution import prepare_model
from lite_handoff_router.protocol import completed_pairs
from lite_handoff_router.runtime import begin
from lite_handoff_router.native_context import capture_native_context
from lite_handoff_router.shared_summary import SharedSummary
from lite_handoff_router.tool_history import ToolResultWindow
from tests.test_history import turn
from tests.test_protocol import call
from tests.test_router import Adapter, User
from tests.test_native_context import pair


def projected(state, limit, agent=None):
    window = ToolResultWindow(limit)
    if agent:
        messages = agent_projection(state, agent, {"lookup", "view_skill"}, {"own"}, window=window)
    else:
        messages = router_projection(state, {"lookup", "view_skill"}, {"own"}, window=window)
    window.apply(messages)
    return messages


class ToolHistoryTest(unittest.TestCase):
    def test_html_window_and_restore_with_repeated_ids(self):
        source = turn("a", "<html>OLD_HTML</html>") + turn("b", "FOREIGN_HTML") + turn("a", "<html>NEW_HTML</html>")
        state = {"source": source, "user_boundary": len(source), "audit": []}
        original = copy.deepcopy(state)
        for limit, old, new in ((None, True, True), (0, False, False), (1, False, True), (2, True, True)):
            with self.subTest(limit=limit):
                messages = projected(state, limit, "a")
                text = str(messages)
                self.assertEqual("OLD_HTML" in text, old)
                self.assertEqual("NEW_HTML" in text, new)
                self.assertNotIn("FOREIGN_HTML", text)
                pairs = completed_pairs(messages)
                self.assertEqual(len(pairs), 3)
                batch, ordinal, result = pairs[1]
                self.assertEqual(messages[batch]["tool_calls"][ordinal]["function"], {
                    "name": HISTORICAL_AGENT_TURN, "arguments": "{}"})
                self.assertEqual(json.loads(messages[result]["content"]), {
                    "agent_id": "b", "reply": "Visible answer b",
                    "events": [{"type": "tool_calls", "calls": [
                        {"tool": "lookup", "status": "completed",
                         "result": "[Tool result withheld from other agents]"},
                    ]}]})
        self.assertEqual(state, original)

    def test_router_shares_one_window_between_handoff_and_current(self):
        source = turn("a", "OLD_HTML") + [{"role": "user", "content": "current"},
            {"role": "assistant", "tool_calls": [call("lookup", "current", {"q": "new"})]},
            {"role": "tool", "tool_call_id": "current", "content": "NEW_HTML"}]
        state = {"source": source, "user_boundary": 6, "audit": [
            {"id": "current", "name": "lookup", "arguments": {"q": "new"},
             "executor": {"kind": "router"}, "generation": 1}]}
        messages = projected(state, 1)
        self.assertNotIn("OLD_HTML", str(messages))
        self.assertIn("NEW_HTML", str(messages))
        reference = next(m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") == "d")
        packet = json.loads(reference["content"])
        records = packet["tool_results"]
        self.assertEqual(records[-1]["arguments"], {"q": "same"})
        self.assertEqual(packet["agent_id"], "a")
        self.assertIn("hidden by history limit", records[-1]["result"])

    def test_batch_order_not_completion_order_and_no_duplicate_payloads(self):
        source = [{"role": "user", "content": "current"},
                  {"role": "assistant", "tool_calls": [call("lookup", str(i), {}) for i in range(4)]}]
        source += [{"role": "tool", "tool_call_id": str(i), "content": "HTML_" + str(i),
                    "files": [{"body": "HTML_" + str(i)}], "output": "HTML_" + str(i)} for i in (3, 2, 1, 0)]
        state = {"source": source, "user_boundary": 0, "audit": [
            {"id": str(i), "name": "lookup", "arguments": {}, "executor": {"kind": "router"}, "generation": 1}
            for i in range(4)]}
        messages = projected(state, 2)
        for index in range(4):
            self.assertEqual("HTML_" + str(index) in str(messages), index >= 2)
        self.assertEqual(len(messages[1]["tool_calls"]), 4)
        self.assertEqual(len([m for m in messages if m["role"] == "tool"]), 4)

    def test_current_skill_does_not_consume_window(self):
        source = turn("a", "OLD_HTML")
        boundary = len(source)
        source += turn("a", "NEW_HTML")
        source += [{"role": "assistant", "tool_calls": [call("view_skill", "skill", {"id": "own"})]},
                   {"role": "tool", "tool_call_id": "skill", "content": "CURRENT_SKILL"}]
        state = {"source": source, "user_boundary": boundary, "audit": []}
        text = str(projected(state, 1, "a"))
        self.assertIn("CURRENT_SKILL", text)
        self.assertIn("NEW_HTML", text)
        self.assertNotIn("OLD_HTML", text)

    def test_known_error_status_and_arguments_survive_hiding(self):
        state = {"source": turn("a", '{"error":"PRIVATE_ERROR"}'), "user_boundary": 0, "audit": []}
        messages = projected(state, 0, "a")
        self.assertNotIn("PRIVATE_ERROR", str(messages))
        self.assertIn("status=error", str(messages))
        self.assertEqual(messages[1]["tool_calls"][0]["function"]["arguments"], json.dumps({"q": "same"}))

    def test_verified_native_context_is_not_hidden_or_counted(self):
        request = SimpleNamespace(scope={})
        state = begin(request, {"model": "router", "messages": [{"role": "user", "content": "question"}]},
                      {}, "u", {}, [], [], [])
        state["source"].extend(pair("CURRENT_MEMORY"))
        capture_native_context(request, {}, state, pair("CURRENT_MEMORY"))
        record = state["native_context"]["records"][0]
        state["native_context_allowed"] = {record["key"]: record["result"]}
        self.assertIn("CURRENT_MEMORY", str(projected(state, 0)))
        self.assertIn("CURRENT_MEMORY", str(projected(state, 0, "a")))

    def test_filter_cannot_replace_packet_or_add_orphan_results(self):
        state = {"source": turn("a", "HTML"), "user_boundary": 6, "audit": []}
        window = ToolResultWindow(0)
        messages = router_projection(state, set(), set(), window=window)
        reference = next(m for m in messages if m.get("role") == "tool")
        reference["content"] = reference["content"].replace('"agent_id": "a"', '"agent_id": "b"')
        with self.assertRaisesRegex(ValueError, "historical handoff packet"):
            window.apply(messages)
        window = ToolResultWindow(0)
        messages = agent_projection(state, "a", {"lookup"}, set(), window=window)
        messages.append({"role": "tool", "tool_call_id": "orphan", "content": "HTML"})
        with self.assertRaisesRegex(ValueError, "history structure"):
            window.apply(messages)

    def test_nested_inlet_result_edits_and_duplicate_html_are_hidden(self):
        state = {"source": turn("a", "OLD_HTML"), "user_boundary": 6, "audit": []}
        window = ToolResultWindow(0)
        messages = router_projection(state, set(), set(), window=window)
        result = next(m for m in messages if m.get("role") == "tool")
        packet = json.loads(result["content"])
        packet["tool_results"][0]["result"] = "UPDATED_HTML"
        result["content"] = json.dumps(packet)
        result["embeds"] = ["UPDATED_HTML"]
        result["output"] = "UPDATED_HTML"
        window.apply(messages)
        self.assertNotIn("HTML", str(messages))
        self.assertIn("hidden by history limit", str(messages))
        self.assertIn("OLD_HTML", str(state["source"]))


class ToolHistoryIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_retained_old_result_does_not_block_shared_summary(self):
        adapter = Adapter()
        adapter.shared_summary_cache = AsyncMock(return_value={})
        adapter.generate_shared_summary = AsyncMock(return_value="Old turn summary")
        adapter.save_shared_summary = AsyncMock(return_value="Old turn summary")
        current = {"role": "user", "content": "next"}
        source = turn("a", "RETAINED_RESULT") + [current]
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(
            MODELS={"llm": {"owned_by": "openai"}})))
        body, metadata = {"model": "router", "messages": source}, {}
        state = begin(request, body, metadata, "u", {}, [], [], [], context_policy={
            "tool_result_history_limit": 1, "shared_summary_model_id": "summary", "context_window_tokens": 3000,
        })
        draft, _ = await prepare_model(adapter, request, body, User(), metadata, state, "llm", {})
        self.assertIn("RETAINED_RESULT", str(draft["messages"]))
        summary = SharedSummary(adapter, request, User(), state)
        await summary.load(draft)
        self.assertTrue(await summary.extend(draft, None, copy.deepcopy(draft["messages"]), trigger="Бюджет"))
        self.assertIn("Old turn summary", str(draft["messages"]))
        self.assertNotIn("RETAINED_RESULT", str(draft["messages"]))
        self.assertEqual(state["source"], source)

    async def test_agent_inlet_edits_are_limited_before_callable_binding(self):
        seen = []

        class ViewingAdapter(Adapter):
            async def tools(self, request, ids, user, extra):
                async def base_helper(__messages__=None):
                    seen.append(copy.deepcopy(__messages__))
                    return "ok"
                return {"base_helper": {"tool_id": "base", "spec": {"name": "base_helper", "parameters": {}},
                                        "callable": base_helper}}

            async def inlets(self, request, body, model, extra):
                body = copy.deepcopy(body)
                body["messages"].insert(0, {"role": "system", "content": "INLET"})
                for message in body["messages"]:
                    if message.get("role") == "tool" and message.get("content") == "OLD_HTML":
                        message["content"] = "OLD_HTML_UPDATED"
                        message["embeds"] = ["OLD_HTML_UPDATED"]
                return body

        adapter = ViewingAdapter()
        source = turn("a", "OLD_HTML") + turn("a", "NEW_HTML") + [{"role": "user", "content": "next"}]
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(
            MODELS={"a": {"owned_by": "openai"}})))
        body = {"model": "router", "messages": source}
        metadata = {}
        state = begin(request, body, metadata, "u", {}, [], [], [],
                      context_policy={"tool_result_history_limit": 1})
        # This model's attached Tool shares its name with the recorded operation.
        for message in state["source"]:
            for item in message.get("tool_calls", []):
                if item["function"]["name"] == "lookup":
                    item["function"]["name"] = "base_helper"
        state["parallel"].update(status="completed", results=[{"agent_id": "b", "result": "FULL_GROUP", "files": []}])
        draft, tools = await prepare_model(adapter, request, body, User(), metadata, state, "llm", {}, target="a")
        self.assertNotIn("OLD_HTML", str(draft["messages"]))
        for expected in ("NEW_HTML", "INLET", "FULL_GROUP"):
            self.assertIn(expected, str(draft["messages"]))
        await tools["base_helper"]["callable"]()
        self.assertEqual(seen[0], draft["messages"])
        self.assertIn("OLD_HTML", str(state["source"]))

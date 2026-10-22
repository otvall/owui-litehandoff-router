import copy
import json
from pathlib import Path
import unittest

from lite_handoff_router.history import analyze, refresh_source, router_projection, agent_projection
from lite_handoff_router.runtime import begin
from types import SimpleNamespace
from tests.test_protocol import call

FIXTURE = Path(__file__).parent / "fixtures/owui_0123_grouped.json"


def turn(agent, value):
    marker = {"__lite_delegate__": "v2", "agent_id": agent, "request_id": "request-" + agent,
              "nonce": "server-receipt", "router_calls": []}
    return [
        {"role": "user", "content": "task " + agent},
        {"role": "assistant", "tool_calls": [call("lite_delegate", "d", {"agent_id": agent})]},
        {"role": "tool", "tool_call_id": "d", "content": json.dumps(marker)},
        {"role": "assistant", "tool_calls": [call("lookup", "repeated", {"q": "same"})]},
        {"role": "tool", "tool_call_id": "repeated", "content": value},
        {"role": "assistant", "content": "Visible answer " + agent},
    ]


class HistoryTest(unittest.TestCase):
    def test_router_sees_all_old_results_as_data_without_skill_payloads_or_capabilities(self):
        messages = json.loads(FIXTURE.read_text())["messages"] + [
            {"role": "user", "content": "intervening general question"},
            {"role": "assistant", "content": "general answer"},
            {"role": "user", "content": "Use the result from two turns ago"},
        ]
        original = copy.deepcopy(messages)
        state = {"source": messages, "user_boundary": len(messages) - 1, "audit": []}
        view = router_projection(state, {"base_helper", "lite_delegate"}, set())
        self.assertIn("AGENT_RESULT_42", str(view))
        self.assertIn("ROUTER_BASE_RESULT", str(view))
        self.assertIn('"agent_id": "agent-a"', str(view))
        self.assertIn('"skill_id": "route-a"', str(view))
        self.assertNotIn("FULL_ROUTING_INSTRUCTIONS", str(view))
        self.assertFalse(any(m.get("tool_calls") or m.get("role") == "tool" for m in view))
        self.assertEqual(messages, original)

    def test_router_preserves_current_own_skill_read(self):
        messages = [{"role": "user", "content": "current"},
                    {"role": "assistant", "tool_calls": [call("view_skill", "s", {"id": "own"})]},
                    {"role": "tool", "tool_call_id": "s", "content": "CURRENT_INSTRUCTIONS"}]
        state = {"source": messages, "user_boundary": 0, "audit": [
            {"id": "s", "name": "view_skill", "arguments": {"id": "own"}, "generation": 1, "executor": {"kind": "router"}},
        ]}
        view = router_projection(state, {"view_skill"}, {"own"})
        self.assertEqual(view[-1], messages[-1])
        self.assertEqual(view[1]["tool_calls"], messages[1]["tool_calls"])
        self.assertEqual(len(view), 3)

    def test_agent_retains_own_results_across_reselection_but_no_foreign_or_old_skills(self):
        first = turn("a", "A_FIRST_RESULT")
        first[-1:-1] = [
            {"role": "assistant", "tool_calls": [call("view_skill", "s", {"id": "work-a"})]},
            {"role": "tool", "tool_call_id": "s", "content": "OLD_A_SKILL_INSTRUCTIONS"},
        ]
        unknown = [{"role": "user", "content": "unknown model task"},
                   {"role": "assistant", "tool_calls": [call("lookup", "unknown", {})]},
                   {"role": "tool", "tool_call_id": "unknown", "content": "UNKNOWN_EXECUTOR_RESULT"}]
        messages = first + turn("b", "B_PRIVATE_RESULT") + unknown + turn("a", "A_SECOND_RESULT")
        boundary = len(messages)
        messages += turn("a", "A_CURRENT_RESULT")
        messages[-1:-1] = [
            {"role": "assistant", "tool_calls": [call("view_skill", "fresh", {"id": "work-a"})]},
            {"role": "tool", "tool_call_id": "fresh", "content": "CURRENT_A_SKILL_INSTRUCTIONS"},
        ]
        original = copy.deepcopy(messages)
        state = {"source": messages, "user_boundary": boundary, "audit": []}
        view = agent_projection(state, "a", {"lookup", "view_skill"}, {"work-a"})
        text = str(view)
        for expected in ("A_FIRST_RESULT", "A_SECOND_RESULT", "A_CURRENT_RESULT", "Visible answer b", "CURRENT_A_SKILL_INSTRUCTIONS"):
            self.assertIn(expected, text)
        for excluded in ("B_PRIVATE_RESULT", "UNKNOWN_EXECUTOR_RESULT", "OLD_A_SKILL_INSTRUCTIONS", "__lite_delegate__"):
            self.assertNotIn(excluded, text)
        detached = agent_projection(state, "a", {"view_skill"}, {"work-a"})
        self.assertNotIn("A_FIRST_RESULT", str(detached))
        self.assertNotIn("A_CURRENT_RESULT", str(detached))
        self.assertEqual(messages, original)

    def test_grouped_generation_ownership_preserves_router_calls_after_delegate(self):
        messages = json.loads(FIXTURE.read_text())["messages"]
        original = copy.deepcopy(messages)
        exchanges = analyze(messages)
        owners = {messages[e.batch]["tool_calls"][e.call_index]["id"]: e.executor for e in exchanges}
        self.assertEqual(owners["base"], {"kind": "router"})
        self.assertEqual(owners["lookup"], {"kind": "agent", "agent_id": "agent-a"})
        self.assertEqual(owners["read"], {"kind": "router"})
        self.assertEqual(messages, original)

    def test_repeated_ids_are_scoped_to_user_request(self):
        messages = turn("a", "A_RESULT") + turn("b", "B_RESULT")
        exchanges = [e for e in analyze(messages) if messages[e.batch]["tool_calls"][e.call_index]["id"] == "repeated"]
        self.assertEqual([e.turn for e in exchanges], [0, 1])
        self.assertEqual([e.executor["agent_id"] for e in exchanges], ["a", "b"])
        self.assertEqual([messages[e.result_index]["content"] for e in exchanges], ["A_RESULT", "B_RESULT"])

    def test_incomplete_contradictory_and_duplicate_calls_do_not_create_pairs(self):
        base = [{"role": "user", "content": "task"}, {"role": "assistant", "tool_calls": [call("lookup", "x", {})]}]
        self.assertEqual(analyze(base), [])
        result = {"role": "tool", "tool_call_id": "x", "content": "one"}
        self.assertEqual(analyze(base + [result, {**result, "content": "two"}]), [])
        duplicate = copy.deepcopy(base)
        duplicate[-1]["tool_calls"].append(call("lookup", "x", {"q": "different"}))
        self.assertEqual(analyze(duplicate + [result]), [])
        unknown = analyze(base + [result])
        self.assertEqual(unknown[0].executor, {"kind": "unknown"})

    def test_canonical_prefix_survives_continuations_and_synthetic_user_image(self):
        original = turn("b", "OLD_RESULT") + [{"role": "user", "content": "new task", "files": [{"id": "file"}]}]
        body = {"model": "router", "messages": copy.deepcopy(original)}
        state = begin(SimpleNamespace(scope={}), body, {}, "u", {}, [], [], [])
        image = {"role": "user", "content": [
            {"type": "text", "text": "Here are the images from the tool results above. Please analyze them."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,test"}},
        ]}
        continuation = [{"role": "system", "content": "OUTER_PROMPT"}, original[-1],
                        {"role": "assistant", "tool_calls": [call("image", "im", {})]},
                        {"role": "tool", "tool_call_id": "im", "content": "image"}, image]
        refresh_source(state, continuation)
        self.assertEqual(state["original_source"], original)
        self.assertIn("OLD_RESULT", str(state["source"]))
        self.assertEqual(state["source"][-1], image)
        self.assertEqual(state["source"][state["user_boundary"]]["files"], [{"id": "file"}])

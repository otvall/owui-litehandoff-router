from copy import deepcopy
import json
from unittest import TestCase

from lite_handoff_router.agent_history_block import AGENT_HISTORY_HEADING, pack_agent_history
from lite_handoff_router.history import agent_projection
from lite_handoff_router.tool_history import ToolResultWindow, result_placeholder
from tests.test_history import turn
from tests.test_protocol import call


class AgentHistoryBlockTest(TestCase):
    def test_packs_own_and_foreign_history_after_window_and_keeps_live_chain(self):
        own = turn("a", "OWN_RESULT")
        foreign = turn("b", "FOREIGN_RESULT")
        image = {"type": "image_url", "image_url": {"url": "attachment"}}
        own[0]["content"] = [{"type": "text", "text": "Inspect this"}, image]
        current = {"role": "user", "content": "Continue"}
        source = own + foreign + [current]
        state = {"source": source, "user_boundary": len(source) - 1}
        snapshot = deepcopy(source)
        window = ToolResultWindow(1)
        messages = agent_projection(state, "a", {"lookup"}, set(), window=window)
        window.apply(messages)
        live = [
            {"role": "assistant", "tool_calls": [call("lookup", "live", {})]},
            {"role": "tool", "tool_call_id": "live", "content": "LIVE_RESULT"},
        ]
        system = {"role": "system", "content": "Agent A"}
        packet = {"role": "user", "content": "Confirmed parallel task results."}
        messages[:] = [system, *messages[:-1], packet, current, *live]
        pack_agent_history(messages, state, current, packet=packet)

        self.assertEqual(messages[:1] + messages[2:], [system, packet, current, *live])
        self.assertEqual(messages[1]["role"], "user")
        text, preserved_image = messages[1]["content"]
        self.assertEqual(preserved_image, image)
        self.assertTrue(text["text"].startswith(AGENT_HISTORY_HEADING))
        block = json.loads(text["text"].split("\n", 1)[1])
        self.assertEqual([turn["agent_id"] for turn in block["turns"]], ["a", "b"])
        self.assertEqual(block["turns"][0]["question"], ["Inspect this", {"media_index": 1}])
        self.assertIn("OWN_RESULT", text["text"])
        self.assertNotIn("FOREIGN_RESULT", text["text"])
        self.assertIn("withheld from other agents", text["text"])
        self.assertEqual(source, snapshot)

    def test_includes_applied_summary_in_the_same_history_block(self):
        old = {"role": "user", "content": "Old"}
        current = {"role": "user", "content": "Current"}
        summary = {"role": "user", "content": "[Shared conversation summary]\nPrevious decisions"}
        messages = [{"role": "system", "content": "Prompt"}, summary, current]
        pack_agent_history(messages, {"source": [old, current], "user_boundary": 1}, current, covered_turns=1)
        self.assertEqual(json.loads(messages[1]["content"].split("\n", 1)[1]),
                         {"summary": "Previous decisions", "turns": []})
        self.assertEqual(messages[2], current)

    def test_hiding_result_does_not_change_tool_status(self):
        source = turn("a", "Error: PRIVATE_ERROR")
        current = {"role": "user", "content": "Current"}
        source.append(current)
        state = {"source": source, "user_boundary": len(source) - 1}
        window = ToolResultWindow(0)
        messages = agent_projection(state, "a", {"lookup"}, set(), window=window)
        window.apply(messages)
        pack_agent_history(messages, state, current)
        calls = json.loads(messages[0]["content"].split("\n", 1)[1])["turns"][0]["events"][0]["calls"]
        self.assertEqual(calls[0]["status"], "error")
        self.assertEqual(calls[0]["result"], result_placeholder("Error:"))

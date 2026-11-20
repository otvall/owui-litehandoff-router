"""Saved-branch terminal evidence with the real OWUI store and output converter."""

import copy
import json
import unittest
from types import SimpleNamespace
from uuid import uuid4

from lite_handoff_router.history import context_handoff_key
from lite_handoff_router.owui_adapter import OWUIAdapter


class NativeHandoffHistoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_saved_branch_status_and_access_without_client_status_grants(self):
        from open_webui.models.chats import Chats, ChatForm
        from open_webui.models.users import Users

        suffix = uuid4().hex
        actor = await Users.insert_new_user(suffix, "History actor", suffix + "@example.test", role="user")
        other = await Users.insert_new_user(suffix + "-other", "Other actor", suffix + "-other@example.test", role="user")
        marker = {"__lite_delegate__": "v2", "agent_id": "agent-a", "request_id": suffix,
                  "nonce": suffix, "router_calls": []}
        output = [
            {"type": "function_call", "id": "fc", "call_id": "d", "name": "lite_delegate",
             "arguments": json.dumps({"agent_id": "agent-a"}), "status": "completed"},
            {"type": "function_call_output", "id": "fo", "call_id": "d", "status": "completed",
             "output": [{"type": "input_text", "text": json.dumps(marker)}]},
        ]
        cid = str(uuid4())
        messages = {
            "u1": {"id": "u1", "parentId": None, "childrenIds": ["a1"], "role": "user", "content": "first"},
            "a1": {"id": "a1", "parentId": "u1", "childrenIds": ["u2"], "role": "assistant",
                   "content": "", "output": output, "done": True, "error": {"content": "failed after handoff"}},
            "u2": {"id": "u2", "parentId": "a1", "childrenIds": [], "role": "user", "content": "next"},
        }
        await Chats.insert_new_chat(cid, actor.id, ChatForm(chat={"history": {"currentId": "u2", "messages": messages}}))
        adapter = OWUIAdapter()
        state = {"chat_id": cid, "message_id": "u2", "user_id": actor.id}
        before = copy.deepcopy(await Chats.get_messages_map_by_chat_id(cid))
        found = await adapter.historical_handoff_statuses(None, {}, state, actor)
        self.assertEqual(found, {context_handoff_key(marker): "failed"})
        self.assertEqual(await adapter.historical_handoff_statuses(None, {}, {**state, "user_id": other.id}, other), {})
        self.assertEqual(await adapter.historical_handoff_statuses(None, {}, {**state, "message_id": "missing"}, actor), {})
        self.assertEqual(await Chats.get_messages_map_by_chat_id(cid), before)
        sample = {"prompt_tokens": 900}
        saved_turn = {**state, "message_id": "a1", "request_id": suffix}
        await adapter.save_prompt_usage(saved_turn, actor, "router-llm", sample["prompt_tokens"])
        await adapter.save_prompt_usage(saved_turn, actor, "model-a", 2000, agent_id="agent-a", generation=3)
        await adapter.save_prompt_usage(saved_turn, actor, "model-a", 2200, agent_id="agent-a", generation=4)
        await adapter.save_prompt_usage(saved_turn, actor, "model-a", 2250, agent_id="agent-a", generation=4)
        await adapter.save_prompt_usage(saved_turn, other, "model-a", 1, agent_id="agent-a", generation=5)
        state["context_policy"] = {"shared_summary_model_id": "summary"}
        self.assertEqual(await adapter.historical_handoff_statuses(None, {}, state, actor), found)
        self.assertEqual(state["historical_router_usage"], {"router-llm": {"model": "router-llm", **sample}})
        self.assertEqual(state["historical_agent_prompt_tokens"], {("agent-a", "model-a"): 2250})
        current = {**state, "message_id": "a1"}
        await adapter.historical_handoff_statuses(None, {}, current, actor)
        self.assertEqual(current["historical_agent_prompt_tokens"], {})
        resume = SimpleNamespace(state=SimpleNamespace(tool_approval_resume=(cid, "a1", "tools")))
        await adapter.historical_handoff_statuses(resume, {}, current, actor)
        self.assertEqual(current["historical_agent_prompt_tokens"], {("agent-a", "model-a"): 2250})
        self.assertEqual(current["historical_router_usage"], {})
        saved = await Chats.get_messages_map_by_chat_id(cid)
        self.assertEqual(saved["a1"]["meta"]["lite_agent_usage"], [
            {"model": "model-a", "agent_id": "agent-a", "request_id": suffix, "generation": generation,
             "prompt_tokens": tokens}
            for generation, tokens in ((3, 2000), (4, 2250))
        ])
        self.assertEqual(saved["a1"]["output"], output)
        self.assertEqual(saved["a1"]["error"], before["a1"]["error"])

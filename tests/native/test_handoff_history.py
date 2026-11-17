"""Saved-branch terminal evidence with the real OWUI store and output converter."""

import copy
import json
import unittest
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

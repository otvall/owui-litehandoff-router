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
        sample = {"prompt_tokens": 900, "summary_key": None}
        saved_turn = {**state, "message_id": "a1", "request_id": suffix}
        await adapter.save_prompt_usage(saved_turn, actor, "router-llm", sample["prompt_tokens"], summary_key=None)
        await adapter.save_prompt_usage(saved_turn, actor, "model-a", 2000, summary_key="prefix", agent_id="agent-a", generation=3)
        await adapter.save_prompt_usage(saved_turn, actor, "model-a", 2200, summary_key="prefix", agent_id="agent-a", generation=4)
        await adapter.save_prompt_usage(saved_turn, actor, "model-a", None, summary_key="prefix", agent_id="agent-a", generation=4)
        await adapter.save_prompt_usage(saved_turn, other, "model-a", 1, summary_key="prefix", agent_id="agent-a", generation=5)
        state["context_policy"] = {"shared_summary_model_id": "summary"}
        self.assertEqual(await adapter.historical_handoff_statuses(None, {}, state, actor), found)
        self.assertEqual(state["historical_router_usage"], {"router-llm": {"model": "router-llm", **sample}})
        self.assertIsNone(state["historical_agent_usage"]["agent-a", "model-a"]["prompt_tokens"])
        current = {**state, "message_id": "a1"}
        await adapter.historical_handoff_statuses(None, {}, current, actor)
        self.assertEqual(current["historical_agent_usage"], {})
        resume = SimpleNamespace(state=SimpleNamespace(tool_approval_resume=(cid, "a1", "tools")))
        await adapter.historical_handoff_statuses(resume, {}, current, actor)
        self.assertEqual(current["historical_agent_usage"], state["historical_agent_usage"])
        self.assertEqual(current["historical_router_usage"], {})
        saved = await Chats.get_messages_map_by_chat_id(cid)
        self.assertEqual(saved["a1"]["meta"]["lite_agent_usage"], [
            {"model": "model-a", "agent_id": "agent-a", "request_id": suffix, "generation": generation,
             "prompt_tokens": tokens, "summary_key": "prefix"}
            for generation, tokens in ((3, 2000), (4, None))
        ])
        self.assertEqual(saved["a1"]["output"], output)
        self.assertEqual(saved["a1"]["error"], before["a1"]["error"])

        summary_state = {**state, "router_id": "router"}
        await adapter.save_shared_summary(summary_state, actor, "summary", "old", "Older summary")
        async with Chats._chat_transaction(cid) as (_, row):
            meta = dict(row.meta)
            meta["lite_shared_summaries"] = [*meta["lite_shared_summaries"], {
                "key": "new", "router": "router", "model": "summary", "summary": "Newer summary",
                "completion_tokens": 9000,
            }]
            row.meta = meta
        self.assertEqual(await adapter.shared_summary_cache(summary_state, actor, "summary"), {
            "old": "Older summary", "new": "Newer summary",
        })

    async def test_shared_summary_save_rejects_an_edited_prefix_and_merges_metadata(self):
        import asyncio
        from open_webui.models.chats import Chats, ChatForm
        from open_webui.models.users import Users

        cid, actor_id = str(uuid4()), str(uuid4())
        user = await Users.insert_new_user(actor_id, "Summary actor", actor_id + "@example.test", role="user")
        output = [{"type": "message", "id": "m", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": "Past answer"}]}]
        messages = {
            "u1": {"id": "u1", "role": "user", "content": "Past", "parentId": None},
            "a1": {"id": "a1", "role": "assistant", "content": "Past answer", "parentId": "u1", "output": output},
            "u2": {"id": "u2", "role": "user", "content": "Current question", "parentId": "a1"},
            "a2": {"id": "a2", "role": "assistant", "content": "", "parentId": "u2", "done": False},
        }
        await Chats.insert_new_chat(cid, user.id, ChatForm(chat={"history": {"currentId": "a2", "messages": messages}}))
        state = {"chat_id": cid, "message_id": "a2", "user_id": user.id, "router_id": "router"}
        adapter = OWUIAdapter()
        original = await adapter.shared_summary_source_version(state, user, 1)
        self.assertIsInstance(original, str)
        await Chats.upsert_message_to_chat_by_id_and_message_id(cid, "u1", {"content": "Edited"})
        self.assertIs(await adapter.save_shared_summary(state, user, "summary", "old", "Stale",
                                                        covered_turns=1, source_version=original), False)
        edited = await adapter.shared_summary_source_version(state, user, 1)
        self.assertNotEqual(original, edited)

        async def update_chat_metadata():
            async with Chats._chat_transaction(cid) as (_, row):
                row.meta = {**(row.meta or {}), "other_feature": {"kept": True}}

        await asyncio.gather(
            update_chat_metadata(),
            adapter.save_shared_summary(state, user, "summary", "new", "Current",
                                        covered_turns=1, source_version=edited),
        )
        self.assertEqual(await adapter.shared_summary_cache(state, user, "summary"), {"new": "Current"})
        self.assertEqual((await Chats.get_chat_by_id(cid)).meta["other_feature"], {"kept": True})
        self.assertEqual((await Chats.get_messages_map_by_chat_id(cid))["a1"]["output"], output)
        self.assertEqual(await adapter.save_shared_summary(state, user, "summary", "new", "Other wording",
                                                          covered_turns=1, source_version=edited), "Current")

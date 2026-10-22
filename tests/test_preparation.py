import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lite_handoff_router.entrypoints import router_preparation

from lite_handoff_router.registry import discover
from lite_handoff_router.runtime import begin, require


class Directory:
    async def user(self, user_id):
        return SimpleNamespace(id=user_id)

    async def owner(self, model):
        return SimpleNamespace(id="owner", model_dump=lambda: {"id": "owner", "role": "user"})

    async def model(self, request, model_id, user):
        return request.app.state.MODELS[model_id]

    async def skill(self, skill_id, user):
        return {"name": "Routing", "description": "Purpose", "id": skill_id}


class PreparationTest(unittest.IsolatedAsyncioTestCase):
    async def test_filter_reads_administrative_valves_each_request(self):
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={
            "router": {"pipe": {"id": "router-pipe"}}, "agent-a": {"id": "agent-a"},
        })))
        instance = router_preparation.Filter()
        with patch.object(router_preparation, "OWUIAdapter", return_value=Directory()), \
             patch.object(router_preparation, "AGENTS", {"agent-a": "route-a"}):
            body = {"model": "router", "messages": [], "base_tool_ids": ["client"]}
            await instance.inlet(body, request, {"id": "u"})
            self.assertEqual(require(request, body["metadata"], "u")["base_tool_ids"], [])
            instance.valves.base_skill_ids = ["new-base"]
            next_body = {"model": "router", "messages": [], "metadata": {"message_id": "next"}}
            await instance.inlet(next_body, request, {"id": "u"})
            self.assertEqual(require(request, next_body["metadata"], "u")["base_skill_ids"], ["new-base"])

    async def test_snapshot_and_new_message_valves(self):
        request = SimpleNamespace(scope={}, app=SimpleNamespace(state=SimpleNamespace(MODELS={
            "router": {"pipe": {"id": "router-pipe"}}, "agent-a": {"id": "agent-a"},
        })))
        registry, _ = await discover(Directory(), request, SimpleNamespace(id="u"), {"agent-a": "route-a"}, "router")
        metadata = {"chat_id": "chat", "message_id": "first"}
        body = {"model": "router", "messages": [{"role": "user", "content": "one"}]}
        state = begin(request, body, metadata, "u", registry, [], ["delegate"], [])
        self.assertEqual(state["registry"]["agent-a"]["skill_id"], "route-a")
        self.assertEqual(require(request, metadata, "u")["base_tool_ids"], ["delegate"])
        old_metadata = copy.deepcopy(metadata)
        state["source"][0]["content"] = "copy"
        self.assertEqual(body["messages"][0]["content"], "one")
        metadata["message_id"] = "second"
        begin(request, body, metadata, "u", registry, [], ["delegate", "new-tool"], ["base"])
        self.assertEqual(require(request, metadata, "u")["base_skill_ids"], ["base"])
        with self.assertRaisesRegex(ValueError, "stale"):
            require(request, old_metadata, "u")
        with self.assertRaisesRegex(ValueError, "stale"):
            require(SimpleNamespace(scope={}), metadata, "u")
        with self.assertRaisesRegex(ValueError, "stale"):
            require(request, metadata, "other-user")

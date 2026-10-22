import copy
from types import SimpleNamespace
import unittest

from lite_handoff_router.model_execution import prepare_model
from lite_handoff_router.runtime import begin, metadata_draft
from tests.test_isolation import IsolatedAdapter
from tests.test_router import User


class SkillContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_scope_replaces_wide_catalog_mentions_and_foreign_versions(self):
        request = SimpleNamespace(scope={}, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={
            "router": {"id": "router", "info": {"params": {"system": "Router"}, "meta": {"skillIds": ["card-extra"]}}},
            "a": {"id": "a", "owned_by": "openai", "info": {"params": {"system": "A"}, "meta": {"toolIds": [], "skillIds": ["work-a"]}}},
            "llm": {"id": "llm", "owned_by": "openai"},
        })))
        source = [{"role": "user", "content": "Task <$foreign|Foreign>"}]
        body, metadata = {"model": "router", "messages": copy.deepcopy(source)}, {}
        registry = {"a": {"agent_id": "a", "skill_id": "route-a", "name": "Route A"}}
        state = begin(request, body, metadata, "u", registry, [], ["delegate"], ["router-guide"])
        body["messages"].insert(0, {"role": "system", "content": "BROAD_FOREIGN_INSTRUCTIONS"})
        metadata.update(skill_ids=["foreign"], chat_context={"mentioned_skill_ids": ["foreign"], "skill_versions": {"foreign": "v0", "router-guide": "old"}})
        adapter = IsolatedAdapter()
        routed, _ = await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {})
        self.assertEqual(routed["metadata"]["lite_skill_scope"]["skill_ids"], ["router-guide", "route-a"])
        self.assertEqual(routed["metadata"]["chat_context"]["skill_versions"], {})
        self.assertNotIn("mentioned_skill_ids", routed["metadata"]["chat_context"])
        self.assertNotIn("BROAD_FOREIGN_INSTRUCTIONS", str(routed["messages"]))
        self.assertNotIn("card-extra", str(routed["messages"]))
        agent, tools = await prepare_model(adapter, request, body, User(), metadata_draft(metadata), state, "llm", {}, target="a")
        self.assertEqual(agent["metadata"]["lite_skill_scope"]["skill_ids"], ["work-a"])
        self.assertIn("work-a", str(agent["messages"]))
        self.assertNotIn("router-guide", str(agent["messages"]))
        self.assertEqual(set(tools), {"view_skill", "read_skill_file", "grep_skill_files"})

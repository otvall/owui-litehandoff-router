import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from lite_handoff_router.skills import manifest


class SkillManifestTest(unittest.IsolatedAsyncioTestCase):
    def entries(self, prompt):
        return json.loads(prompt.split("using skill_id):\n", 1)[1])

    async def test_router_receives_agent_names_separate_from_shared_skill_name(self):
        adapter = SimpleNamespace(skill=AsyncMock(return_value={
            "name": "Routing rules", "description": "Research documents",
        }))
        registry = {
            "a": {"agent_id": "a", "skill_id": "route", "agent_name": "Исследователь"},
            "b": {"agent_id": "b", "skill_id": "route", "agent_name": "Аналитик"},
            "legacy": {"agent_id": "legacy", "skill_id": "route"},
        }
        prompt = await manifest(adapter, ["route"], None, registry)
        entries = self.entries(prompt)
        self.assertEqual([e["agent_name"] for e in entries], ["Исследователь", "Аналитик", "legacy"])
        self.assertTrue(all(e["name"] == "Routing rules" for e in entries))
        self.assertIn("Do not delegate merely to describe capabilities", prompt)
        adapter.skill.assert_awaited_once_with("route", None)

    async def test_empty_router_catalog_explicitly_has_no_agents_even_with_base_skills(self):
        adapter = SimpleNamespace(skill=AsyncMock(return_value={"name": "Base skill"}))
        for skill_ids in ([], ["base"]):
            with self.subTest(skill_ids=skill_ids):
                prompt = await manifest(adapter, skill_ids, None, {})
                self.assertIn("no agents are currently available", prompt)
                self.assertFalse(any("agent_id" in e for e in self.entries(prompt)))

    async def test_agent_context_does_not_receive_router_capability_instructions(self):
        adapter = SimpleNamespace(skill=AsyncMock(return_value={"name": "Work skill"}))
        self.assertEqual(await manifest(adapter, [], None), "")
        prompt = await manifest(adapter, ["work"], None)
        self.assertEqual(self.entries(prompt), [{"skill_id": "work", "name": "Work skill", "description": ""}])
        self.assertNotIn("list all available agents", prompt)

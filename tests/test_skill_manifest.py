import xml.etree.ElementTree as ET
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from lite_handoff_router.skills import manifest, routing_guidance
from lite_handoff_router.parallel import parallel_tool
from lite_handoff_router.protocol import delegate_tool


class SkillManifestTest(unittest.IsolatedAsyncioTestCase):
    async def test_shared_catalog_uses_pinned_descriptions_and_escaped_paths(self):
        adapter = SimpleNamespace(skill=AsyncMock())
        registry = {name: {"agent_id": name, "skill_id": "shared", "name": "Shared routes",
                           "description": "Forecast & review", "path": f"profiles/{name}.md"}
                    for name in ("a", "b&c")}
        prompt = await manifest(adapter, ["shared"], None, registry)
        entries = self.entries(prompt)
        self.assertEqual([entry["id"] for entry in entries], ["shared", "shared"])
        self.assertEqual(list(entries[0]), ["id", "agent_id", "agent_name", "name", "description", "path"])
        self.assertEqual(entries[1]["description"], "Forecast & review")
        self.assertEqual(entries[1]["path"], "profiles/b&c.md")
        self.assertIn("profiles/b&amp;c.md", prompt)
        adapter.skill.assert_not_awaited()
        state = {"registry": registry}
        ordinary = delegate_tool(None, None, None, None, state)
        parallel = parallel_tool(None, None, None, None, state, {})
        self.assertEqual(ordinary["spec"]["parameters"]["properties"]["agent_id"]["enum"], list(registry))
        self.assertEqual(parallel["spec"]["parameters"]["properties"]["agent_id"]["enum"], list(registry))
        guidance = routing_guidance()
        for phrase in ("read_skill_file(id=id, path=path)", "next_offset", "view_skill(id=id)",
                       "If reading fails", "lite_delegate or any delegate_task"):
            self.assertIn(phrase, guidance)

    async def test_router_catalogs_are_ordered_and_routing_ids_are_not_duplicated(self):
        adapter = SimpleNamespace(skill=AsyncMock(return_value={"name": "Skill", "description": ""}))
        prompt = await manifest(adapter, ["base", "route", "chat"], None,
                                {"a": {"agent_id": "a", "skill_id": "route"}})
        root = ET.fromstring("<catalogs>" + prompt[prompt.index("<routing_skills"):] + "</catalogs>")
        self.assertEqual([item.tag for item in root], ["routing_skills", "available_skills"])
        self.assertEqual([e.findtext("id") for e in root.find("available_skills")], ["base", "chat"])
        self.assertTrue(all(not e.attrib for catalog in root for e in catalog))
        empty = await manifest(adapter, [], None, {})
        self.assertIn("<routing_skills />", empty)
        self.assertIn("<available_skills />", empty)

    def entries(self, prompt):
        start = prompt.index("<routing_skills" if "<routing_skills" in prompt else "<available_skills")
        root = ET.fromstring("<catalogs>" + prompt[start:] + "</catalogs>")
        catalog = root.find("routing_skills")
        if catalog is None:
            catalog = root.find("available_skills")
        return [{child.tag: child.text or "" for child in item} for item in catalog]

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
        self.assertIn("Do not delegate merely to describe capabilities", routing_guidance())
        adapter.skill.assert_awaited_once_with("route", None)

    async def test_empty_router_catalog_explicitly_has_no_agents_even_with_base_skills(self):
        adapter = SimpleNamespace(skill=AsyncMock(return_value={"name": "Base skill"}))
        for skill_ids in ([], ["base"]):
            with self.subTest(skill_ids=skill_ids):
                prompt = await manifest(adapter, skill_ids, None, {})
                self.assertIn("no agents are currently available", routing_guidance())
                self.assertFalse(any("agent_id" in e for e in self.entries(prompt)))

    async def test_agent_context_does_not_receive_router_capability_instructions(self):
        adapter = SimpleNamespace(skill=AsyncMock(return_value={"name": "Work skill"}))
        self.assertEqual(await manifest(adapter, [], None), "<available_skills />")
        prompt = await manifest(adapter, ["work"], None)
        self.assertEqual(self.entries(prompt), [{"id": "work", "name": "Work skill", "description": ""}])
        self.assertNotIn("list all available agents", prompt)

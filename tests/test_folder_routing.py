import json
from types import SimpleNamespace
import unittest

from lite_handoff_router.registry import discover_folder
from lite_handoff_router.skills import manifest
from test_registry import RegistryAdapter


class FolderAdapter(RegistryAdapter):
    def __init__(self, routes):
        self.routes = routes

    async def skill(self, skill_id, user):
        return {"name": "Routing folder", "data": {"files": [
            {"path": "routing.json", "content": json.dumps(self.routes)},
            {"path": "profiles/a.md", "content": "Choose for analysis."},
        ]}}


class FolderRoutingTest(unittest.IsolatedAsyncioTestCase):
    def request(self):
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={
            "router": {"id": "router"}, "a": {"id": "a", "name": "Analyst"},
            "denied": {"id": "denied", "denied": True},
        })))

    def profile(self, path="profiles/a.md"):
        return {"path": path, "name": "Analysis profile", "description": "Analyze data"}

    async def test_folder_discovers_permitted_agents_and_exposes_native_file_reader(self):
        adapter = FolderAdapter({"a": self.profile(), "denied": self.profile()})
        entries, diagnostics = await discover_folder(adapter, self.request(), None, "folder", "router")
        self.assertEqual(list(entries), ["a"])
        self.assertEqual(diagnostics[0]["agent_id"], "denied")
        prompt = await manifest(adapter, ["folder"], None, entries)
        self.assertIn('"skill_path": "profiles/a.md"', prompt)
        self.assertIn('"name": "Analysis profile"', prompt)
        self.assertIn('read_skill_file', prompt)
        self.assertEqual(entries["a"]["skill_id"], "folder")

    async def test_missing_and_unsafe_files_fail_before_publishing_catalog(self):
        for path in ("missing.md", "../secret", "/secret", "SKILL.md"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await discover_folder(FolderAdapter({"a": self.profile(path)}), self.request(), None, "folder", "router")

    async def test_index_must_be_a_mapping_with_complete_profiles(self):
        for routes in ([], {"a": {}}, {"a": "profiles/a.md"}):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                await discover_folder(FolderAdapter(routes), self.request(), None, "folder", "router")

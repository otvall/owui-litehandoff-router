from copy import deepcopy
import unittest

from lite_handoff_router.owui_adapter import register_model_tool


class ToolNamesTest(unittest.TestCase):
    def test_long_and_repeated_collisions_preserve_tools(self):
        operation = "x" * 64
        spec = {"name": operation, "parameters": {"type": "object", "properties": {}}}
        baseline = deepcopy(spec)
        registry = {}
        for resource in ("one", "two", "three", "four"):
            tool = {"spec": spec, "tool_id": resource, "callable": lambda r=resource: (r, operation)}
            # Repeated collisions in the same namespace must keep making progress.
            register_model_tool(registry, operation, tool, "server_two")
        self.assertEqual(len(registry), 4)
        self.assertIn(operation, registry)
        for name, tool in registry.items():
            self.assertLessEqual(len(name), 64)
            self.assertRegex(name, r"^[a-zA-Z0-9_-]+$")
            self.assertEqual(tool["spec"]["name"], name)
            self.assertEqual(tool["callable"](), (tool["tool_id"], operation))
        self.assertEqual(spec, baseline)

    def test_existing_alias_collision_keeps_both_resources(self):
        registry = {}
        operation = "x" * 64
        for resource in ("one", "two"):
            register_model_tool(registry, operation, {"spec": {"name": operation}, "tool_id": resource}, "server_two")
        alias = next(name for name in registry if name != operation)
        # A real operation may have exactly the name of an already generated alias.
        register_model_tool(registry, alias, {"spec": {"name": alias}, "tool_id": "three"}, "server_two")
        self.assertEqual(len(registry), 3)
        self.assertEqual({tool["tool_id"] for tool in registry.values()}, {"one", "two", "three"})
        self.assertTrue(all(len(name) <= 64 and tool["spec"]["name"] == name
                            for name, tool in registry.items()))

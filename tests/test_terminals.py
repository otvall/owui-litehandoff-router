import copy
import unittest

from lite_handoff_router.terminals import terminal_binding


class TerminalBindingTest(unittest.TestCase):
    def model(self, settings):
        return {"info": {"meta": settings}}

    def test_absent_null_and_empty_binding_are_disabled(self):
        for meta in ({}, {"capabilities": {"terminal": True}}, {"lite_handoff": {}},
                     {"lite_handoff": {"terminal_id": None}},
                     {"lite_handoff": {"terminal_id": "  "}}):
            with self.subTest(meta=meta):
                self.assertIsNone(terminal_binding(self.model(meta)))

    def test_binding_does_not_mutate_model_or_depend_on_client_fields(self):
        model = self.model({"lite_handoff": {"terminal_id": " terminal-a "}})
        model["terminal_id"] = "client"
        original = copy.deepcopy(model)
        self.assertEqual(terminal_binding(model), "terminal-a")
        self.assertEqual(model, original)

    def test_invalid_types_and_disabled_capability_are_configuration_errors(self):
        for settings in (None, [], "terminal-a", False):
            with self.assertRaisesRegex(ValueError, "object"):
                terminal_binding(self.model({"lite_handoff": settings}))
        for identifier in ([], {}, False, 1):
            with self.assertRaisesRegex(ValueError, "string or null"):
                terminal_binding(self.model({"lite_handoff": {"terminal_id": identifier}}))
        with self.assertRaisesRegex(ValueError, "disabled Terminal"):
            terminal_binding(self.model({"capabilities": {"terminal": False},
                "lite_handoff": {"terminal_id": "terminal-a"}}))

import unittest

from pydantic import ValidationError

from lite_handoff_router.context_policy import ContextPolicy
from lite_handoff_router.entrypoints.router_preparation import Filter


class ContextPolicyTest(unittest.TestCase):
    def test_valves_use_standard_pydantic_conversion_for_all_numbers(self):
        raw = {
            "priority": "-2",
            "max_parallel_tasks": "0",
            "parallel_timeout_seconds": "300",
            "tool_result_history_limit": "0",
        }
        valves = Filter.Valves.model_validate(raw)
        for name, value in raw.items():
            self.assertIs(type(getattr(valves, name)), int)
            self.assertEqual(getattr(valves, name), int(value))

    def test_valves_preserve_null_and_numeric_validation(self):
        self.assertIsNone(Filter.Valves().tool_result_history_limit)
        fields = ("priority", "max_parallel_tasks", "parallel_timeout_seconds", "tool_result_history_limit")
        for field in fields:
            for value in (True, False, "1.5", "", "not-a-number"):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    Filter.Valves(**{field: value})
        with self.assertRaises(ValidationError):
            Filter.Valves(parallel_timeout_seconds="0")

    def test_runtime_history_limit_is_strict(self):
        self.assertIsNone(ContextPolicy().tool_result_history_limit)
        for limit in (None, 0, 2):
            self.assertEqual(ContextPolicy(tool_result_history_limit=limit).tool_result_history_limit, limit)
        for limit in (-1, True, "2", 2.5):
            with self.subTest(limit=limit), self.assertRaises(ValidationError):
                ContextPolicy(tool_result_history_limit=limit)

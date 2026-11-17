import unittest

from pydantic import ValidationError

from lite_handoff_router.context_policy import ContextPolicy
from lite_handoff_router.entrypoints.router_preparation import Filter


class ContextPolicyTest(unittest.TestCase):
    def test_defaults_and_valve_fields(self):
        policy = ContextPolicy()
        self.assertIsNone(policy.tool_result_history_limit)
        self.assertIsNone(policy.usable_budget("a"))
        self.assertFalse(policy.context_summary_enabled)
        self.assertEqual(Filter.Valves().snapshot(), policy.snapshot())
        for limit in (None, 0, 2):
            self.assertEqual(ContextPolicy(tool_result_history_limit=limit).tool_result_history_limit, limit)

    def test_budget_resolution_and_snapshot_isolation(self):
        policy = ContextPolicy(context_input_budget_tokens=12000,
                               context_model_input_budgets={"agent": 6000})
        self.assertEqual(policy.usable_budget("router"), 10976)
        self.assertEqual(policy.usable_budget("agent"), 4976)
        snapshot = policy.snapshot()
        policy.context_model_input_budgets["agent"] = 8000
        self.assertEqual(snapshot["context_model_input_budgets"]["agent"], 6000)
        policy.context_model_input_budgets["agent"] = True
        with self.assertRaises(ValidationError):
            policy.snapshot()

    def test_invalid_settings(self):
        invalid = [
            {"tool_result_history_limit": value} for value in (-1, True, "2", 2.5)
        ] + [
            {"context_input_budget_tokens": 0},
            {"context_input_budget_tokens": 1024},
            {"context_model_input_budgets": {"a": True}},
            {"context_model_input_budgets": {"a": 10}},
            {"context_summary_target_ratio": 0.95},
            {"context_summary_trigger_ratio": 1.0},
            {"context_summary_enabled": True},
            {"context_summary_enabled": True, "context_input_budget_tokens": 12000},
            {"context_summary_enabled": True, "context_summary_model_id": "summary"},
            {"context_summary_input_budget_tokens": 100},
            {"context_summary_max_tokens": 0},
            {"context_summary_timeout_seconds": True},
            {"context_summary_max_chunks": -1},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ContextPolicy(**values)

    def test_summary_override_only_requires_called_model_budget(self):
        policy = ContextPolicy(context_summary_enabled=True, context_summary_model_id="summary",
                               context_model_input_budgets={"a": 6000})
        self.assertEqual(policy.usable_budget("a"), 4976)
        with self.assertRaisesRegex(ValueError, "Missing context input budget"):
            policy.usable_budget("b")

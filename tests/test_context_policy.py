import unittest

from pydantic import ValidationError

from lite_handoff_router.context_policy import ContextPolicy
from lite_handoff_router.entrypoints.router_preparation import Filter


class ContextPolicyTest(unittest.TestCase):
    def test_valves_use_standard_pydantic_conversion_for_all_numbers(self):
        raw = {
            "priority": "-2", "max_parallel_tasks": "0", "parallel_timeout_seconds": "300",
            "tool_result_history_limit": "0", "context_input_budget_tokens": "12000",
            "context_safety_margin_tokens": "0", "context_model_input_budgets": {"a": "6000"},
            "context_summary_trigger_ratio": "0.9", "context_summary_target_ratio": "0.7",
            "context_summary_input_budget_tokens": "8192", "context_summary_max_tokens": "1024",
            "context_summary_timeout_seconds": "60", "context_summary_max_chunks": "8",
        }
        valves = Filter.Valves.model_validate(raw)
        for name in raw:
            if name == "context_model_input_budgets":
                self.assertEqual(valves.context_model_input_budgets, {"a": 6000})
            elif name.endswith("_ratio"):
                self.assertIs(type(getattr(valves, name)), float)
                self.assertEqual(getattr(valves, name), float(raw[name]))
            else:
                self.assertIs(type(getattr(valves, name)), int)
                self.assertEqual(getattr(valves, name), int(raw[name]))
        self.assertEqual(ContextPolicy.model_validate(valves.snapshot()).tool_result_history_limit, 0)
        # Every numeric field remains strict at the internal policy boundary.
        for name, value in raw.items():
            if name in ContextPolicy.model_fields:
                with self.subTest(field=name), self.assertRaises(ValidationError):
                    ContextPolicy.model_validate({**valves.snapshot(), name: value})

    def test_valves_preserve_null_and_numeric_validation(self):
        valves = Filter.Valves(tool_result_history_limit=None, context_input_budget_tokens=None)
        self.assertIsNone(valves.snapshot()["tool_result_history_limit"])
        self.assertIsNone(valves.snapshot()["context_input_budget_tokens"])
        integer_fields = ("priority", "max_parallel_tasks", "parallel_timeout_seconds", "tool_result_history_limit",
                          "context_input_budget_tokens", "context_safety_margin_tokens", "context_summary_input_budget_tokens",
                          "context_summary_max_tokens", "context_summary_timeout_seconds", "context_summary_max_chunks")
        for field in integer_fields:
            for value in (True, False, "1.5", "", "not-a-number"):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    Filter.Valves(**{field: value})
        for field in ("context_summary_trigger_ratio", "context_summary_target_ratio"):
            for value in (True, "nan", "inf", "0", "1", "", "0,7"):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    Filter.Valves(**{field: value})
        for field in ("context_input_budget_tokens", "context_summary_input_budget_tokens", "context_summary_max_tokens",
                      "context_summary_timeout_seconds", "context_summary_max_chunks", "parallel_timeout_seconds"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                Filter.Valves(**{field: "0"})
        with self.assertRaises(ValidationError):
            Filter.Valves(context_safety_margin_tokens="0", context_model_input_budgets={"a": True})
        with self.assertRaises(ValidationError):
            Filter.Valves(context_summary_target_ratio="0.95")
        self.assertIs(Filter.Valves(context_summary_enabled=False).context_summary_enabled, False)

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

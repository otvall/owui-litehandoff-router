"""Exercise the authoring helper as a portable script and in-memory renderer."""

import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "docs/skills/routing-skill-authoring"
SCRIPT = SKILL / "scripts/render_routing_skill.py"
TEMPLATE = (SKILL / "references/routing-skill-template.md").read_text(encoding="utf-8")
render = runpy.run_path(str(SCRIPT))["render"]


def fields():
    return {
        "skill_name": "route-forecast",
        "routing_description": 'Прогноз: "продажи".\nНа основе ряда.',
        "agent_name": "Прогнозист",
        "agent_model_id": "forecast/model-1",
        "routing_criteria": "Нужно рассчитать прогноз по временному ряду.",
        "other_route_criteria": "Объяснение общих терминов без расчёта прогноза вне профиля.",
        "required_inputs": "Временной ряд, горизонт и единицы измерения; горизонт уточнить до запуска.",
        "independent_task_scope": "Один прогноз для одного заданного ряда и горизонта.",
        "final_result_scope": "Сравнение готовых прогнозов одной метрики без повторного расчёта.",
        "expected_result": "Прогноз с допущениями.",
        "positive_examples": ["Рассчитай продажи на декабрь по приложенному ряду."],
        "negative_examples": ["Что такое сезонность?"],
    }


class RoutingSkillRendererTests(unittest.TestCase):
    def test_render_preserves_metadata_and_both_example_sets(self):
        result = render(fields(), TEMPLATE)
        # JSON-quoted scalar values must survive the real YAML parser used by OWUI.
        import yaml
        metadata = yaml.safe_load(result.split("---", 2)[1])
        self.assertEqual(metadata["description"], fields()["routing_description"])
        self.assertEqual(metadata["name"], "route-forecast")
        self.assertEqual(metadata["metadata"], {"agent_id": fields()["agent_model_id"],
                                                "agent_name": fields()["agent_name"]})
        self.assertIn("`forecast/model-1`", result)
        for key in ("positive_examples", "negative_examples"):
            self.assertIn("- " + fields()[key][0], result)
        for key in ("required_inputs", "independent_task_scope", "final_result_scope"):
            self.assertIn(fields()[key], result)
        self.assertNotIn("{{", result)

    def test_agent_metadata_quotes_do_not_change_plain_markdown_identity(self):
        import yaml
        values = {**fields(), "agent_name": 'Агент: "Прогноз" \\ A', "agent_model_id": 'forecast/model:"1"'}
        result = render(values, TEMPLATE)
        metadata = yaml.safe_load(result.split("---", 2)[1])["metadata"]
        self.assertEqual(metadata, {"agent_id": values["agent_model_id"], "agent_name": values["agent_name"]})
        body = result.split("---", 2)[2]
        self.assertIn("# " + values["agent_name"], body)
        self.assertIn("`" + values["agent_model_id"] + "`", body)

    def test_bundled_template_matches_canonical_template(self):
        canonical = ROOT / "docs/templates/routing-skill/SKILL.md"
        self.assertEqual(canonical.read_text(encoding="utf-8"), TEMPLATE)

    def test_rejects_incomplete_or_malformed_inputs(self):
        cases = []
        for key in fields():
            incomplete = fields()
            del incomplete[key]
            cases.append(incomplete)
        for key, value in (
            ("positive_examples", []), ("negative_examples", [""]),
            ("negative_examples", "a string"), ("skill_name", "Invalid Name"),
            ("agent_model_id", "{{AGENT_MODEL_ID}}"), ("agent_name", "Name\n# Heading"),
            ("unknown_field", "typo"),
        ):
            cases.append({**fields(), key: value})
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                render(case, TEMPLATE)

    def test_rejects_incompatible_template(self):
        with self.assertRaises(ValueError):
            render(fields(), TEMPLATE.replace("{{EXPECTED_RESULT}}", "{{OTHER_FIELD}}"))

    def test_cli_uses_bundled_template_and_stdin(self):
        process = subprocess.run(
            [sys.executable, str(SCRIPT)], input=json.dumps(fields()),
            capture_output=True, text=True, cwd="/tmp",
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout, render(fields(), TEMPLATE))

    def test_cli_invalid_fields_produce_no_partial_skill(self):
        process = subprocess.run(
            [sys.executable, str(SCRIPT)], input="{}", capture_output=True, text=True,
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, "")
        self.assertIn("Missing fields:", process.stderr)

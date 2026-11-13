"""Fill the routing Skill template from JSON; Python 3, standard library only."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


TEXT_FIELDS = (
    "skill_name", "routing_description", "agent_name", "agent_model_id",
    "routing_criteria", "other_route_criteria", "expected_result",
    "required_inputs", "independent_task_scope", "final_result_scope",
)
EXAMPLE_FIELDS = ("positive_examples", "negative_examples")
TOKEN = re.compile(r"\{\{([A-Z_]+)\}\}")


def render(fields: dict, template: str) -> str:
    """Return SKILL.md. Both example lists must already be agreed with the author."""
    if not isinstance(fields, dict):
        raise ValueError("Input must be a JSON object")
    required = set(TEXT_FIELDS + EXAMPLE_FIELDS)
    missing, unknown = required - fields.keys(), fields.keys() - required
    if missing:
        raise ValueError("Missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise ValueError("Unknown fields: " + ", ".join(sorted(unknown)))

    values = {}
    for name in TEXT_FIELDS:
        value = fields[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(name + " must be a non-empty string")
        values[name] = value.strip()
    for name in EXAMPLE_FIELDS:
        examples = fields[name]
        if not isinstance(examples, list) or not examples:
            raise ValueError(name + " must be a non-empty array of strings")
        if any(not isinstance(item, str) or not item.strip() for item in examples):
            raise ValueError(name + " must contain non-empty strings")
        values[name] = [item.strip() for item in examples]

    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", values["skill_name"]) or len(values["skill_name"]) > 64:
        raise ValueError("skill_name must be a lowercase hyphenated name, at most 64 characters")
    if len(values["routing_description"]) > 1024:
        raise ValueError("routing_description must be at most 1024 characters")
    for name in ("agent_name", "agent_model_id"):
        if any(char in values[name] for char in "\n\r`"):
            raise ValueError(name + " must be a single line without backticks")
    for value in values.values():
        items = value if isinstance(value, list) else [value]
        if any("{{" in item or "}}" in item for item in items):
            raise ValueError("Replace all template placeholders in input fields")

    expected = Counter({name.upper(): 1 for name in TEXT_FIELDS})
    if Counter(TOKEN.findall(template)) != expected:
        raise ValueError("Template must contain each supported placeholder once")
    if not template.startswith("---\n") or "\n---\n" not in template[4:]:
        raise ValueError("Template must start with YAML frontmatter")
    header, body = template[4:].split("\n---\n", 1)
    # JSON-quoted strings are valid YAML. Quote metadata separately from body text.
    for name in ("SKILL_NAME", "ROUTING_DESCRIPTION"):
        quoted = '"{{' + name + '}}"'
        if header.count(quoted) != 1:
            raise ValueError("Template must quote the " + name + " placeholder")
        header = header.replace(quoted, "{{" + name + "}}")

    replacements = {name.upper(): values[name] for name in TEXT_FIELDS}
    header = TOKEN.sub(lambda match: json.dumps(replacements[match.group(1)], ensure_ascii=False), header)

    def examples_section(title, examples):
        bullets = "\n".join("- " + item.replace("\n", "\n  ") for item in examples)
        return "\n\n### " + title + "\n\n" + bullets

    replacements["ROUTING_CRITERIA"] += examples_section("Позитивные примеры", values["positive_examples"])
    replacements["OTHER_ROUTE_CRITERIA"] += examples_section("Негативные примеры", values["negative_examples"])
    result = "---\n" + header + "\n---\n" + TOKEN.sub(lambda match: replacements[match.group(1)], body)
    if "{{" in result or "}}" in result:
        raise ValueError("Unresolved template placeholders")
    return result.rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="-", help="JSON file, or - for stdin")
    parser.add_argument("--template", type=Path, help="Override the bundled template path")
    args = parser.parse_args()
    try:
        source = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        template_path = args.template or Path(__file__).resolve().parents[1] / "references/routing-skill-template.md"
        result = render(json.loads(source), template_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        parser.exit(2, "Error: " + str(exc) + "\n")
    sys.stdout.write(result)


if __name__ == "__main__":
    main()

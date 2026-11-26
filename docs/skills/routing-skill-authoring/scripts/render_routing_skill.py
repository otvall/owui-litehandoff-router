"""Fill the routing Skill template from JSON; Python 3, standard library only."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


TEXT_FIELDS = (
    "skill_name", "routing_description", "agent_name", "agent_model_id",
    "routing_criteria", "exclusion_criteria", "expected_result",
)
EXAMPLE_FIELDS = ("positive_examples", "negative_examples")
TOKEN = re.compile(r"\{\{([A-Z_]+)\}\}")
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def routing_name_candidates(model_id, suggested_name, number=1):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", model_id.strip()).strip("-").lower()
    if not slug:
        slug = suggested_name.removeprefix("route-") if suggested_name.startswith("route-") else ""
        if not SKILL_NAME.fullmatch(slug):
            raise ValueError("Provide an agreed ASCII fallback as skill_name (route-<slug>)")
    base = "route-" + slug
    while True:
        suffix = "" if number == 1 else f"-{number}"
        yield base[:64 - len(suffix)].rstrip("-") + suffix
        number += 1


def matching_routing_name(model_id, suggested_name, name):
    if not isinstance(name, str) or not name:
        return False
    candidates = routing_name_candidates(model_id, suggested_name)
    if name == next(candidates):
        return True
    suffix = re.search(r"-([1-9][0-9]{0,8})$", name)
    if not suffix or int(suffix[1]) < 2:
        return False
    return name == next(routing_name_candidates(model_id, suggested_name, int(suffix[1])))


def render(fields: dict, template: str, *, resolved_name=None) -> str:
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

    if not SKILL_NAME.fullmatch(values["skill_name"]) or len(values["skill_name"]) > 64:
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

    model_id, suggested = values["agent_model_id"], values["skill_name"]
    name = resolved_name or suggested
    values["skill_name"] = (name if matching_routing_name(model_id, suggested, name)
                            else next(routing_name_candidates(model_id, suggested)))

    # Model ID is a control input for authoring Tools, not part of the profile.
    expected = Counter({name.upper(): 1 for name in TEXT_FIELDS + EXAMPLE_FIELDS if name != "agent_model_id"})
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

    for name in EXAMPLE_FIELDS:
        replacements[name.upper()] = "\n".join("- " + item.replace("\n", "\n  ") for item in values[name])
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

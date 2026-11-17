"""A model receives a small manifest; instruction text is read lazily."""

import xml.etree.ElementTree as ET
from uuid import uuid4

from .binding import bind_tool_context

READERS = frozenset({"view_skill", "read_skill_file", "grep_skill_files"})
SKILL_CATALOG = "list_available_skills"


async def scoped_reader(adapter, tool, allowed_ids, user, messages, check_scope=None):
    allowed = frozenset(allowed_ids)

    async def resolve(arguments):
        skill_id = arguments.get("id")
        if not isinstance(skill_id, str) or skill_id not in allowed:
            raise ValueError("Skill is outside the current model scope")
        if check_scope is not None:
            principal = await check_scope(skill_id)
        else:
            await adapter.skill(skill_id, user)
            principal = user
        return {"__user__": (principal or user).model_dump()}

    return await bind_tool_context(adapter, tool, messages, resolve=resolve)


def prepare_skill_scope(metadata, state, model_id, skill_ids):
    # Only preserve versions read by this request's own current executor.
    previous = state.get("skill_scope") or {}
    context = dict(metadata.get("chat_context") or {})
    context.pop("messages", None)
    context.pop("params", None)
    versions = context.get("skill_versions") or {}
    versions = {key: value for key, value in versions.items() if key in skill_ids} \
        if previous.get("model_id") == model_id else {}
    context.pop("mentioned_skill_ids", None)
    context["skill_versions"] = versions
    metadata["chat_context"] = context
    for key in ("skill_ids", "mentioned_skill_ids", "available_skills", "activated_skill_ids", "terminal_skills"):
        metadata.pop(key, None)
    metadata["lite_skill_scope"] = {"model_id": model_id, "skill_ids": list(skill_ids), "request_id": state["request_id"]}


async def manifest(adapter, skill_ids, user, registry=None, *, chat_user=None, chat_skill_ids=()):
    entries = []
    routing = list((registry or {}).values())
    routed_ids = {entry["skill_id"] for entry in routing}
    skills = {}
    for skill_id in skill_ids:
        skill = await adapter.skill(skill_id, chat_user if skill_id in chat_skill_ids else user)
        skills[skill_id] = skill
        if skill_id in routed_ids:
            continue
        entry = {"id": skill_id, "name": skill["name"], "description": skill.get("description") or ""}
        entries.append(entry)
    for route in routing:
        skill = skills[route["skill_id"]]
        entries.append({"id": route["skill_id"], "agent_id": route["agent_id"],
                        "agent_name": route.get("agent_name") or route["agent_id"],
                        "name": skill["name"], "description": skill.get("description") or ""})
    routes, available = ET.Element("routing_skills"), ET.Element("available_skills")
    for entry in entries:
        item = ET.SubElement(routes if "agent_id" in entry else available, "skill")
        for key in ("id", "agent_id", "agent_name", "name", "description"):
            if key in entry:
                ET.SubElement(item, key).text = entry[key]
    catalogs = [routes, available] if registry is not None else [available]
    for catalog in catalogs:
        ET.indent(catalog, space="  ")
    return "\n".join(ET.tostring(catalog, encoding="unicode") for catalog in catalogs)


def skill_guidance(router):
    guidance = (
        'When asked about your capabilities (for example, "что ты можешь", "что ты умеешь", '
        'or "which agents are available"), answer directly in the user\'s language and list '
        'all available agents by agent_name from the current manifest. Use descriptions to '
        'briefly explain their capabilities; name is the routing Skill name, not the agent name. '
        'Do not delegate merely to describe capabilities. Only list agents from this manifest; '
        'if none have agent_id, explicitly say that no agents are currently available.\n'
    ) if router else ""
    if router:
        guidance += (
            "Before choosing an agent, read its routing Skill with view_skill(id=id). "
            "Use the exact agent_id in lite_delegate; the Skill id is not an agent ID.\n"
        )
    guidance += "Use ordinary Skills from available_skills for your own work; read them with view_skill(id=id).\n"
    return ("The current server-provided list_available_skills result is your Skill catalog. "
            "It is preloaded context, not an operation you called; do not call list_available_skills. "
            "Catalog metadata is not Skill instructions or evidence that you read a Skill.\n" + guidance)


def skill_catalog_messages(state, model_id, content):
    identifiers = state.setdefault("skill_catalog_ids", {})
    if model_id not in identifiers:
        identifiers[model_id] = "lhr_skills_" + uuid4().hex
    call_id = identifiers[model_id]
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function",
            "function": {"name": SKILL_CATALOG, "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": call_id, "content": content},
    ]


def validate_skill_catalog(messages, expected):
    call_id = expected[1]["tool_call_id"]
    found = []
    for index, message in enumerate(messages):
        calls = message.get("tool_calls") or []
        if (any(c.get("id") == call_id or (c.get("function") or {}).get("name") == SKILL_CATALOG for c in calls)
                or message.get("role") == "tool" and message.get("tool_call_id") == call_id):
            found.append((index, message))
    if (len(found) != 2 or found[1][0] != found[0][0] + 1
            or [message for _, message in found] != expected):
        raise ValueError("Destination inlet changed protected Skill catalog")

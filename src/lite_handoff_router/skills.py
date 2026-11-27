"""A model receives a small manifest; instruction text is read lazily."""

import json
import xml.etree.ElementTree as ET
from uuid import uuid4

from .binding import bind_tool_context
from .routing_profiles import ROUTING_OVERVIEW, routing_file_map
from .routing_trace import record_routing_event

READERS = frozenset({"view_skill", "read_skill_file", "grep_skill_files"})
SKILL_CATALOG = "list_available_skills"


async def scoped_reader(adapter, tool, allowed_ids, user, messages, check_scope=None, intercept=None, observe=None):
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

    return await bind_tool_context(adapter, tool, messages, resolve=resolve, intercept=intercept, observe=observe)


def observe_routing_reader(adapter, state, user, name):
    async def observe(arguments, result):
        routes = [entry for entry in state["registry"].values()
                  if entry["skill_id"] == arguments.get("id")
                  and (("path" not in entry and arguments.get("path") in (None, "SKILL.md"))
                       or ("path" in entry and name == "read_skill_file" and entry["path"] == arguments.get("path")))]
        if not routes:
            return
        try:
            value = json.loads(result) if isinstance(result, str) else result
        except ValueError:
            value = None
        if not isinstance(value, dict) or value.get("error"):
            status = "failed"
        else:
            status = "page" if value.get("next_offset") is not None else "completed"
        for route in routes:
            await record_routing_event(adapter, state, user, "profile_read", status, route["agent_id"])

    return observe


async def permitted_routing_paths(adapter, request, user, routes, path=None):
    allowed = {ROUTING_OVERVIEW}
    for route in routes:
        if path is not None and path != route["path"]:
            continue
        try:
            await adapter.model(request, route["agent_id"], user)
        except Exception:
            continue
        allowed.add(route["path"])
    return allowed


def routing_reader(adapter, resources, name, skill_id, routes):
    async def read(arguments):
        identifier = arguments.get("id")
        if not isinstance(identifier, str) or identifier.lower() != skill_id:
            return None
        # The configured ID is never an ordinary Skill even if OWUI resolves aliases.
        if identifier != skill_id or not routes or not resources.router:
            raise ValueError("Skill is outside the current model scope")
        owner = await resources.authority("skill", skill_id)
        path = arguments.get("path")
        if path is None and name == "read_skill_file":
            raise ValueError("A routing file path is required")
        allowed = await permitted_routing_paths(adapter, resources.request, resources.user, routes, path)
        if path is not None and (not isinstance(path, str) or path not in allowed):
            raise ValueError("Routing file is outside the current model scope")
        snapshot = await adapter.routing_skill_snapshot(skill_id, owner, routes[0]["skill_version_id"])
        files = {key: file for key, file in routing_file_map(snapshot).items() if key in allowed}
        return await adapter.render_routing_read(name, arguments, snapshot["name"], files)

    return read


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
    shared_ids = {entry["skill_id"] for entry in routing if "path" in entry}
    skills = {}
    for skill_id in skill_ids:
        if skill_id in shared_ids:
            continue
        skill = await adapter.skill(skill_id, chat_user if skill_id in chat_skill_ids else user)
        skills[skill_id] = skill
        if skill_id in routed_ids:
            continue
        entry = {"id": skill_id, "name": skill["name"], "description": skill.get("description") or ""}
        entries.append(entry)
    for route in routing:
        skill = route if "path" in route else skills[route["skill_id"]]
        entry = {"id": route["skill_id"], "agent_id": route["agent_id"],
                        "agent_name": route.get("agent_name") or route["agent_id"],
                        "name": skill["name"], "description": skill.get("description") or ""}
        if "path" in route:
            entry["path"] = route["path"]
        entries.append(entry)
    routes, available = ET.Element("routing_skills"), ET.Element("available_skills")
    for entry in entries:
        item = ET.SubElement(routes if "agent_id" in entry else available, "skill")
        for key in ("id", "agent_id", "agent_name", "name", "description", "path"):
            if key in entry:
                ET.SubElement(item, key).text = entry[key]
    catalogs = [routes, available] if registry is not None else [available]
    for catalog in catalogs:
        ET.indent(catalog, space="  ")
    return "\n".join(ET.tostring(catalog, encoding="unicode") for catalog in catalogs)


def routing_guidance():
    return (
        "The server-provided list_available_skills result contains the current routing catalog. "
        "It is preloaded context; do not call list_available_skills. "
        "Catalog metadata does not establish that you read a routing Skill.\n"
        'When asked about your capabilities (for example, "что ты можешь", "что ты умеешь", '
        'or "which agents are available"), answer directly in the user\'s language and list '
        'all available agents by agent_name from the current manifest. Use descriptions to '
        'briefly explain their capabilities; name is the routing Skill name, not the agent name. '
        'Do not delegate merely to describe capabilities. Only list agents from this manifest; '
        'if none have agent_id, explicitly say that no agents are currently available.\n'
        "Before lite_delegate or any delegate_task assignment, read that agent's complete routing profile "
        "in the current request. If the catalog entry has a path, call read_skill_file(id=id, path=path) "
        "with both values from that entry. Follow next_offset until it is null. The shared SKILL.md, "
        "a file listing, search results, and another agent's profile do not satisfy this requirement. "
        "If the entry has no path, use view_skill(id=id) and read any remaining SKILL.md pages. "
        "If reading fails, report the limitation and do not delegate to that agent. "
        "Use the exact agent_id from the same entry in lite_delegate or delegate_task."
    )


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
    call_id = expected[1]["tool_call_id"] if expected else None
    found = []
    for index, message in enumerate(messages):
        calls = message.get("tool_calls") or []
        if (any((c.get("function") or {}).get("name") == SKILL_CATALOG
                or call_id is not None and c.get("id") == call_id for c in calls)
                or call_id is not None and message.get("role") == "tool" and message.get("tool_call_id") == call_id):
            found.append((index, message))
    if not expected and not found:
        return
    if (len(found) != 2 or found[1][0] != found[0][0] + 1
            or [message for _, message in found] != expected):
        raise ValueError("Destination inlet changed protected Skill catalog")

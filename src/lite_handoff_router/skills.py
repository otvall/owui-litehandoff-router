"""A model receives a small manifest; instruction text is read lazily."""

import json

from .binding import bind_tool_context

READERS = frozenset({"view_skill", "read_skill_file", "grep_skill_files"})


async def scoped_reader(adapter, tool, allowed_ids, user, messages, check_scope=None):
    allowed = frozenset(allowed_ids)

    async def check(arguments):
        skill_id = arguments.get("id")
        if not isinstance(skill_id, str) or skill_id not in allowed:
            raise ValueError("Skill is outside the current model scope")
        if check_scope is not None:
            await check_scope(skill_id)
        else:
            await adapter.skill(skill_id, user)

    return await bind_tool_context(adapter, tool, messages, check=check,
                                   pinned={"__user__": user.model_dump()})


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


async def manifest(adapter, skill_ids, user, registry=None):
    entries = []
    routing = list((registry or {}).values())
    routed_ids = {entry["skill_id"] for entry in routing}
    skills = {}
    for skill_id in skill_ids:
        skill = await adapter.skill(skill_id, user)
        skills[skill_id] = skill
        if skill_id in routed_ids:
            continue
        entry = {"skill_id": skill_id, "name": skill["name"], "description": skill.get("description") or ""}
        entries.append(entry)
    for route in routing:
        skill = skills[route["skill_id"]]
        entries.append({"skill_id": route["skill_id"], "agent_id": route["agent_id"],
                        "agent_name": route.get("agent_name") or route["agent_id"],
                        "name": skill["name"], "description": skill.get("description") or ""})
    guidance = (
        'When asked about your capabilities (for example, "что ты можешь", "что ты умеешь", '
        'or "which agents are available"), answer directly in the user\'s language and list '
        'all available agents by agent_name from the current manifest. Use descriptions to '
        'briefly explain their capabilities; name is the routing Skill name, not the agent name. '
        'Do not delegate merely to describe capabilities. Only list agents from this manifest; '
        'if none have agent_id, explicitly say that no agents are currently available.\n'
    ) if registry is not None else ""
    if not entries and registry is None:
        return ""
    return guidance + (
        "Available skills (read instructions with view_skill using skill_id):\n"
        + json.dumps(entries, ensure_ascii=False)
    )

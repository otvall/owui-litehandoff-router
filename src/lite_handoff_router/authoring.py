"""Tools for a standalone OWUI routing Skill author; every call uses the chat user."""

import json
from pathlib import Path

from .routing_skill_renderer import render


def authoring_template():
    # The standalone builder embeds the canonical template in this function.
    return (Path(__file__).resolve().parents[2] / "docs/templates/routing-skill/SKILL.md").read_text(encoding="utf-8")


async def authoring_user(injected):
    from open_webui.models.users import Users

    user = await Users.get_user_by_id((injected or {}).get("id"))
    if user is None:
        raise ValueError("Authenticated user is required")
    return user


async def writable_agent(model_id, user):
    from open_webui.routers.models import get_model_by_id

    model = await get_model_by_id(model_id, user=user, db=None)
    if not model.write_access:
        raise ValueError("Write access to the agent model is required")
    return model


def authoring_page(content, offset, max_chars):
    if offset < 0 or not 1 <= max_chars <= 20000:
        raise ValueError("offset must be non-negative; max_chars must be between 1 and 20000")
    end = offset + max_chars
    return {"content": content[offset:end], "next_offset": end if end < len(content) else None}


def authoring_json(value):
    return json.dumps(value, ensure_ascii=False)


async def authoring_tool_specs(identifier, user):
    """Inspect stored local schemas without importing or running another Tool's code."""
    from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
    from open_webui.env import ENABLE_TOOLS
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.tools import Tools as ToolRows

    if identifier.startswith("server:"):
        return {"id": identifier, "status": "not_inspected",
                "reason": "External server schemas require separate discovery; capabilities are unverified."}
    if not ENABLE_TOOLS:
        return {"id": identifier, "status": "unavailable", "reason": "Local Tools are disabled"}
    row = await ToolRows.get_tool_by_id(identifier)
    if row is None or not (
        row.user_id == user.id or (user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL)
        or await AccessGrants.has_access(user_id=user.id, resource_type="tool",
                                        resource_id=identifier, permission="read")
    ):
        return {"id": identifier, "status": "unavailable", "reason": "Tool missing or not readable by the chat user"}
    return {"id": identifier, "name": row.name, "status": "stored_schemas",
            "specs": row.specs}


class Tools:
    async def list_agent_models(self, query: str = "", page: int = 1, __user__: dict = None) -> str:
        """Find Workspace Models writable by the current user. Follow next_page even if items is empty.

        :param query: Agent name or model ID search text; empty lists accessible models.
        :param page: Source catalog page, starting at 1.
        """
        from open_webui.routers.models import get_models, PAGE_ITEM_COUNT

        user = await authoring_user(__user__)
        if page < 1:
            raise ValueError("page must be positive")
        result = await get_models(query=query or None, page=page, user=user, db=None)
        return authoring_json({
            "items": [{"id": m.id, "name": m.name, "is_active": m.is_active}
                      for m in result.items if m.write_access],
            "next_page": page + 1 if page * PAGE_ITEM_COUNT < result.total else None,
        })

    async def inspect_agent_model(self, model_id: str, __user__: dict = None) -> str:
        """Read a writable agent's prompt, resource IDs and stored local Tool schemas, without executing Tools.

        :param model_id: Exact Workspace Model ID from list_agent_models.
        """
        from open_webui.routers.skills import authorized_skill

        user = await authoring_user(__user__)
        model = await writable_agent(model_id, user)
        data = model.model_dump()
        meta, params = data.get("meta") or {}, data.get("params") or {}
        skills = []
        for identifier in meta.get("skillIds") or []:
            try:
                skill = await authorized_skill(identifier, user)
                skills.append({"id": identifier, "name": skill.name, "description": skill.description,
                               "is_active": skill.is_active, "version_id": skill.version_id,
                               "files": [{"path": f["path"], "chars": len(f.get("content", "")),
                                          "encoding": f.get("encoding")}
                                         for f in skill.data["files"]]})
            except Exception as error:
                from fastapi import HTTPException
                if not isinstance(error, HTTPException) or error.status_code not in (401, 403, 404):
                    raise
                skills.append({"id": identifier, "status": "unavailable", "reason": "Skill missing or not readable"})
        tool_ids = meta.get("toolIds") or []
        result = {
            "id": model.id, "name": model.name, "base_model_id": model.base_model_id,
            "is_active": model.is_active, "system_prompt": params.get("system", ""),
            "capabilities": meta.get("capabilities") or {},
            "builtin_tools": meta.get("builtinTools") or {},
            "tool_ids": tool_ids, "skills": skills,
            "tools": [await authoring_tool_specs(identifier, user) for identifier in tool_ids],
            "terminal_configured": bool((meta.get("lite_handoff") or {}).get("terminal_id")),
            "limitations": [
                "These are stored model settings, not proof of working external services or effective runtime tools.",
                "Dynamic Filters, inherited base prompts, knowledge contents and external server schemas are not inspected.",
                "Resource access uses the chat user; model write access does not grant access to private Skills or Tools.",
            ],
        }
        return authoring_json(result)

    async def read_agent_skill_file(self, model_id: str, skill_id: str, path: str = "SKILL.md",
                                    offset: int = 0, max_chars: int = 12000, __user__: dict = None) -> str:
        """Read a page of a Skill attached to the inspected writable agent, with the chat user's Skill read permission.

        :param model_id: Agent Workspace Model ID.
        :param skill_id: Attached Skill ID from inspect_agent_model.
        :param path: Exact virtual file path from the Skill file catalog.
        :param offset: Character offset, initially 0; follow next_offset.
        :param max_chars: Page size, 1 through 20000.
        """
        from open_webui.routers.skills import authorized_skill

        user = await authoring_user(__user__)
        model = await writable_agent(model_id, user)
        if skill_id not in (model.model_dump().get("meta") or {}).get("skillIds", []):
            raise ValueError("Skill is not attached to this agent")
        skill = await authorized_skill(skill_id, user)
        file = next((f for f in skill.data["files"] if f["path"] == path), None)
        if file is None or file.get("encoding"):
            raise ValueError("Text Skill file is unavailable")
        return authoring_json({"skill_id": skill_id, "version_id": skill.version_id, "path": path,
                               **authoring_page(file["content"], offset, max_chars)})

    async def inspect_routing_skill(self, skill_id: str, offset: int = 0, max_chars: int = 12000,
                                    __user__: dict = None) -> str:
        """Read an existing writable routing Skill before updating; returns the version required for saving.

        :param skill_id: Existing routing Skill ID supplied by the author.
        :param offset: Character offset, initially 0; follow next_offset.
        :param max_chars: Page size, 1 through 20000.
        """
        from open_webui.routers.skills import authorized_skill

        user = await authoring_user(__user__)
        skill = await authorized_skill(skill_id, user, permission="write")
        return authoring_json({"id": skill.id, "name": skill.name, "description": skill.description,
                               "is_active": skill.is_active, "version_id": skill.version_id,
                               **authoring_page(skill.content, offset, max_chars)})

    async def render_routing_skill(self, fields: dict, __user__: dict = None) -> str:
        """Validate agreed profile fields and render a routing SKILL.md; does not save it.

        :param fields: Object with skill_name, routing_description, agent_name, agent_model_id, routing_criteria,
            other_route_criteria, required_inputs, independent_task_scope, final_result_scope, expected_result
            (all strings), and positive_examples and negative_examples (nonempty arrays of agreed strings).
        """
        user = await authoring_user(__user__)
        content = render(fields, authoring_template())
        model = await writable_agent(fields["agent_model_id"].strip(), user)
        return authoring_json({"agent_id": model.id, "name": fields["skill_name"].strip(),
                               "description": fields["routing_description"].strip(), "content": content})

    async def save_routing_skill(self, fields: dict, skill_id: str, expected_version_id: str = "",
                                 __user__: dict = None, __request__=None) -> str:
        """Render and save the agreed routing Skill. Empty expected_version_id creates only; a version updates only.

        :param fields: Same agreed field object accepted by render_routing_skill; content is rendered server-side.
        :param skill_id: Exact OWUI Skill ID to create or update; lowercase letters, digits, underscore or hyphen.
        :param expected_version_id: Empty for creation. For updates use the version from inspect_routing_skill;
            a stale version is rejected, preserving concurrent edits. Never retry a conflict without rereading.
        """
        from open_webui.models.skills import SkillForm
        from open_webui.routers.skills import authorized_skill, create_new_skill, update_skill_by_id

        user = await authoring_user(__user__)
        content = render(fields, authoring_template())
        model = await writable_agent(fields["agent_model_id"].strip(), user)
        values = {"id": skill_id, "name": fields["skill_name"].strip(),
                  "description": fields["routing_description"].strip(), "content": content}
        if expected_version_id:
            await authorized_skill(skill_id, user, permission="write")
            values["expected_version_id"] = expected_version_id
            saved = await update_skill_by_id(__request__, skill_id, SkillForm(**values), user=user, db=None)
        else:
            saved = await create_new_skill(__request__, SkillForm(**values), user=user, db=None)
        if saved is None:
            raise ValueError("OWUI did not return a saved Skill")
        return authoring_json({
            "id": saved.id, "name": saved.name, "version_id": saved.version_id,
            "is_active": saved.is_active, "agent_id": model.id,
            "workspace_url": "/workspace/skills", "registry_updated": False,
            "router_connection": "not_checked",
            "registry_entry": {model.id: saved.id},
            "next_step": "Ensure Router owner's read access and add this entry to Router Preparation AGENTS, preserving existing entries.",
        })

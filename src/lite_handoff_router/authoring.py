"""Tools for a standalone OWUI routing Skill author; every call uses the chat user."""

import asyncio
from difflib import unified_diff
import json
from pathlib import Path

from .routing_skill_renderer import matching_routing_name, render, routing_name_candidates
from .routing_profiles import ROUTING_INDEX, routing_entries, routing_file_map, routing_index, routing_profile_path
from .routing_profiles import unique_routing_keys
from .routing_trace import trusted_routing_trace


def authoring_template():
    # The standalone builder embeds the canonical template in this function.
    return (Path(__file__).resolve().parents[2] / "docs/templates/routing-skill/SKILL.md").read_text(encoding="utf-8")


async def authoring_user(injected):
    from open_webui.models.users import Users

    user = await Users.get_user_by_id((injected or {}).get("id"))
    if user is None:
        raise ValueError("Authenticated user is required")
    return user


async def routing_administrator(injected):
    from fastapi import HTTPException

    user = await authoring_user(injected)
    if user.role != "admin":
        raise HTTPException(403, "Router diagnostics require an administrator")
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


def shared_routing_name(model_id, suggested_name, files, index, current_path):
    from open_webui.utils.skill_files import frontmatter

    paths = {entry["path"] for agent_id, entry in index.items()
             if agent_id != model_id and isinstance(entry, dict) and isinstance(entry.get("path"), str)}
    occupied = {path for path in files if path != current_path} | paths
    names = [frontmatter(files[path]["content"]).get("name") for path in paths & files.keys()
             if not files[path].get("encoding")]
    previous = frontmatter(files.get(current_path, {}).get("content", "")).get("name")
    if (matching_routing_name(model_id, suggested_name, previous) and previous not in names
            and "profiles/" + previous + ".md" not in occupied):
        return previous
    return next(candidate for candidate in routing_name_candidates(model_id, suggested_name)
                if candidate not in names and "profiles/" + candidate + ".md" not in occupied)


async def writable_routing_index(skill_id, user):
    from open_webui.routers.skills import authorized_skill

    skill = await authorized_skill(skill_id, user, permission="write")
    files = routing_file_map({"data": skill.data})
    return skill, files, routing_index(files)


async def readable_routing_catalog(skill_id, expected_version_id, user):
    from fastapi import HTTPException
    from open_webui.routers.skills import authorized_skill

    skill = await authorized_skill(skill_id, user, permission="read")
    if expected_version_id and expected_version_id != skill.version_id:
        raise HTTPException(409, {"code": "version_conflict", "current_version_id": skill.version_id})
    if not skill.version_id:
        raise ValueError("Shared routing Skill has no saved version")
    snapshot = {"data": skill.data}
    entries, invalid = routing_entries(snapshot)
    return skill, snapshot, entries, invalid


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


async def prepare_standalone_routing_change(fields, skill_id, expected_version_id, user):
    from fastapi import HTTPException
    from open_webui.models.skills import Skills
    from open_webui.routers.skills import authorized_skill

    render(fields, authoring_template())
    model = await writable_agent(fields["agent_model_id"].strip(), user)
    previous = await authorized_skill(skill_id, user, permission="write") if expected_version_id else None
    suggested = fields["skill_name"].strip()
    names = routing_name_candidates(model.id, suggested)
    if previous and matching_routing_name(model.id, suggested, previous.name):
        from itertools import chain
        names = chain((previous.name,), names)
    for name in names:
        by_name = await Skills.get_skill_by_name(name)
        by_id = await Skills.get_skill_by_id(name)
        if all(item is None or (previous and item.id == previous.id) for item in (by_name, by_id)):
            break
    if not previous and skill_id != name:
        raise HTTPException(409, {"code": "routing_skill_name_mismatch", "available_skill_id": name})
    content = render(fields, authoring_template(), resolved_name=name)
    values = {"id": skill_id, "name": name,
              "description": fields["routing_description"].strip(), "content": content}
    return model, previous, values


async def prepare_shared_routing_change(fields, skill_id, expected_version_id, user):
    from fastapi import HTTPException

    render(fields, authoring_template())
    model = await writable_agent(fields["agent_model_id"].strip(), user)
    skill, files, index = await writable_routing_index(skill_id, user)
    if not expected_version_id:
        raise ValueError("Read the shared routing Skill to obtain the expected version")
    if expected_version_id != skill.version_id:
        raise HTTPException(409, {"code": "version_conflict", "current_version_id": skill.version_id})
    existing = index.get(model.id)
    current_path = routing_profile_path(existing["path"]) if existing is not None else None
    name = shared_routing_name(model.id, fields["skill_name"].strip(), files, index, current_path)
    path = current_path or "profiles/" + name + ".md"
    content = render(fields, authoring_template(), resolved_name=name)
    if (existing is None and path in files or any(
        agent != model.id and isinstance(entry, dict) and entry.get("path") == path
        for agent, entry in index.items()
    )):
        raise ValueError("Routing profile path is already in use")
    entry = {"path": path, "description": fields["routing_description"].strip()}
    operations = [
        {"op": "put", "path": path, "content": content},
        {"op": "put", "path": ROUTING_INDEX, "content": json.dumps({**index, model.id: entry}, ensure_ascii=False, indent=2) + "\n"},
    ]
    return skill, model.id, name, path, files.get(path, {}).get("content", ""), existing, entry, operations


def routing_diff(before, after, filename):
    return "".join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                fromfile="before/" + filename, tofile="after/" + filename))


class Tools:
    async def inspect_router_catalog(self, router_id: str, agent_id: str = "",
                                     __user__: dict = None, __request__=None) -> str:
        """Inspect an installed Router's effective catalog without starting a chat.

        :param router_id: Exact Router Workspace Model ID.
        :param agent_id: Optional exact expected Model ID to diagnose if missing.
        """
        from open_webui.utils.filter import get_filter_context, get_filter_functions, get_function_module
        from starlette.requests import Request

        user = await routing_administrator(__user__)
        router = __request__.app.state.MODELS.get(router_id)
        if not router or not router.get("pipe"):
            raise ValueError("Router Workspace Model is unavailable")
        request = Request({"type": "http", "method": "POST", "path": __request__.url.path,
                           "app": __request__.app, "headers": [], "state": {}})
        functions = await get_filter_functions(request, router)
        preparations = []
        for function in functions:
            module = await get_function_module(request, function.id, function=function)
            if callable(getattr(module, "inspect_routing_catalog", None)):
                preparations.append((function.id, module))
        if len(preparations) != 1:
            raise ValueError("Router must have one compatible Router Preparation filter")
        filter_id, preparation = preparations[0]
        valves = await get_filter_context(request).get_function_valves(
            [filter_id], filter_id, preparation.Valves)
        entries, errors, source = await preparation.inspect_routing_catalog(request, user, router_id, valves)
        result = {"router_id": router_id, "source": source, "entries": list(entries.values()), "diagnostics": errors}
        if agent_id:
            result["requested_agent"] = {"agent_id": agent_id,
                                         "status": "available" if agent_id in entries else "unavailable",
                                         "diagnostics": [item for item in errors
                                                         if item["agent_id"] in {agent_id, None}]}
        return authoring_json(result)

    async def inspect_routing_turn(self, chat_id: str, message_id: str, __user__: dict = None) -> str:
        """Read evidence for an assistant turn in an accessible chat.

        :param chat_id: Exact chat ID.
        :param message_id: Exact assistant message ID inside that chat.
        """
        from fastapi import HTTPException
        from open_webui.env import WEBUI_SECRET_KEY
        from open_webui.models.chats import Chats

        user = await routing_administrator(__user__)
        chat = await Chats.get_accessible_chat_by_id(chat_id, user)
        if chat is None:
            raise HTTPException(404, "Chat or message is unavailable")
        message = await Chats.get_message_by_id_and_message_id(chat.id, message_id)
        if not message or message.get("role") != "assistant":
            raise HTTPException(404, "Chat or message is unavailable")
        trace = trusted_routing_trace(message, chat.id, message_id, WEBUI_SECRET_KEY)
        if trace is not None:
            return authoring_json({"evidence": "trace", **trace})
        output = [item for item in message.get("output") or [] if isinstance(item, dict)]
        results = {item.get("call_id") for item in output if item.get("type") == "function_call_output"
                   and item.get("status") == "completed" and isinstance(item.get("call_id"), str)}
        return authoring_json({
            "evidence": "saved_history",
            "events": [{"tool": item["name"], "result_recorded": item.get("call_id") in results}
                       for item in output if item.get("type") == "function_call"
                       and item.get("name") in ("view_skill", "read_skill_file", "lite_delegate", "delegate_task")
                       and isinstance(item.get("call_id"), str)],
            "missing": ["historical_catalog", "profile_version", "verified_execution_outcome"],
        })

    async def resolve_routing_test_group(self, diagnostics_skill_id: str, group_id: str,
                                         routing_skill_id: str, expected_version_id: str,
                                         __user__: dict = None) -> str:
        """Resolve a diagnostic group against one reviewed routing catalog version.

        :param diagnostics_skill_id: Exact ID of the attached routing-diagnostics Skill.
        :param group_id: Group ID from references/test-groups.json.
        :param routing_skill_id: Exact ID of the shared routing Skill.
        :param expected_version_id: Version from list_shared_routing_catalog.
        """
        from open_webui.routers.skills import authorized_skill

        user = await routing_administrator(__user__)
        if not expected_version_id:
            raise ValueError("A reviewed routing catalog version is required")
        diagnostic = await authorized_skill(diagnostics_skill_id, user, permission="read")
        file = routing_file_map({"data": diagnostic.data}).get("references/test-groups.json")
        if not file or file.get("encoding"):
            raise ValueError("Diagnostic Skill must contain a text references/test-groups.json")
        groups = json.loads(file["content"], object_pairs_hook=unique_routing_keys)
        group = groups.get(group_id) if isinstance(groups, dict) else None
        if not isinstance(group, dict):
            raise ValueError("Unknown routing test group")
        description, agents = group.get("description"), group.get("agents")
        if (not isinstance(description, str) or not description.strip() or not isinstance(agents, list)
                or not agents or any(not isinstance(agent, str) or not agent or agent != agent.strip()
                                     for agent in agents) or len(set(agents)) != len(agents)):
            raise ValueError("A group requires a description and unique exact agent IDs")
        skill, _, entries, invalid = await readable_routing_catalog(routing_skill_id, expected_version_id, user)
        missing = {agent: invalid.get(agent, "Not registered") for agent in agents if agent not in entries}
        return authoring_json({
            "group_id": group_id, "description": description, "agents": agents,
            "diagnostics_skill_version_id": diagnostic.version_id, "routing_skill_id": skill.id,
            "version_id": skill.version_id, "invalid_agents": missing,
            "status": "blocked" if missing else "ready",
        })

    async def export_routing_test_job(self, diagnostics_skill_id: str, group_id: str, routing_skill_id: str,
                                      expected_version_id: str, cases: list[dict], __user__: dict = None) -> str:
        """Export a selected group's routing cases for the isolated test runner.

        :param diagnostics_skill_id: Exact ID of the routing-diagnostics Skill.
        :param group_id: Selected group ID.
        :param routing_skill_id: Exact ID of the shared routing Skill.
        :param expected_version_id: Reviewed catalog version.
        :param cases: Test cases with a unique id, actor role, entrypoint, turns and expected decisions.
        """
        group = json.loads(await self.resolve_routing_test_group(
            diagnostics_skill_id, group_id, routing_skill_id, expected_version_id, __user__=__user__))
        if group["status"] != "ready":
            return authoring_json(group)
        if not cases:
            raise ValueError("Supply at least one case")
        case_ids = set()
        for case in cases:
            case_id = case.get("id")
            if not isinstance(case_id, str) or not case_id.strip() or case_id in case_ids or not case.get("expected"):
                raise ValueError("Each case needs a unique ID and an expected decision")
            case_ids.add(case_id)
        return authoring_json({
            "schema_version": 1, "group_id": group["group_id"], "agents": group["agents"],
            "routing_skill_id": group["routing_skill_id"], "version_id": group["version_id"],
            "diagnostics_skill_version_id": group["diagnostics_skill_version_id"], "cases": cases,
        })

    async def ask_agent(self, model_id: str, question: str, __user__: dict = None,
                        __model__: dict = None, __request__=None) -> str:
        """Ask a Workspace Model one question about its scope without executing its tools.

        :param model_id: Exact Workspace Model ID.
        :param question: One question about the agent's capabilities, constraints or output.
        """
        from fastapi import HTTPException
        from open_webui.utils.chat import generate_chat_completion
        from open_webui.utils.models import check_model_access
        from starlette.requests import Request
        from starlette.responses import Response, StreamingResponse

        question = question.strip()
        if not 1 <= len(question) <= 4000:
            raise ValueError("Question must contain 1 through 4000 characters")
        user = await authoring_user(__user__)
        if __request__ is None or not (__model__ or {}).get("id"):
            raise ValueError("Consultation requires a Workspace Model chat")
        if model_id == __model__["id"]:
            raise ValueError("The authoring model cannot consult itself")
        model = await writable_agent(model_id, user)
        params = model.params.model_dump()
        custom = params.get("custom_params") or {}
        isolated_fields = {"tools", "tool_choice", "functions", "function_call", "tool_ids",
                           "files", "metadata", "chat_id", "input", "previous_response_id",
                           "conversation", "background"}
        if not isinstance(custom, dict) or isolated_fields.intersection(params.keys() | custom.keys()):
            raise ValueError("Model parameters are incompatible with isolated consultation")
        models = __request__.app.state.MODELS
        runtime = models.get(model.id)
        base = models.get(model.base_model_id) if model.base_model_id else runtime
        if (not model.is_active or not runtime or not base
                or any(item.get("pipe") or item.get("pipeline") or item.get("arena")
                       or item.get("owned_by") == "arena" for item in (runtime, base))
                or base.get("owned_by") not in {"openai", "ollama"}):
            raise ValueError("Model is unavailable for isolated consultation")
        try:
            await check_model_access(user, runtime)
        except Exception:
            raise HTTPException(403, "Model execution access is required") from None

        # Bypass the outer chat/tool loop while retaining native model dispatch and prompt handling.
        request = Request({"type": "http", "method": "POST", "path": __request__.url.path,
                           "app": __request__.app, "headers": [], "state": {}})
        body = {"model": model.id, "messages": [{"role": "user", "content": question}],
                "stream": False, "n": 1, "max_tokens": 1024}
        try:
            response = await asyncio.wait_for(generate_chat_completion(request, body, user), timeout=30)
            if isinstance(response, StreamingResponse):
                result = {"status": "invalid_response"}
            else:
                if isinstance(response, Response):
                    if response.status_code >= 400:
                        return authoring_json({"agent_id": model.id, "status": "generation_failed"})
                    response = json.loads(response.body)
                message = response["choices"][0]["message"]
                answer = message.get("content")
                result = ({"status": "ok", "answer": answer[:8000], "truncated": len(answer) > 8000}
                          if isinstance(answer, str) and answer.strip() and not message.get("tool_calls")
                          else {"status": "invalid_response"})
        except TimeoutError:
            result = {"status": "timeout"}
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            result = {"status": "invalid_response"}
        except Exception:
            result = {"status": "generation_failed"}
        return authoring_json({"agent_id": model.id, **result})

    async def list_agent_models(self, query: str = "", page: int = 1, __user__: dict = None) -> str:
        """Find writable Workspace Models by name or model ID; follow next_page for more results.

        :param query: Case-insensitive substring of agent name or model ID; empty lists writable models.
        :param page: Page of matching writable models, starting at 1.
        """
        from open_webui.models.models import Models
        from open_webui.routers.models import BYPASS_ADMIN_ACCESS_CONTROL, PAGE_ITEM_COUNT

        user = await authoring_user(__user__)
        if page < 1:
            raise ValueError("page must be positive")
        # The UI search first filters by read grants and does not search Model IDs.
        # Query the native writable catalog directly; never substitute the model owner.
        bypass = user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL
        models = await Models.get_models(writable_by_user_id=None if bypass else user.id)
        needle = query.strip().casefold()
        models = [m for m in models if not needle or needle in m.id.casefold() or needle in m.name.casefold()]
        models.sort(key=lambda m: (m.name.casefold(), m.id))
        start, end = (page - 1) * PAGE_ITEM_COUNT, page * PAGE_ITEM_COUNT
        return authoring_json({
            "items": [{"id": m.id, "name": m.name, "is_active": m.is_active}
                      for m in models[start:end]],
            "next_page": page + 1 if end < len(models) else None,
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
            exclusion_criteria, expected_result
            (all strings), and positive_examples and negative_examples (nonempty arrays of agreed strings).
            agent_model_id is used to authorize the target model and is not rendered into the profile.
            Describe only this agent's scope; exclusions must not name other agents or prescribe another route.
        """
        from open_webui.utils.skill_files import frontmatter

        user = await authoring_user(__user__)
        content = render(fields, authoring_template())
        model = await writable_agent(fields["agent_model_id"].strip(), user)
        return authoring_json({"agent_id": model.id, "name": frontmatter(content)["name"],
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
        from open_webui.routers.skills import create_new_skill, update_skill_by_id

        user = await authoring_user(__user__)
        model, _, values = await prepare_standalone_routing_change(fields, skill_id, expected_version_id, user)
        if expected_version_id:
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

    async def list_shared_routing_catalog(self, skill_id: str, offset: int = 0, limit: int = 25,
                                          expected_version_id: str = "", __user__: dict = None) -> str:
        """List registered routing profiles and any invalid entries in one catalog version.

        :param skill_id: Exact shared routing Skill ID.
        :param offset: Entry offset, initially 0; follow next_offset.
        :param limit: Number of entries per page, 1 through 100.
        :param expected_version_id: Pass the version of the first page on every continuation.
        """
        if offset < 0 or not 1 <= limit <= 100 or (offset and not expected_version_id):
            raise ValueError("Use a non-negative offset, a limit from 1 to 100 and a version on continuation")
        user = await authoring_user(__user__)
        skill, _, entries, invalid = await readable_routing_catalog(skill_id, expected_version_id, user)
        model_ids = sorted(entries.keys() | invalid.keys())
        end = offset + limit
        return authoring_json({
            "id": skill.id, "version_id": skill.version_id, "total": len(model_ids),
            "next_offset": end if end < len(model_ids) else None,
            "items": [{"agent_id": model_id, **(entries[model_id] if model_id in entries
                       else {"error": invalid[model_id]})} for model_id in model_ids[offset:end]],
        })

    async def read_shared_routing_profile(self, skill_id: str, model_id: str, expected_version_id: str,
                                          offset: int = 0, max_chars: int = 12000,
                                          __user__: dict = None) -> str:
        """Read the registered Markdown profile from the version used for catalog review.

        :param skill_id: Exact shared routing Skill ID.
        :param model_id: Exact registered Model ID from the catalog.
        :param expected_version_id: Version returned by list_shared_routing_catalog.
        :param offset: Character offset, initially 0; follow next_offset.
        :param max_chars: Page size, 1 through 20000.
        """
        if not expected_version_id:
            raise ValueError("Read the shared routing catalog to obtain the expected version")
        user = await authoring_user(__user__)
        skill, snapshot, entries, invalid = await readable_routing_catalog(skill_id, expected_version_id, user)
        if model_id not in entries:
            raise ValueError(invalid.get(model_id, "Model ID is not registered in this routing Skill"))
        entry = entries[model_id]
        content = routing_file_map(snapshot)[entry["path"]]["content"]
        return authoring_json({"id": skill.id, "agent_id": model_id, "version_id": skill.version_id,
                               **entry, **authoring_page(content, offset, max_chars)})

    async def inspect_shared_routing_profile(self, skill_id: str, model_id: str, offset: int = 0,
                                             max_chars: int = 12000, __user__: dict = None) -> str:
        """Read a profile in a shared routing Skill and get the current version for saving.

        :param skill_id: Exact shared Skill ID configured as routing_skill_id.
        :param model_id: Exact Workspace Model ID the author can edit.
        :param offset: Character offset, initially 0; follow next_offset.
        :param max_chars: Page size, 1 through 20000.
        """
        user = await authoring_user(__user__)
        model = await writable_agent(model_id, user)
        skill, files, index = await writable_routing_index(skill_id, user)
        existing = index.get(model.id)
        result = {"id": skill.id, "agent_id": model.id, "version_id": skill.version_id,
                  "path": None, "content": "", "next_offset": None}
        if existing is not None:
            path = routing_profile_path(existing["path"])
            file = files.get(path)
            if not file or file.get("encoding"):
                raise ValueError("Existing routing profile is missing or unreadable")
            result.update(path=path, description=existing.get("description"),
                          **authoring_page(file["content"], offset, max_chars))
        return authoring_json(result)

    async def preview_routing_change(self, fields: dict, skill_id: str, expected_version_id: str,
                                     shared: bool = True, __user__: dict = None) -> str:
        """Preview a routing profile without creating a version.

        :param fields: Proposed profile fields.
        :param skill_id: Exact shared routing Skill ID.
        :param expected_version_id: Reviewed version, or empty for a new separate Skill.
        :param shared: Use a shared Skill container (default) or preview a separate Skill.
        """
        user = await authoring_user(__user__)
        if not shared:
            model, previous, values = await prepare_standalone_routing_change(
                fields, skill_id, expected_version_id, user)
            if previous and previous.version_id != expected_version_id:
                from fastapi import HTTPException
                raise HTTPException(409, {"code": "version_conflict", "current_version_id": previous.version_id})
            return authoring_json({
                "id": skill_id, "agent_id": model.id, "version_id": expected_version_id,
                "name": values["name"], "content": values["content"], "path": "SKILL.md",
                "profile_diff": routing_diff(previous.content if previous else "", values["content"], "SKILL.md"),
                "registry_entry": {model.id: skill_id},
            })
        skill, model_id, name, path, before, previous, entry, operations = await prepare_shared_routing_change(
            fields, skill_id, expected_version_id, user)
        content = operations[0]["content"]
        old_entry = {model_id: previous} if previous is not None else {}
        new_entry = {model_id: entry}
        return authoring_json({
            "id": skill.id, "agent_id": model_id, "version_id": skill.version_id, "path": path, "name": name,
            "content": content, "registry_entry": new_entry, "profile_diff": routing_diff(before, content, path),
            "registry_diff": routing_diff(json.dumps(old_entry, ensure_ascii=False, indent=2) + "\n",
                                          json.dumps(new_entry, ensure_ascii=False, indent=2) + "\n", ROUTING_INDEX),
        })

    async def save_shared_routing_profile(self, fields: dict, skill_id: str, expected_version_id: str,
                                          __user__: dict = None, __request__=None) -> str:
        """Save the agreed profile and agents.json together in the specified shared routing Skill.

        :param fields: Same agreed field object used by render_routing_skill.
        :param skill_id: Exact shared Skill ID configured as routing_skill_id.
        :param expected_version_id: Version returned by inspect_shared_routing_profile, including for a new entry.
        """
        from open_webui.models.skills import SkillForm
        from open_webui.routers.skills import update_skill_by_id

        user = await authoring_user(__user__)
        skill, model_id, name, path, _, _, _, operations = await prepare_shared_routing_change(
            fields, skill_id, expected_version_id, user)
        form = SkillForm(id=skill.id, name=skill.name, operations=operations, expected_version_id=expected_version_id)
        saved = await update_skill_by_id(__request__, skill.id, form, user=user, db=None)
        if saved is None:
            raise ValueError("OWUI did not return a saved Skill")
        return authoring_json({
            "id": saved.id, "agent_id": model_id, "name": name, "path": path, "version_id": saved.version_id,
            "is_active": saved.is_active, "registry_updated": True, "router_connection": "not_checked",
        })

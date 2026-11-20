"""All imports of Open WebUI internal APIs live behind this boundary."""

import asyncio
from copy import deepcopy
import json
import logging
from uuid import UUID

from .binding import bind_tool_context
from .capabilities import MCP_CLOSE_TIMEOUT, bounded_tool_name
from .native_context import scoped_knowledge_result
from .history import context_handoff_key, handoff_evidence
from .protocol import completed_pairs
from .registry import ids
from .shared_summary import SummaryGenerationError, prompt_token_count, propagate_provider_error


def as_dict(value):
    return value.model_dump() if hasattr(value, "model_dump") else dict(value or {})


async def collect_summary_stream(stream):
    """Consume an unexpected provider SSE response without streaming it into the chat."""
    from open_webui.routers.openai import convert_responses_result

    pending = b""
    received = 0
    parts = []
    finish_reason = None
    message = {}
    try:
        async for chunk in stream:
            chunk = chunk.encode() if isinstance(chunk, str) else chunk
            received += len(chunk)
            if received > 4 * 1024 * 1024:
                raise SummaryGenerationError("Поток сводки превысил предел в 4 МиБ.")
            lines = (pending + chunk).split(b"\n")
            pending = lines.pop()
            for line in lines:
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    finish_reason = finish_reason or "stop"
                    break
                if not data:
                    continue
                event = json.loads(data)
                if event.get("error"):
                    propagate_provider_error(event["error"])
                kind = event.get("type")
                if kind in ("response.completed", "response.incomplete"):
                    return convert_responses_result(event["response"])
                if kind in ("error", "response.failed"):
                    propagate_provider_error((event.get("response") or {}).get("error") or event)
                for choice in event.get("choices") or ():
                    if choice.get("index", 0) != 0:
                        continue
                    delta = choice.get("delta") or {}
                    if isinstance(delta.get("content"), str):
                        parts.append(delta["content"])
                    if delta.get("tool_calls") or delta.get("function_call"):
                        message["tool_calls"] = True
                    finish_reason = choice.get("finish_reason") or finish_reason
            else:
                continue
            break
    finally:
        if hasattr(stream, "aclose"):
            await stream.aclose()
    if not finish_reason:
        raise SummaryGenerationError("Поток сводки завершился без признака окончания.")
    message["content"] = "".join(parts)
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def register_model_tool(registry, name, tool, namespace):
    alias = bounded_tool_name(name)
    while alias in registry:
        name = namespace + "_" + name
        alias = bounded_tool_name(name)
    registry[alias] = {**tool, "spec": {**deepcopy(tool["spec"]), "name": alias}}


def saved_handoff_statuses(branch, convert):
    statuses = {}
    for message in branch:
        if message.get("role") != "assistant" or not isinstance(message.get("output"), list):
            continue
        converted = convert(deepcopy(message["output"]), raw=True)
        markers = [handoff_evidence(converted[i]["tool_calls"][n], converted[r])
                   for i, n, r in completed_pairs(converted)]
        markers = [marker for marker in markers if marker]
        if len(markers) != 1:
            continue
        status = message.get("status")
        if status not in ("failed", "cancelled", "timed_out"):
            failed = any(item.get("type") == "message" and item.get("status") in ("failed", "rejected")
                         for item in message["output"] if isinstance(item, dict))
            pending = any(item.get("status") in ("pending", "in_progress", "incomplete", "requires_action")
                          for item in message["output"] if isinstance(item, dict))
            status = ("failed" if message.get("error") or failed else
                      "completed" if message.get("done") is True and not pending else "unknown")
        key = context_handoff_key(markers[0])
        statuses[key] = status if key not in statuses or statuses[key] == status else "unknown"
    return statuses


class OWUIAdapter:
    async def chat_skill_ids(self, body):
        from open_webui.utils.skills import extract_skill_ids_from_messages

        current = next((m for m in reversed(body.get("messages") or []) if m.get("role") == "user"), None)
        selected = ids(body.get("skill_ids") or [])
        mentioned = sorted(extract_skill_ids_from_messages([current] if current else []))
        # Terminal Skills retain their native terminal scope and reader contract.
        return [sid for sid in ids(selected + mentioned) if not sid.startswith("terminal:")]

    async def historical_handoff_statuses(self, request, metadata, state, user):
        """Read terminal evidence from this saved branch, never from client metadata."""
        if not state.get("chat_id") or user.id != state.get("user_id"):
            return {}
        from open_webui.models.chats import Chats
        from open_webui.utils.misc import get_message_list, convert_output_to_messages

        try:
            chat = await Chats.get_accessible_chat_by_id(state["chat_id"], user)
            if chat is None:
                return {}
            messages = await Chats.get_messages_map_by_chat_id(chat.id)
            current = state.get("message_id")
            if current not in (messages or {}):
                current = metadata.get("user_message_id")
            branch = get_message_list(messages or {}, current)
            if (state.get("context_policy") or {}).get("shared_summary_model_id"):
                router_usage, agent_usage = {}, {}
                resume = getattr(getattr(request, "state", None), "tool_approval_resume", None)
                resumed_current = (isinstance(resume, (tuple, list)) and
                                   tuple(resume[:2]) == (state["chat_id"], state["message_id"]))
                for message in branch:
                    if message.get("role") != "assistant":
                        continue
                    meta = message.get("meta") or {}
                    is_current = message.get("id") == state["message_id"]
                    if not is_current:
                        sample = meta.get("lite_router_usage")
                        if isinstance(sample, dict) and isinstance(sample.get("model"), str):
                            router_usage[sample["model"]] = sample
                    if is_current and not resumed_current:
                        continue
                    samples = meta.get("lite_agent_usage")
                    if isinstance(samples, list):
                        for sample in samples:
                            tokens = prompt_token_count(sample)
                            if tokens is not None and isinstance(sample.get("agent_id"), str) and isinstance(sample.get("model"), str):
                                agent_usage[sample["agent_id"], sample["model"]] = tokens
                state["historical_router_usage"] = router_usage
                state["historical_agent_prompt_tokens"] = agent_usage
            return saved_handoff_statuses(branch, convert_output_to_messages)
        except Exception as error:
            logging.getLogger(__name__).warning("Historical terminal evidence unavailable (%s)", type(error).__name__)
            return {}

    async def native_memory_allowed(self, metadata, model, user):
        from open_webui.models.config import Config
        from open_webui.utils.access_control import has_permission
        from open_webui.utils.memory import model_allows_memory

        return (bool((metadata.get("features") or {}).get("memory"))
                          and await Config.get("memories.enable")
                          and await Config.get("memories.system_context.enable")
                          and model_allows_memory(model)
                          and (user.role == "admin" or await has_permission(
                              user.id, "features.memories", await Config.get("user.permissions"))))

    async def native_resume_context(self, request, metadata, state, user):
        approval = getattr(request.state, "tool_approval_resume", None)
        if (user.id != state["user_id"] or not isinstance(approval, (tuple, list)) or len(approval) != 3
                or tuple(approval[:2]) != (state["chat_id"], metadata.get("assistant_message_id"))
                or metadata.get("tool_approval_resume") != approval[2]
                or metadata.get("assistant_message_id") != state["message_id"]):
            return []
        model = metadata.get("model") or {}
        if not await self.native_memory_allowed(metadata, model, user):
            return []
        from open_webui.utils.middleware import preload_memory
        output = await preload_memory(request, deepcopy(state["source"]), user, model)
        request.scope["lite_handoff_metadata"]["initial_output"] = output
        return self.native_context_messages(request, metadata, state)

    async def native_context_policy(self, request, metadata, state, model, user):
        allowed = {}
        context = state.get("native_context") or {}
        identity = tuple(state.get(k) for k in ("request_id", "user_id", "chat_id", "message_id"))
        if user.id != state["user_id"] or context.get("identity") != identity:
            return allowed
        memory_enabled = await self.native_memory_allowed(metadata, model, user)
        for record in (state.get("native_context") or {}).get("records", []):
            if record["name"] == "recall_memories" and memory_enabled:
                allowed[record["key"]] = deepcopy(record["result"])
            elif record["name"] == "list_attached_knowledge":
                from open_webui.utils.tools import get_attached_knowledge
                meta = model.get("info", {}).get("meta") or {}
                if not (meta.get("builtinTools") or {}).get("knowledge", True):
                    continue
                eligible = {(item.get("type"), item.get("id"), item.get("source"))
                            for item in get_attached_knowledge(model, metadata)}
                async def check(kind, identifier):
                    return await self.native_knowledge_access(request, user, kind, identifier)
                result = await scoped_knowledge_result(record, eligible, check)
                if result:
                    allowed[record["key"]] = result
        return allowed

    async def native_knowledge_access(self, request, user, kind, identifier):
        from fastapi import HTTPException
        try:
            if kind == "collection":
                from open_webui.routers.knowledge import get_knowledge_by_id
                return await get_knowledge_by_id(identifier, user=user, db=None) is not None
            if kind == "file":
                await self.file(identifier, user)
                return True
            if kind == "note":
                from open_webui.routers.notes import get_note_by_id
                return await get_note_by_id(request, identifier, user=user, db=None) is not None
        except (HTTPException, ValueError):
            return False
        return False

    def native_context_messages(self, request, metadata, state):
        from open_webui.utils.misc import convert_output_to_messages

        root = request.scope.get("lite_handoff_metadata")
        if (not isinstance(root, dict) or root.get("user_id", state["user_id"]) != state["user_id"]
                or any(root.get(k) != state.get(k) for k in ("chat_id", "message_id"))):
            return []
        output = root.get("initial_output")
        if not isinstance(output, list):
            return []
        calls = [item for item in output if isinstance(item, dict) and item.get("type") == "function_call"]
        allowed = {item.get("call_id") for item in calls
                   if item.get("hidden") is True and item.get("status") == "completed"}
        verified = [item for item in output if isinstance(item, dict) and item.get("call_id") in allowed
                    and item.get("status") == "completed"
                    and item.get("type") in {"function_call", "function_call_output"}]
        return convert_output_to_messages(deepcopy(verified), raw=True) if verified else []

    async def user(self, user_id):
        from open_webui.models.users import Users

        user = await Users.get_user_by_id(user_id)
        if user is None:
            raise ValueError("Execution user is unavailable")
        return user

    async def model(self, request, model_id, user):
        from open_webui.models.models import Models
        from open_webui.utils.models import check_model_access

        row = await Models.get_model_by_id(model_id)
        runtime = request.app.state.MODELS.get(model_id)
        if row is None or not row.is_active or runtime is None:
            raise ValueError(f'Workspace Model "{model_id}" is unavailable or inactive')
        await check_model_access(user, runtime, model_info=row)
        # Never modify the shared model catalog, including its nested info.
        result = deepcopy(runtime)
        result["info"] = as_dict(row)
        return result

    async def skill(self, skill_id, user):
        from open_webui.routers.skills import authorized_skill

        skill = await authorized_skill(skill_id, user)
        if not skill.is_active:
            raise ValueError(f'Skill "{skill_id}" is inactive')
        return as_dict(skill)

    async def owner(self, model):
        identifier = (model.get("info") or {}).get("user_id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("Workspace Model resource owner is unavailable")
        try:
            return await self.user(identifier)
        except ValueError as error:
            raise ValueError("Workspace Model resource owner is unavailable") from error

    async def tools(self, request, ids, user, extra):
        from open_webui.models.tools import Tools
        from open_webui.utils.plugin import get_tools_cache, get_tool_contents_cache

        actor = await self.user(extra["__user__"]["id"])
        local = [identifier for identifier in ids if not identifier.startswith("server:")]
        rows = await Tools.get_tools_by_ids(local) if local else {}
        result = {}
        for identifier in local:
            row = rows.get(identifier)
            if row is None:
                continue
            try:
                await self.tool_access(request, identifier, user)
            except ValueError:
                continue
            module = get_tools_cache(request).get(identifier)
            if module is None or get_tool_contents_cache(request).get(identifier) != row.content:
                module = await self.load_module(request, identifier, row.content)
            if hasattr(module, "valves") and hasattr(module, "Valves"):
                module.valves = module.Valves(**(await Tools.get_tool_valves_by_id(identifier) or {}))
            injection = actor.model_dump()
            if hasattr(module, "UserValves"):
                values = await Tools.get_user_valves_by_id_and_user_id(identifier, actor.id)
                injection["valves"] = module.UserValves(**values)
            for stored in row.specs:
                spec = deepcopy(stored)
                properties = spec.get("parameters", {}).get("properties", {})
                spec["parameters"]["properties"] = {key: value for key, value in properties.items()
                                                       if not key.startswith("__")}
                for value in spec["parameters"]["properties"].values():
                    if value.get("type") == "str":
                        value["type"] = "string"
                name = spec["name"]
                function = getattr(module, name)
                tool = {"tool_id": identifier, "spec": spec, "metadata": {
                    "file_handler": bool(getattr(module, "file_handler", False)),
                    "citation": bool(getattr(module, "citation", False))},
                    "callable": await self.bind(function, {**extra, "__id__": identifier, "__user__": injection})}
                tool = await bind_tool_context(self, tool, None, pinned={"__user__": injection})
                register_model_tool(result, name, tool, identifier)
        for identifier in ids:
            if not identifier.startswith("server:"):
                continue
            for name, tool in (await self.server_tools(request, identifier, user, actor, extra)).items():
                register_model_tool(result, name, tool, identifier.partition("|")[0].replace(":", "_"))
        return result

    async def load_module(self, request, identifier, content):
        from open_webui.utils.plugin import load_tool_module_by_id, set_tool_module_in_cache

        module, _, source = await load_tool_module_by_id(identifier, content=content)
        set_tool_module_in_cache(request, identifier, content, module, source)
        return module

    def local_tools_enabled(self):
        from open_webui.env import ENABLE_TOOLS

        return ENABLE_TOOLS

    def servers_enabled(self):
        from open_webui.utils.tools import ENABLE_TOOL_SERVERS

        return ENABLE_TOOL_SERVERS

    async def member_groups(self, user):
        from open_webui.models.groups import Groups

        return {g.id for g in await Groups.get_groups_by_member_id(user.id, include_inherited=True)}

    async def tool_access(self, request, identifier, owner):
        from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
        from open_webui.models.tools import Tools
        from open_webui.models.access_grants import AccessGrants

        if identifier.startswith("server:mcp:"):
            await self.mcp_access(identifier.removeprefix("server:mcp:"), owner)
            return
        if identifier.startswith("server:"):
            await self.openapi_server(request, identifier, owner)
            return
        row = await Tools.get_tool_by_id(identifier) if self.local_tools_enabled() else None
        groups = await self.member_groups(owner)
        if row is None or not row.is_active or not ((owner.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL)
                or row.user_id == owner.id or await AccessGrants.has_access(
                    user_id=owner.id, resource_type="tool", resource_id=identifier,
                    permission="read", user_group_ids=groups)):
            raise ValueError("Required model Tool unavailable or denied: " + identifier)

    async def openapi_server(self, request, identifier, owner):
        from open_webui.utils import tools

        parts = identifier.split(":")
        if len(parts) not in (2, 3) or (len(parts) == 3 and parts[1] != "openapi"):
            raise ValueError("Unsupported model Tool server: " + identifier)
        server_id, _, subset = parts[-1].partition("|")
        data = next((s for s in await tools.get_tool_servers(request) if s["id"] == server_id), None)
        connections = await tools.Config.get("tool_server.connections", [])
        index = data.get("idx", 0) if data else -1
        connection = connections[index] if 0 <= index < len(connections) else None
        if (not self.servers_enabled() or not data or not connection
                or not (connection.get("config") or {}).get("enable", True)
                or not await tools.has_connection_access(owner, connection)):
            raise ValueError("Required model Tool server unavailable or denied: " + identifier)
        return server_id, data, connection, set(subset.split(",")) if subset else None

    async def server_tools(self, request, identifier, owner, actor, extra):
        from open_webui.utils import tools

        server_id, data, connection, subset = await self.openapi_server(request, identifier, owner)
        # Detect missing required actor authorization before publishing a draft.
        await self.server_headers(request, connection, actor, server_id, extra.get("__metadata__", {}))

        def allowed(name, current):
            filters = (current.get("config") or {}).get("function_name_filter_list", "")
            if isinstance(filters, str):
                filters = filters.split(",")
            return (subset is None or name in subset) and (not filters or tools.is_string_allowed(name, filters))

        def operation(name):
            async def invoke(**kwargs):
                fresh_owner = await self.user(owner.id)
                _, current_data, current, _ = await self.openapi_server(request, identifier, fresh_owner)
                if not allowed(name, current) or not any(s["name"] == name for s in current_data.get("specs", [])):
                    raise ValueError("Model server Tool is no longer available: " + name)
                current_actor = await self.user(actor.id)
                headers, cookies = await self.server_headers(
                    request, current, current_actor, server_id, extra.get("__metadata__", {}))
                headers.setdefault("Content-Type", "application/json")
                return await tools.execute_tool_server(url=current_data["url"], headers=headers, cookies=cookies,
                    name=name, params=kwargs, server_data=current_data)
            return invoke

        result = {}
        for spec in data.get("specs", []):
            name = spec["name"]
            if allowed(name, connection):
                result[name] = {"tool_id": identifier, "type": "external",
                    "spec": tools.clean_openai_tool_schema(deepcopy(spec)),
                    "callable": await self.bind(operation(name), {})}
        return result

    async def server_headers(self, request, connection, actor, server_id, metadata, *, require_user_auth=False):
        from open_webui.utils import tools

        auth = connection.get("auth_type", "bearer")
        if require_user_auth:
            if auth == "session" and not getattr(getattr(request.state, "token", None), "credentials", None):
                raise ValueError("Terminal requires execution-user session authorization")
            if auth == "system_oauth":
                token = await tools.get_system_oauth_token(request, actor)
                if not token or not token.get("access_token"):
                    raise ValueError("Terminal requires execution-user OAuth authorization")
        return await tools.build_tool_server_headers(
            connection, request, actor, server_id=server_id, metadata=metadata)

    async def terminal_access(self, request, identifier, owner):
        from open_webui.utils import tools

        connections = await tools.Config.get("terminal_server.connections", []) or []
        connection = next((c for c in connections if c.get("id") == identifier), None)
        groups = await self.member_groups(owner)
        if (not self.servers_enabled() or not connection or not connection.get("enabled", True)
                or not await tools.has_connection_access(owner, connection, groups)):
            raise ValueError("Required model terminal unavailable or denied: " + identifier)
        return connection

    async def terminal_server(self, request, identifier, owner, metadata):
        from open_webui.utils import tools
        from open_webui.utils.terminals import (
            terminal_context_available, terminal_context_config, terminal_context_id, get_terminal_server_url)

        connection = await self.terminal_access(request, identifier, owner)
        context_id = terminal_context_id(connection, metadata, "chat")
        context = terminal_context_config(connection, "chat")
        if not terminal_context_available(connection, "chat") or (
                isinstance(context, dict) and context.get("context_id") == "chat_id" and not context_id):
            raise ValueError("Model terminal requires an available saved chat context: " + identifier)
        data = next((s for s in await tools.get_terminal_servers(request) if s.get("id") == identifier), None)
        if not data or not data.get("specs"):
            raise ValueError("Required model terminal has no available Tools: " + identifier)
        return connection, {**data, "url": get_terminal_server_url(connection)}, context_id

    async def terminal_headers(self, request, identifier, connection, actor, metadata, context_id):
        from open_webui.utils.terminals import TERMINAL_CONTEXT_HEADER

        auth = connection.get("auth_type", "bearer")
        if auth not in {"bearer", "session", "system_oauth", "none"}:
            raise ValueError("Unsupported terminal authorization: " + str(auth))
        headers, cookies = await self.server_headers(
            request, connection, actor, identifier, metadata, require_user_auth=True)
        headers.update({"Content-Type": "application/json", "X-User-Id": actor.id})
        # A connection's custom headers cannot override trusted execution routing.
        for name in list(headers):
            if name.lower() in {"x-user-id", "x-session-id", TERMINAL_CONTEXT_HEADER.lower()}:
                headers.pop(name)
        headers["X-User-Id"] = actor.id
        if metadata.get("chat_id"):
            headers["X-Session-Id"] = metadata["chat_id"]
        if context_id:
            headers[TERMINAL_CONTEXT_HEADER] = context_id
        return headers, cookies

    async def terminal_tools(self, request, identifier, owner, extra):
        from open_webui.utils import tools

        actor = await self.user(extra["__user__"]["id"])
        metadata = {key: extra.get("__metadata__", {}).get(key) for key in ("chat_id", "message_id")}
        connection, data, context_id = await self.terminal_server(request, identifier, owner, metadata)
        headers, cookies = await self.terminal_headers(request, identifier, connection, actor, metadata, context_id)
        if (connection.get("config") or {}).get("working_directory_context", True):
            cwd, prompt = await asyncio.gather(tools.get_terminal_cwd(data["url"], headers, cookies),
                                              tools.get_terminal_system_prompt(data["url"], headers, cookies))
        else:
            cwd, prompt = None, await tools.get_terminal_system_prompt(data["url"], headers, cookies)

        def operation(name):
            async def invoke(**kwargs):
                current_owner = await self.user(owner.id)
                current, current_data, context = await self.terminal_server(request, identifier, current_owner, metadata)
                if not any(s.get("name") == name for s in current_data.get("specs", [])):
                    raise ValueError("Model terminal Tool is no longer available: " + name)
                current_actor = await self.user(actor.id)
                fresh_headers, fresh_cookies = await self.terminal_headers(
                    request, identifier, current, current_actor, metadata, context)
                if name == "display_file":
                    kwargs.pop("page", None)
                return await tools.execute_tool_server(url=current_data["url"], headers=fresh_headers,
                    cookies=fresh_cookies, name=name, params=kwargs, server_data=current_data)
            return invoke

        result = {}
        for spec in data["specs"]:
            name = spec["name"]
            if name in {"read_user_terminal", "send_user_terminal_input"}:
                continue
            if name in result:
                raise ValueError("Conflicting terminal Tool name: " + name)
            schema = tools.clean_openai_tool_schema(
                tools.get_builtin_tool_spec(tools.display_file) if name == "display_file" else deepcopy(spec))
            if name == "run_command" and cwd:
                schema["description"] = schema.get("description", "") + "\n\nThe current working directory is: " + cwd
            result[name] = {"tool_id": "terminal:" + identifier, "type": "terminal", "spec": schema,
                            "callable": await self.bind(operation(name), {})}
        if not result:
            raise ValueError("Required model terminal has no permitted server Tools: " + identifier)
        return result, prompt or data.get("system_prompt")

    async def connect_mcp(self, request, server_id, user, extra, *, register_client=None):
        from fastapi import HTTPException
        from open_webui.utils import tools

        connection = await self.mcp_connection(server_id, user)
        actor = await self.user(extra["__user__"]["id"])
        metadata = extra.get("__metadata__", {})

        async def headers():
            values, _ = await tools.build_tool_server_headers(
                connection, request, actor, server_id=server_id, metadata=metadata)
            return values

        auth = tools.OAuthTokenAuth(headers) if connection.get("auth_type") == "system_oauth" else None
        initial = {} if auth else await headers()
        if connection.get("auth_type") in ("oauth_2.1", "oauth_2.1_static") and not initial.get("Authorization"):
            raise HTTPException(status_code=401, detail="Auth required")
        client = tools.MCPClient(event_caller=extra.get("__event_call__") if metadata.get("session_id") else None,
                                 server_name=server_id)
        if register_client is not None:
            register_client(client)
        try:
            await client.connect(url=connection.get("url", ""), headers=initial or None, auth=auth)
            specs = await client.list_tool_specs()
            return client, specs
        except BaseException:
            # Request-owned sessions close even a partially opened client, in
            # parallel with cancellation of this operation. Direct callers own
            # successful clients and retain bounded failure cleanup here.
            if register_client is None:
                try:
                    async with asyncio.timeout(MCP_CLOSE_TIMEOUT):
                        await client.disconnect()
                except Exception:
                    logging.getLogger(__name__).exception("MCP opening cleanup failed")
            raise

    async def mcp_access(self, server_id, user):
        await self.mcp_connection(server_id, user)

    async def mcp_specs(self, server_id, user, specs):
        from open_webui.utils.tools import is_string_allowed

        connection = await self.mcp_connection(server_id, user)
        filters = (connection.get("config") or {}).get("function_name_filter_list", "")
        if isinstance(filters, str):
            filters = filters.split(",")
        return [spec for spec in specs if is_string_allowed(spec["name"], filters)]

    async def mcp_connection(self, server_id, user):
        from open_webui.models.config import Config
        from open_webui.utils.access_control import has_connection_access

        connections = await Config.get("tool_server.connections", [])
        connection = next((c for c in connections if c.get("type") == "mcp"
                           and (c.get("info") or {}).get("id") == server_id), None)
        if not self.servers_enabled() or not connection or not (connection.get("config") or {}).get("enable") \
                or not await has_connection_access(user, connection):
            raise ValueError("Required MCP connection unavailable or denied: " + server_id)
        return connection

    async def builtins(self, request, model, extra):
        from open_webui.utils.tools import get_builtin_tools

        return await get_builtin_tools(request, extra, features={}, model=model)

    async def bind(self, function, extra):
        from open_webui.utils.tools import get_async_tool_function_and_apply_extra_params

        return await get_async_tool_function_and_apply_extra_params(function, extra)

    async def refresh(self, function, extra):
        from open_webui.utils.tools import get_updated_tool_function

        return await get_updated_tool_function(function, extra)

    async def prompt_and_params(self, body, model, user, metadata):
        from open_webui.utils.payload import apply_params_to_form_data, apply_system_prompt_to_body

        params = deepcopy(model.get("info", {}).get("params") or {})
        body = apply_params_to_form_data(body, model, params)
        return await apply_system_prompt_to_body(params.get("system"), body, metadata, user)

    async def inlets(self, request, body, model, extra):
        from open_webui.utils.filter import get_filter_functions, process_filter_functions

        filters = await get_filter_functions(request, model, [])
        body, _ = await process_filter_functions(
            request=request, filter_context=None, filter_functions=filters,
            filter_type="inlet", form_data=body, extra_params=extra,
        )
        return body

    async def parallel_reason(self, request, metadata, user):
        from open_webui.models.chats import Chats
        from open_webui.models.config import Config

        try:
            UUID(str(metadata.get("chat_id", "")))
        except ValueError:
            return "Parallel tasks require a saved chat"
        if not await Config.get("subagents.enable", False):
            return "Parallel tasks require subagents.enable in OWUI"
        if metadata.get("params", {}).get("tool_approval_mode", "full") != "full":
            return "Parallel tasks cannot resume tool approval pauses; use a supported approval policy"
        parent = await Chats.get_chat_by_id(metadata["chat_id"])
        if not parent or parent.user_id != user.id or (parent.meta or {}).get("internal"):
            return "Parallel tasks require an owned saved main chat"
        message = await Chats.get_message_by_id_and_message_id(parent.id, metadata.get("message_id"))
        if not message or message.get("done") is True:
            return "Parallel tasks require an active parent message"
        return None

    async def file(self, file_id, user):
        import asyncio
        from pathlib import Path
        from open_webui.routers.files import get_file_by_id
        from open_webui.storage.provider import Storage

        try:
            UUID(str(file_id))
            row = await get_file_by_id(file_id, user=user, db=None)
            path = await asyncio.to_thread(Storage.get_file, row.path) if row.path else None
            if not path or not Path(path).is_file():
                raise ValueError("Original upload is missing")
        except Exception as error:
            raise ValueError("OWUI file unavailable or denied: " + str(file_id)) from error
        return {"type": "file", "id": row.id, "name": (row.meta or {}).get("name") or row.filename,
                "url": "/api/v1/files/" + row.id + "/content"}

    async def native_delegate(self, request, user, metadata, task, file_ids):
        from open_webui.utils.subagents import delegate

        return await delegate(task, "", False, request=request, user_data=user.model_dump(),
                              metadata=metadata, file_ids=file_ids,
                              parent_chat_id=metadata["chat_id"],
                              parent_message_id=metadata["message_id"])

    async def child(self, child_id, user):
        from open_webui.models.chats import Chats

        chat = await Chats.get_accessible_chat_by_id(child_id, user)
        return as_dict(chat) if chat else None

    async def child_message(self, child_id, message_id, user):
        from open_webui.models.chats import Chats

        if not await Chats.get_accessible_chat_by_id(child_id, user):
            return None
        return await Chats.get_message_by_id_and_message_id(child_id, message_id)

    async def children(self, parent_id, user):
        from open_webui.models.chats import Chats

        return await Chats.get_internal_chat_ids_by_parent_id(parent_id, user.id)

    async def correlate_child(self, child_id, user, correlation):
        from open_webui.models.chats import Chats

        async with Chats._chat_transaction(child_id) as (_, row):
            if row is None or row.user_id != user.id or (row.meta or {}).get("internal") is not True:
                raise ValueError("Internal execution chat is unavailable")
            row.meta = {**(row.meta or {}), "lite_parallel": deepcopy(correlation)}

    async def stop_child(self, request, child_id, user):
        from open_webui.main import stop_tasks_by_chat_id_endpoint

        if await self.child(child_id, user):
            await stop_tasks_by_chat_id_endpoint(request, child_id, user=user)

    async def save_prompt_usage(self, state, user, model_id, prompt_tokens, *, agent_id=None, generation=None):
        from open_webui.models.chats import Chats
        from open_webui.utils.chat_id import is_saved_chat_id

        logger = logging.getLogger(__name__)
        sample = {"model": model_id, "prompt_tokens": prompt_tokens}
        if agent_id:
            sample.update(agent_id=agent_id, generation=generation, request_id=state["request_id"])
            logger.info("Agent input tokens: agent=%s model=%s request=%s generation=%s prompt_tokens=%d",
                        agent_id, model_id, state["request_id"], generation, prompt_tokens)
        if not is_saved_chat_id(state["chat_id"]) or not state["message_id"]:
            return
        try:
            chat = await Chats.get_accessible_chat_by_id(state["chat_id"], user)
            if chat is None or chat.user_id != user.id:
                return
            async with Chats.edit_message_output(chat.id, state["message_id"]) as message:
                if message is not None and message.get("role") == "assistant":
                    meta = {**(message.get("meta") or {})}
                    if agent_id:
                        previous = meta.get("lite_agent_usage")
                        entries = previous if isinstance(previous, list) else []
                        updated = [entry for entry in entries if not isinstance(entry, dict) or
                                   (entry.get("request_id"), entry.get("generation")) != (state["request_id"], generation)]
                        meta["lite_agent_usage"] = [*updated, sample]
                    else:
                        meta["lite_router_usage"] = sample
                    message["meta"] = meta
        except Exception as error:
            logger.warning("Prompt token usage could not be saved (%s)", type(error).__name__)

    async def shared_summary_cache(self, state, user, model_id):
        from open_webui.models.chats import Chats
        from open_webui.utils.chat_id import is_saved_chat_id

        if not is_saved_chat_id(state["chat_id"]):
            return {}
        try:
            chat = await Chats.get_accessible_chat_by_id(state["chat_id"], user)
            if chat is None or chat.user_id != user.id:
                return {}
            entries = (chat.meta or {}).get("lite_shared_summaries", ())
            return {entry["key"]: entry["summary"] for entry in entries
                    if entry.get("router") == state["router_id"] and entry.get("model") == model_id
                    and isinstance(entry.get("summary"), str) and entry["summary"].strip()}
        except Exception as error:
            logging.getLogger(__name__).warning("Shared summary cache unreadable (%s)", type(error).__name__)
            return {}

    async def save_shared_summary(self, state, user, model_id, key, summary):
        from open_webui.models.chats import Chats
        from open_webui.utils.chat_id import is_saved_chat_id

        if not is_saved_chat_id(state["chat_id"]):
            return
        try:
            async with Chats._chat_transaction(state["chat_id"]) as (_, row):
                if row is None or row.user_id != user.id:
                    return
                entry = {"summary": summary, "key": key, "model": model_id, "router": state["router_id"]}
                meta = dict(row.meta or {})
                entries = meta.get("lite_shared_summaries") or []
                meta["lite_shared_summaries"] = ([old for old in entries if any(
                    old.get(field) != entry[field] for field in ("router", "model", "key"))] + [entry])[-8:]
                row.meta = meta
        except Exception as error:
            logging.getLogger(__name__).warning("Shared summary cache write failed (%s)", type(error).__name__)

    async def generate_shared_summary(self, request, user, model_id, messages, previous):
        from open_webui.models.config import Config
        from open_webui.utils.context_compaction import DEFAULT_CONTEXT_COMPACTION_PROMPT
        from open_webui.utils.models import check_model_access
        from open_webui.utils.payload import apply_params_to_form_data
        from open_webui.utils.task import prompt_template, replace_messages_variable
        from starlette.requests import Request
        from starlette.responses import StreamingResponse

        model = request.app.state.MODELS.get(model_id)
        if not model or model.get("pipe") or model.get("pipeline") or model.get("owned_by") != "openai":
            raise SummaryGenerationError("Модель суммаризации не найдена или несовместима с OpenAI API.")
        try:
            await check_model_access(user, model)
        except Exception as error:
            raise SummaryGenerationError("Open WebUI не подтвердил доступ к модели суммаризации.") from error
        isolated = Request({"type": "http", "method": "POST", "path": request.url.path,
                            "app": request.app, "headers": [], "state": {}})
        config = await Config.get_many("chat.context_compaction.prompt_template", "task.model.params")
        template = (config.get("chat.context_compaction.prompt_template") or "").strip()
        prompt = await prompt_template(template or DEFAULT_CONTEXT_COMPACTION_PROMPT, user)
        prompt = replace_messages_variable(prompt, [], "RECENT_MESSAGES")
        prompt = replace_messages_variable(prompt, messages)
        prompt = replace_messages_variable(prompt, messages, "COMPACTED_MESSAGES")
        prompt = prompt.replace("{{PREVIOUS_SUMMARY}}", previous or "")
        body = {"model": model_id, "messages": [{"role": "user", "content": prompt}], "stream": False,
                "n": 1, "metadata": {"task": "context_compaction"}}
        params = config.get("task.model.params")
        if isinstance(params, dict):
            body = apply_params_to_form_data(body, model, params)
        async with asyncio.timeout(45):
            response = await self.complete(isolated, body, user, bypass_system_prompt=False)
            if isinstance(response, list) and len(response) == 1:
                response = response[0]
            if isinstance(response, StreamingResponse):
                response = await collect_summary_stream(response.body_iterator)
        if not isinstance(response, dict):
            raise SummaryGenerationError("Провайдер вернул ответ сводки в неожиданном формате.")
        if response.get("error"):
            raise SummaryGenerationError("Провайдер вернул ошибку в ответе суммаризации.")
        choices = response.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        content = message.get("content")
        if choice.get("finish_reason") == "length":
            raise SummaryGenerationError("Генерация сводки достигла лимита модели (finish_reason=length).")
        if message.get("tool_calls"):
            raise SummaryGenerationError("Модель суммаризации вернула вызов инструмента.")
        if choice.get("finish_reason") not in (None, "stop"):
            raise SummaryGenerationError("Провайдер прервал генерацию сводки.")
        return content.strip() if isinstance(content, str) else ""

    async def complete(self, request, body, user, *, bypass_system_prompt=True):
        from fastapi import HTTPException
        from starlette.responses import Response, StreamingResponse
        from open_webui.models.config import Config
        from open_webui.utils.chat import generate_chat_completion

        if await Config.get("chat.context_compaction.enable", False):
            raise RuntimeError(
                "Lite Handoff Router requires full history: disable "
                "chat.context_compaction.enable in OWUI before continuing"
            )
        try:
            # prompt_and_params already installed the chosen prompt. The outer
            # native continuation's bypass flag must not suppress that step.
            response = await generate_chat_completion(
                request, body, user, bypass_system_prompt=bypass_system_prompt,
            )
        except HTTPException as error:
            try:
                propagate_provider_error(error.detail)
            except RuntimeError as provider_error:
                raise provider_error from error
        if isinstance(response, Response) and response.status_code >= 400:
            raw = getattr(response, "body", b"")
            detail = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            propagate_provider_error(detail or f"Provider HTTP {response.status_code}")
        if isinstance(response, StreamingResponse):
            return response
        if isinstance(response, Response):
            response = json.loads(response.body)
        if response is None or (isinstance(response, dict) and response.get("error")):
            propagate_provider_error(response or "Provider returned no response")
        return response

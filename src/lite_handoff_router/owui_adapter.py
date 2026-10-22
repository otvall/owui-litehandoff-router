"""All imports of OWUI's internal APIs live behind this versioned boundary."""

from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
import json
from uuid import UUID

from .binding import bind_tool_context


def as_dict(value):
    return value.model_dump() if hasattr(value, "model_dump") else dict(value or {})


class OWUIAdapter:
    def __init__(self):
        try:
            installed_version = version("open-webui")
        except PackageNotFoundError:
            # The official Docker image runs the source checkout, not a wheel.
            from open_webui.env import VERSION

            installed_version = VERSION
        if installed_version != "0.12.3":
            raise RuntimeError("Lite Handoff Router requires Open WebUI 0.12.3")

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
        from open_webui.utils.plugin import (get_tools_cache, get_tool_contents_cache,
                                            load_tool_module_by_id, set_tool_module_in_cache)

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
                module, _, source = await load_tool_module_by_id(identifier, content=row.content)
                set_tool_module_in_cache(request, identifier, row.content, module, source)
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
                while name in result:
                    name = identifier + "_" + name
                spec["name"] = name
                tool = {"tool_id": identifier, "spec": spec, "metadata": {
                    "file_handler": bool(getattr(module, "file_handler", False)),
                    "citation": bool(getattr(module, "citation", False))},
                    "callable": await self.bind(function, {**extra, "__id__": identifier, "__user__": injection})}
                result[name] = await bind_tool_context(self, tool, None, pinned={"__user__": injection})
        for identifier in ids:
            if not identifier.startswith("server:"):
                continue
            for name, tool in (await self.server_tools(request, identifier, user, actor, extra)).items():
                while name in result:
                    name = identifier.replace(":", "_") + "_" + name
                result[name] = tool
        return result

    async def tool_access(self, request, identifier, owner):
        from open_webui.env import ENABLE_TOOLS
        from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
        from open_webui.models.tools import Tools
        from open_webui.models.groups import Groups
        from open_webui.models.access_grants import AccessGrants

        if identifier.startswith("server:mcp:"):
            await self.mcp_access(identifier.removeprefix("server:mcp:"), owner)
            return
        if identifier.startswith("server:"):
            await self.openapi_server(request, identifier, owner)
            return
        row = await Tools.get_tool_by_id(identifier) if ENABLE_TOOLS else None
        groups = {g.id for g in await Groups.get_groups_by_member_id(owner.id, include_inherited=True)}
        if row is None or not ((owner.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL)
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
        if (not tools.ENABLE_TOOL_SERVERS or not data or not connection
                or not (connection.get("config") or {}).get("enable", True)
                or not await tools.has_connection_access(owner, connection)):
            raise ValueError("Required model Tool server unavailable or denied: " + identifier)
        return server_id, data, connection, set(subset.split(",")) if subset else None

    async def server_tools(self, request, identifier, owner, actor, extra):
        from open_webui.utils import tools

        server_id, data, connection, subset = await self.openapi_server(request, identifier, owner)

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
                headers, cookies = await tools.build_tool_server_headers(
                    current, request, actor, server_id=server_id, metadata=extra.get("__metadata__", {}))
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

    async def connect_mcp(self, request, server_id, user, extra):
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
        try:
            await client.connect(url=connection.get("url", ""), headers=initial or None, auth=auth)
            specs = await client.list_tool_specs()
            filters = (connection.get("config") or {}).get("function_name_filter_list", "")
            if isinstance(filters, str):
                filters = filters.split(",")
            if filters:
                specs = [spec for spec in specs if tools.is_string_allowed(spec["name"], filters)]
            return client, specs
        except BaseException:
            await client.disconnect()
            raise

    async def mcp_access(self, server_id, user):
        await self.mcp_connection(server_id, user)

    async def mcp_connection(self, server_id, user):
        from open_webui.env import ENABLE_TOOL_SERVERS
        from open_webui.models.config import Config
        from open_webui.utils.access_control import has_connection_access

        connections = await Config.get("tool_server.connections", [])
        connection = next((c for c in connections if c.get("type") == "mcp"
                           and (c.get("info") or {}).get("id") == server_id), None)
        if not ENABLE_TOOL_SERVERS or not connection or not (connection.get("config") or {}).get("enable") \
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

    async def complete(self, request, body, user):
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
                request, body, user, bypass_system_prompt=True,
            )
        except HTTPException as error:
            raise RuntimeError(str(error.detail)) from error
        if isinstance(response, Response) and response.status_code >= 400:
            raw = getattr(response, "body", b"")
            detail = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            raise RuntimeError(detail or f"Provider HTTP {response.status_code}")
        if isinstance(response, StreamingResponse):
            return response
        if isinstance(response, Response):
            response = json.loads(response.body)
        if response is None or (isinstance(response, dict) and response.get("error")):
            raise RuntimeError(str(response or "Provider returned no response"))
        return response

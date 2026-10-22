"""All imports of OWUI's internal APIs live behind this versioned boundary."""

from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
import json


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

    async def tools(self, request, ids, user, extra):
        from open_webui.utils.tools import get_tools

        return await get_tools(request, ids, user, extra)

    async def connect_mcp(self, request, server_id, user, extra):
        from open_webui.utils.tools import connect_mcp_server

        return await connect_mcp_server(request, server_id, user, extra)

    async def mcp_access(self, server_id, user):
        from open_webui.env import ENABLE_TOOL_SERVERS
        from open_webui.models.config import Config
        from open_webui.utils.access_control import has_connection_access

        connections = await Config.get("tool_server.connections", [])
        connection = next((c for c in connections if c.get("type") == "mcp"
                           and (c.get("info") or {}).get("id") == server_id), None)
        if not ENABLE_TOOL_SERVERS or not connection or not (connection.get("config") or {}).get("enable") \
                or not await has_connection_access(user, connection):
            raise ValueError("Required MCP connection unavailable or denied: " + server_id)

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

"""Disposable native proof for owner authorization and execution-user identity.

Checks the product adapter against real private resources and MCP transport.
Run with the project's OWUI 0.12.3 virtualenv; it uses its own SQLite database
and an ephemeral loopback MCP endpoint, never the running OWUI database.
"""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import sys
from uuid import uuid4

from native_environment import configure

configure("resource-access-gate")
os.environ["ENABLE_FORWARD_USER_INFO_HEADERS"] = "true"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lite_handoff_router.owui_adapter import OWUIAdapter
from lite_handoff_router.skills import READERS, scoped_reader


async def private_tools(adapter, request, ids, owner, actor):
    return await adapter.tools(request, ids, owner, {"__user__": actor.model_dump()})


async def private_mcp(request, server_id, owner, actor, extra):
    return await OWUIAdapter().connect_mcp(request, server_id, owner,
                                         {**extra, "__user__": actor.model_dump()})


async def main():
    from fastapi import FastAPI
    from starlette.requests import Request
    from mcp.server.fastmcp import FastMCP, Context
    import uvicorn
    from open_webui.models.users import Users
    from open_webui.models.models import Models, ModelForm
    from open_webui.models.tools import Tools, ToolForm
    from open_webui.models.skills import Skills, SkillForm
    from open_webui.models.config import Config
    from open_webui.models.access_grants import AccessGrants
    from open_webui.utils.tools import get_tools, get_tool_specs
    from open_webui.utils.plugin import load_tool_module_by_id

    suffix = uuid4().hex[:10]
    people = [await Users.insert_new_user("rg-" + role + suffix, role, role + suffix + "@gate.test", role="user")
              for role in ("owner", "one", "two")]
    owner, one, two = people
    tool_id, skill_id, model_id = (p + suffix for p in ("private-tool-", "private-skill-", "agent-"))
    content = '''from pydantic import BaseModel
import json
class Tools:
    class UserValves(BaseModel):
        region: str = "default"
    async def identity(self, __user__: dict) -> str:
        return json.dumps({"id":__user__["id"],"role":__user__["role"],"region":__user__["valves"].region})
'''
    module, _, _ = await load_tool_module_by_id(tool_id, content)
    await Tools.insert_new_tool(owner.id, ToolForm(id=tool_id, name="Private identity", content=content, meta={}),
                                get_tool_specs(module), module=module)
    for user in people:
        await Tools.update_user_valves_by_id_and_user_id(tool_id, user.id, {"region": user.name})
    await Skills.insert_new_skill(owner.id, SkillForm(id=skill_id, name="Private guide " + suffix, files=[
        {"path":"SKILL.md","content":"# Private guide\nRead the data."},
        {"path":"data.txt","content":"alpha\nneedle content\nomega"}]))
    row = await Models.insert_new_model(ModelForm(id=model_id, name="Private resources", params={},
                meta={"toolIds":[tool_id],"skillIds":[skill_id]}), owner.id)
    app = FastAPI()
    app.state.MODELS = {model_id:{"id":model_id,"owned_by":"openai","info":row.model_dump()}}
    request = Request({"type":"http","app":app,"headers":[]})
    adapter = OWUIAdapter()
    for user in (one, two):
        await AccessGrants.grant_access("model", model_id, "user", user.id, "read")
        await adapter.model(request, model_id, user)
        assert not await get_tools(request, [tool_id], user, {"__user__":user.model_dump()})
        try:
            await adapter.skill(skill_id, user)
        except Exception:
            pass
        else:
            raise AssertionError("Skill unexpectedly readable by execution user")
        own_tools = await private_tools(adapter, request, [tool_id], owner, user)
        refreshed = await adapter.refresh(own_tools["identity"]["callable"], {"__user__":owner.model_dump()})
        assert json.loads(await refreshed()) == {"id":user.id,"role":"user","region":user.name}
        metadata = {"chat_context":{}}
        builtins = await adapter.builtins(request, app.state.MODELS[model_id], {
            "__request__":request,"__user__":owner.model_dump(),"__metadata__":metadata,"__skill_ids__":[skill_id]})
        for name in READERS:
            tool = await scoped_reader(adapter, builtins[name], [skill_id], owner, [])
            call = await adapter.refresh(tool["callable"], {"__user__":user.model_dump()})
            args = {"view_skill":{},"read_skill_file":{"path":"data.txt"},"grep_skill_files":{"pattern":"needle"}}[name]
            result = json.loads(await call(id=skill_id, **args))
            assert "error" not in result, (name,result)

    mcp = FastMCP("Resource identity", streamable_http_path="/", stateless_http=True, json_response=True)

    @mcp.tool()
    async def identity(ctx: Context) -> str:
        headers = dict(ctx.request_context.request.headers)
        return json.dumps({"auth":headers.get("authorization"),"user":headers.get("x-openwebui-user-id")})

    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    mcp_app.router.lifespan_context = lifespan
    sock = socket.socket()
    sock.bind(("127.0.0.1",0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mcp_app, log_level="error", lifespan="on"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(.02)

    class OAuth:
        allowed = {one.id,two.id,owner.id}

        async def get_oauth_token(self, user_id, server_id):
            return {"access_token":"fixture-token-" + user_id} if user_id in self.allowed else None

    app.state.oauth_client_manager = OAuth()
    previous = await Config.get("tool_server.connections", [])
    try:
        await Config.upsert({"tool_server.connections":[{"type":"mcp","info":{"id":"resource-gate"},
            "url":f"http://127.0.0.1:{port}/","auth_type":"oauth_2.1","config":{"enable":True,
            "access_grants":[{"principal_type":"user","principal_id":owner.id,"permission":"read"}]}}]})
        for actor in (one,two):
            try:
                await adapter.mcp_access("resource-gate",actor)
            except ValueError:
                pass
            else:
                raise AssertionError("MCP unexpectedly directly accessible")
            client,specs = await private_mcp(request,"resource-gate",owner,actor,{})
            try:
                assert specs
                result = await client.call_tool("identity",function_args={})
                text = next(c["text"] for c in result if c.get("type") == "text")
                assert json.loads(text) == {"auth":"Bearer fixture-token-"+actor.id,"user":actor.id}, text
            finally:
                await client.disconnect()
        try:
            app.state.oauth_client_manager.allowed.remove(two.id)
            await private_mcp(request,"resource-gate",owner,two,{})
        except Exception as error:
            assert getattr(error,"status_code",None)==401
        else:
            raise AssertionError("Missing user OAuth unexpectedly succeeded")
        print(json.dumps({"passed":True,"users":2,"private_local_tool":True,"user_valves":True,
                          "skill_readers":3,"native_refresh":True,"mcp_transport":True,
                          "execution_oauth_and_headers":True,"database":"resource-access-gate"}))
    finally:
        await Config.upsert({"tool_server.connections":previous})
        server.should_exit = True
        await serving


if __name__ == "__main__":
    asyncio.run(main())

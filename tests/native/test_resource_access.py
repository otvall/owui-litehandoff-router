"""Native access checks with private resources and non-admin model owners."""

import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from lite_handoff_router.model_execution import prepare_model
from lite_handoff_router.owui_adapter import OWUIAdapter
from lite_handoff_router.registry import discover
from lite_handoff_router.runtime import begin, metadata_draft
from lite_handoff_router.skills import READERS


class NativeResourceAccessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.users import Users
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.tools import Tools, ToolForm
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.access_grants import AccessGrants
        from open_webui.utils.plugin import load_tool_module_by_id
        from open_webui.utils.tools import get_tool_specs

        self.suffix = uuid4().hex[:10]
        self.router_owner, self.agent_owner, self.one, self.two = [
            await Users.insert_new_user(role + self.suffix, role, role + self.suffix + "@resources.test", role="user")
            for role in ("router-owner", "agent-owner", "one", "two")]
        self.adapter = OWUIAdapter()
        self.app = FastAPI()
        self.app.state.redis = None
        self.app.state.MODELS = {"test-llm": {"id":"test-llm", "owned_by":"openai"}}
        self.request = Request({"type":"http", "app":self.app, "headers":[]})
        self.forms = {}
        self.model_ids = ["resource-router-" + self.suffix, "resource-agent-" + self.suffix]
        self.tool_ids = ["resource-tool-" + str(i) + self.suffix for i in range(2)]
        self.skill_ids = ["resource-skill-" + str(i) + self.suffix for i in range(2)]
        content = '''from pydantic import BaseModel
import json
class Tools:
    class UserValves(BaseModel):
        region: str = "default"
    async def identity(self, __user__: dict, __messages__: list, __files__: list) -> str:
        return json.dumps({"id":__user__["id"],"role":__user__["role"],"region":__user__["valves"].region,
                           "messages":__messages__,"files":__files__})
'''
        for i, owner in enumerate((self.router_owner, self.agent_owner)):
            tool_id, skill_id, model_id = self.tool_ids[i], self.skill_ids[i], self.model_ids[i]
            module, _, _ = await load_tool_module_by_id(tool_id, content)
            await Tools.insert_new_tool(owner.id, ToolForm(id=tool_id, name=tool_id, content=content, meta={}),
                                        get_tool_specs(module), module=module)
            for user in (owner, self.one, self.two):
                await Tools.update_user_valves_by_id_and_user_id(tool_id, user.id, {"region":user.name})
            await Skills.insert_new_skill(owner.id, SkillForm(id=skill_id, name=skill_id, files=[
                {"path":"SKILL.md","content":"# Own guide " + str(i)},
                {"path":"data.txt","content":"alpha\nneedle content\nomega"}]))
            form = ModelForm(id=model_id, name=model_id, params={"system":"PROMPT_"+str(i)},
                             meta={"toolIds":[tool_id],"skillIds":[skill_id]})
            self.forms[model_id] = form
            row = await Models.insert_new_model(form, owner.id)
            self.app.state.MODELS[model_id] = {"id":model_id,"owned_by":"openai","info":row.model_dump()}
            for actor in (self.one,self.two):
                await AccessGrants.grant_access("model", model_id, "user", actor.id, "read")

    async def prepare(self, actor=None, target=True, request=None):
        actor, request = actor or self.one, request or self.request
        router, agent = self.model_ids
        body = {"model":router,"messages":[{"role":"user","content":"User question"}]}
        metadata = {}
        registry = {agent:{"agent_id":agent,"skill_id":self.skill_ids[0],"name":"Route","description":"Route."}}
        state = begin(request, body, metadata, actor.id, registry, [], [self.tool_ids[0]], [self.skill_ids[0]])
        return await prepare_model(self.adapter, request, body, actor, metadata_draft(metadata), state,
                                   "test-llm", {}, target=agent if target else None)

    async def assert_private(self, actor):
        from open_webui.utils.tools import get_tools
        from open_webui.routers.skills import get_skill_by_id

        for identifier in self.tool_ids:
            self.assertEqual(await get_tools(self.request, [identifier], actor, {"__user__":actor.model_dump()}), {})
        for identifier in self.skill_ids:
            with self.assertRaises(Exception) as denied:
                await get_skill_by_id(identifier, user=actor, db=None)
            self.assertIn(denied.exception.status_code, (401,403,404))

    async def set_owner(self, model_id, owner_id):
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.models import Model

        async with get_async_db_context() as session:
            row = await session.get(Model,model_id)
            row.user_id = owner_id
            await session.commit()

    async def test_private_router_and_worker_resources_keep_execution_identity_after_refresh(self):
        from starlette.requests import Request

        async def run(actor, target):
            request = Request({"type":"http","app":self.app,"headers":[]})
            body, tools = await self.prepare(actor, target, request)
            refreshed = await self.adapter.refresh(tools["identity"]["callable"], {
                "__user__":self.router_owner.model_dump(), "__messages__":[{"role":"system","content":"FOREIGN"}],
                "__files__":[{"id":"fresh-file"}]})
            value = json.loads(await refreshed())
            self.assertEqual((value["id"],value["role"],value["region"]),(actor.id,"user",actor.name))
            self.assertEqual(value["messages"],body["messages"])
            self.assertEqual(value["files"],[{"id":"fresh-file"}])
            self.assertNotIn("FOREIGN",str(value))

        for actor in (self.one,self.two):
            await self.assert_private(actor)
        await asyncio.gather(*(run(actor,target) for actor in (self.one,self.two) for target in (False,True)))
        for actor in (self.one,self.two):
            await self.assert_private(actor)

    async def test_readers_use_owner_but_never_expand_scope(self):
        from open_webui.models.access_grants import AccessGrants

        await AccessGrants.grant_access("skill",self.skill_ids[0],"user",self.agent_owner.id,"read")
        _, tools = await self.prepare()
        arguments = {"view_skill":{},"read_skill_file":{"path":"data.txt","max_chars":5},
                     "grep_skill_files":{"pattern":"needle"}}
        for name in READERS:
            refreshed = await self.adapter.refresh(tools[name]["callable"], {"__user__":self.one.model_dump()})
            result = json.loads(await refreshed(id=self.skill_ids[1], **arguments[name]))
            self.assertNotIn("error",result)
            with self.assertRaisesRegex(ValueError,"model scope"):
                await refreshed(id=self.skill_ids[0], **arguments[name])

    async def test_invalid_owner_user_valves_do_not_affect_execution_user(self):
        from open_webui.models.tools import Tools

        await Tools.update_user_valves_by_id_and_user_id(self.tool_ids[1],self.agent_owner.id,{"region":{"invalid":"owner"}})
        _, tools = await self.prepare()
        value=json.loads(await tools["identity"]["callable"]())
        self.assertEqual((value["id"],value["region"]),(self.one.id,self.one.name))

    async def test_registry_checks_actor_models_and_router_owner_skills(self):
        from open_webui.models.access_grants import AccessGrants

        router, agent = self.model_ids
        catalog = {agent:self.skill_ids[0]}
        entries,_ = await discover(self.adapter,self.request,self.one,catalog,router)
        self.assertEqual(set(entries),{agent})
        await AccessGrants.revoke_access("model",agent,"user",self.one.id,"read")
        entries,_ = await discover(self.adapter,self.request,self.one,catalog,router)
        self.assertEqual(entries,{})
        entries,_ = await discover(self.adapter,self.request,self.two,catalog,router)
        self.assertEqual(set(entries),{agent})
        # Agent owner cannot authorize a routing Skill on behalf of Router.
        entries,_ = await discover(self.adapter,self.request,self.two,{agent:self.skill_ids[1]},router)
        self.assertEqual(entries,{})

    async def test_detachment_and_owner_change_invalidate_prepared_calls(self):
        from open_webui.models.models import Models

        model_id = self.model_ids[1]
        _, tools = await self.prepare()
        form = self.forms[model_id].model_copy(deep=True)
        form.meta = type(form.meta)(toolIds=[],skillIds=[])
        await Models.update_model_by_id(model_id,form,user_id=self.agent_owner.id)
        for name,args in (("identity",{}),("view_skill",{"id":self.skill_ids[1]})):
            with self.assertRaisesRegex(ValueError,"detached"):
                await tools[name]["callable"](**args)
        await Models.update_model_by_id(model_id,self.forms[model_id],user_id=self.router_owner.id)
        await self.set_owner(model_id,self.router_owner.id)
        with self.assertRaisesRegex(ValueError,"owner changed"):
            await tools["identity"]["callable"]()

    async def test_resource_revocation_missing_owner_and_global_disable_fail_closed(self):
        from open_webui.models.models import Models
        from open_webui.models.access_grants import AccessGrants
        from open_webui.utils import tools as native_tools
        from open_webui import env

        form = self.forms[self.model_ids[1]].model_copy(deep=True)
        form.meta = type(form.meta)(toolIds=[self.tool_ids[0]],skillIds=[])
        await Models.update_model_by_id(form.id,form,user_id=self.agent_owner.id)
        with self.assertRaisesRegex(ValueError,"unavailable"):
            await self.prepare()
        await AccessGrants.grant_access("tool",self.tool_ids[0],"user",self.agent_owner.id,"read")
        _, prepared = await self.prepare()
        await AccessGrants.revoke_access("tool",self.tool_ids[0],"user",self.agent_owner.id,"read")
        with self.assertRaisesRegex(ValueError,"denied"):
            await prepared["identity"]["callable"]()
        await Models.update_model_by_id(form.id,self.forms[form.id],user_id=self.agent_owner.id)
        with patch.object(native_tools,"ENABLE_TOOLS",False), patch.object(env,"ENABLE_TOOLS",False):
            with self.assertRaisesRegex(ValueError,"unavailable"):
                await self.prepare()
        await Models.update_model_by_id(form.id,self.forms[form.id],user_id="missing-owner-"+self.suffix)
        await self.set_owner(form.id,"missing-owner-"+self.suffix)
        with self.assertRaisesRegex(ValueError,"owner is unavailable"):
            await self.prepare()

    async def test_model_revocation_blocks_existing_tool_and_client_owner_is_ignored(self):
        from open_webui.models.access_grants import AccessGrants

        self.request.state.metadata = {"resource_owner":self.one.model_dump(),"owner_id":self.one.id}
        _, tools = await self.prepare()
        await AccessGrants.revoke_access("model",self.model_ids[1],"user",self.one.id,"read")
        with self.assertRaises(Exception):
            await tools["identity"]["callable"]()

    async def test_resource_owner_does_not_grant_access_to_chats_or_ordinary_files(self):
        import tempfile
        from pathlib import Path
        from open_webui.models.files import Files, FileForm
        from open_webui.models.chats import Chats, ChatForm

        with tempfile.TemporaryDirectory(prefix="lhr-resource-files-") as directory:
            for owner, denied in ((self.one,self.agent_owner),(self.agent_owner,self.one)):
                path=Path(directory)/(owner.id+".txt")
                path.write_text("Private ordinary file")
                identifier=str(uuid4())
                await Files.insert_new_file(owner.id,FileForm(id=identifier,filename=path.name,path=str(path)))
                self.assertEqual((await self.adapter.file(identifier,owner))["id"],identifier)
                with self.assertRaisesRegex(ValueError,"unavailable or denied"):
                    await self.adapter.file(identifier,denied)
        identifier=str(uuid4())
        await Chats.insert_new_chat(identifier,self.one.id,ChatForm(chat={"title":"Private execution"}),
                                   internal_meta={"internal":True,"type":"subagent"})
        self.assertIsNotNone(await Chats.get_accessible_chat_by_id(identifier,self.one))
        self.assertIsNone(await Chats.get_accessible_chat_by_id(identifier,self.agent_owner))

    async def test_private_server_connections_use_actor_auth_not_owner_auth(self):
        from open_webui.models.config import Config
        from open_webui.utils import tools as native

        server_id = "private-server-"+self.suffix
        connection = {"type":"mcp","info":{"id":server_id},"url":"http://127.0.0.1/unused",
                      "auth_type":"oauth_2.1","config":{"enable":True,"access_grants":[{
                          "principal_type":"user","principal_id":self.agent_owner.id,"permission":"read"}]}}
        previous = await Config.get("tool_server.connections",[])
        seen=[]

        class Client:
            def __init__(self,**kwargs): pass
            async def connect(self,**kwargs): seen.append(kwargs["headers"])
            async def list_tool_specs(self): return [{"name":"identity"}]
            async def disconnect(self): pass

        async def token(user_id,server_id):
            return {"access_token":"fixture-"+user_id} if user_id != self.two.id else None

        oauth = AsyncMock(side_effect=token)
        self.app.state.oauth_client_manager = SimpleNamespace(get_oauth_token=oauth)
        try:
            await Config.upsert({"tool_server.connections":[connection]})
            with patch.object(native,"MCPClient",Client), patch.object(native,"ENABLE_FORWARD_USER_INFO_HEADERS",True):
                with self.assertRaisesRegex(ValueError,"denied"):
                    await self.adapter.mcp_access(server_id,self.one)
                await self.adapter.connect_mcp(self.request,server_id,self.agent_owner,{"__user__":self.one.model_dump()})
                self.assertEqual(seen[0]["Authorization"],"Bearer fixture-"+self.one.id)
                self.assertIn(self.one.id,seen[0].values())
                self.assertNotIn(self.agent_owner.id,seen[0].values())
                with self.assertRaises(Exception) as missing:
                    await self.adapter.connect_mcp(self.request,server_id,self.agent_owner,{"__user__":self.two.model_dump()})
                self.assertEqual(missing.exception.status_code,401)
                self.assertEqual([call.args[0] for call in oauth.await_args_list],[self.one.id,self.two.id])

                connection = {**connection,"type":"openapi"}
                await Config.upsert({"tool_server.connections":[connection]})
                data = {"id":server_id,"idx":0,"url":connection["url"],"specs":[{
                    "name":"identity","parameters":{"type":"object","properties":{}}}]}
                execute = AsyncMock(return_value="remote result")
                with patch.object(native,"get_tool_servers",AsyncMock(return_value=[data])), \
                     patch.object(native,"execute_tool_server",execute):
                    tools = await self.adapter.tools(self.request,["server:"+server_id],self.agent_owner,
                                                     {"__user__":self.one.model_dump()})
                    self.assertEqual(await tools["identity"]["callable"](),"remote result")
                    self.assertEqual(execute.call_args.kwargs["headers"]["Authorization"],"Bearer fixture-"+self.one.id)
                    connection["config"]={"enable":True,"access_grants":[]}
                    await Config.upsert({"tool_server.connections":[connection]})
                    with self.assertRaisesRegex(ValueError,"denied"):
                        await tools["identity"]["callable"]()
        finally:
            await Config.upsert({"tool_server.connections":previous})

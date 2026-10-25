"""Native terminal integration contracts on unchanged OWUI 0.11.1."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from lite_handoff_router.owui_adapter import OWUIAdapter


class NativeTerminalMetadataTest(unittest.TestCase):
    def test_workspace_model_roundtrip_preserves_optional_binding(self):
        from open_webui.models.models import ModelMeta

        value = {"toolIds": ["lookup"], "skillIds": ["guide"],
                 "capabilities": {"terminal": True},
                 "lite_handoff": {"terminal_id": "internal-analysis"}}
        meta = ModelMeta(**value)
        self.assertEqual(ModelMeta(**meta.model_dump()).model_dump()["lite_handoff"], value["lite_handoff"])
        self.assertEqual(meta.model_dump()["toolIds"], ["lookup"])


class NativeTerminalAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.config import Config
        from open_webui.models.users import Users

        suffix=uuid4().hex[:10]
        self.owner,self.one,self.two=[await Users.insert_new_user(n+suffix,n,n+suffix+"@terminal.test",role="user")
                                      for n in ("owner","one","two")]
        self.adapter=OWUIAdapter()
        self.app=FastAPI()
        self.app.state.redis=None
        self.request=Request({"type":"http","app":self.app,"headers":[(b"cookie",b"fixture=one")]})
        self.request.state.token=SimpleNamespace(credentials="session-one")
        self.identifier="terminal-"+suffix
        self.connection={"id":self.identifier,"url":"http://127.0.0.1/unused","enabled":True,
            "auth_type":"bearer","key":"server-key","config":{"access_grants":[{
                "principal_type":"user","principal_id":self.owner.id,"permission":"read"}]}}
        self.data={"id":self.identifier,"url":self.connection["url"],"specs":[
            {"name":n,"description":"Terminal operation","parameters":{"type":"object","properties":{}}}
            for n in ("run_command","read_file","display_file","read_user_terminal","send_user_terminal_input")]}
        self.previous=await Config.get("terminal_server.connections",[])
        await Config.upsert({"terminal_server.connections":[self.connection]})
        self.patches=[patch("open_webui.utils.tools.get_terminal_servers",AsyncMock(side_effect=lambda r:[self.data])),
                      patch("open_webui.utils.tools.get_terminal_cwd",AsyncMock(return_value="/workspace")),
                      patch("open_webui.utils.tools.get_terminal_system_prompt",AsyncMock(return_value="TERMINAL_GUIDE"))]
        for p in self.patches: p.start()

    async def asyncTearDown(self):
        from open_webui.models.config import Config
        for p in reversed(self.patches): p.stop()
        await Config.upsert({"terminal_server.connections":self.previous})

    async def save(self):
        from open_webui.models.config import Config
        await Config.upsert({"terminal_server.connections":[self.connection]})

    async def load(self,actor=None,request=None,chat_id=None):
        actor=actor or self.one
        return await self.adapter.terminal_tools(request or self.request,self.identifier,self.owner,{
            "__user__":actor.model_dump(),"__metadata__":{"chat_id":chat_id or str(uuid4()),"message_id":"message"}})

    async def test_owner_acl_groups_and_server_only_specs(self):
        from open_webui.models.groups import Groups,GroupForm
        from open_webui.utils import tools
        from open_webui.utils import middleware

        for actor in (self.one,self.two):
            with self.assertRaisesRegex(ValueError,"denied"):
                await self.adapter.terminal_access(self.request,self.identifier,actor)
        group=await Groups.insert_new_group(self.owner.id,GroupForm(name="terminal-owners-"+self.identifier,description="fixture"))
        await Groups.add_users_to_group(group.id,[self.owner.id])
        self.connection["config"]["access_grants"]=[{"principal_type":"group","principal_id":group.id,"permission":"read"}]
        await self.save()
        loaded,prompt=await self.load()
        self.assertEqual(set(loaded),{"run_command","read_file","display_file"})
        self.assertEqual(prompt,"TERMINAL_GUIDE")
        self.assertIn("/workspace",loaded["run_command"]["spec"]["description"])
        self.assertEqual(loaded["run_command"]["tool_id"],"terminal:"+self.identifier)
        self.assertEqual(loaded["run_command"]["type"],"terminal")
        with patch.object(tools,"ENABLE_PLUGINS",False):
            with self.assertRaisesRegex(ValueError,"unavailable"):
                await self.load()
        self.connection["enabled"]=False
        await self.save()
        with self.assertRaisesRegex(ValueError,"unavailable"):
            await self.load()

    async def test_missing_specs_context_and_empty_permitted_set_fail(self):
        from open_webui.models.config import Config
        from open_webui.utils import tools
        from open_webui.utils import middleware

        with patch.object(tools,"get_terminal_servers",AsyncMock(return_value=[])):
            with self.assertRaisesRegex(ValueError,"no available"):
                await self.load()
        self.data["specs"]=[s for s in self.data["specs"] if s["name"].endswith("user_terminal")]
        with self.assertRaisesRegex(ValueError,"no permitted"):
            await self.load()
        self.connection.update(server_type="orchestrator",policy_id="fixture")
        self.connection["config"]["contexts"]={"chat":False}
        await self.save()
        with self.assertRaisesRegex(ValueError,"chat context"):
            await self.load()
        self.connection["config"]["contexts"]={"chat":{"context_id":"chat_id"}}
        await self.save()
        with self.assertRaisesRegex(ValueError,"saved chat"):
            await self.load(chat_id="local:temporary")
        await Config.upsert({"terminal_server.connections":[]})
        with self.assertRaisesRegex(ValueError,"unavailable"):
            await self.load()

    async def test_actor_credentials_context_and_native_file_result(self):
        from starlette.requests import Request
        from open_webui.utils import tools
        from open_webui.utils import middleware
        from open_webui.utils.middleware import build_terminal_file_tool_result

        self.connection.update(server_type="orchestrator",policy_id="fixture")
        self.connection["config"]["contexts"]={"chat":{"context_id":"chat_id"}}
        self.connection["forward_cookies"]=True
        self.connection["headers"]={"x-user-id":"forged","X-Session-Id":"forged","X-Terminal-Context-Id":"forged"}
        seen=[]
        async def transport(**kwargs):
            seen.append(kwargs)
            return {"path":"/workspace/result.txt","exists":True},{}
        for auth in ("bearer","session","system_oauth","none"):
            self.connection["auth_type"]=auth
            await self.save()
            async def run(actor):
                request=Request({"type":"http","app":self.app,"headers":[(b"cookie",("fixture="+actor.id).encode())]})
                request.state.token=SimpleNamespace(credentials="session-"+actor.id)
                chat_id=str(uuid4())
                loaded,_=await self.load(actor,request,chat_id)
                await loaded["run_command"]["callable"](command="marker")
                result=await loaded["display_file"]["callable"](path="/workspace/result.txt",page=2,inline=True)
                native_file=build_terminal_file_tool_result("display_file",{"inline":True},result,loaded["display_file"],{"chat_id":chat_id})
                self.assertEqual(native_file["terminal_id"],self.identifier)
                self.assertEqual(native_file["source"],"open_terminal")
                calls=[c for c in seen if c["headers"]["X-User-Id"]==actor.id][-2:]
                for call in calls:
                    self.assertEqual(call["headers"]["X-Session-Id"],chat_id)
                    self.assertEqual(call["headers"]["X-Terminal-Context-Id"],"chat:"+chat_id)
                    self.assertEqual(call["cookies"],{"fixture":actor.id} if auth in {"session","system_oauth"} else {})
                    expected={"bearer":"Bearer server-key","session":"Bearer session-"+actor.id,
                              "system_oauth":"Bearer oauth-"+actor.id,"none":None}[auth]
                    self.assertEqual(call["headers"].get("Authorization"),expected)
                self.assertNotIn("page",calls[-1]["params"])
            oauth=AsyncMock(side_effect=lambda r,u:{"access_token":"oauth-"+u.id})
            with patch.object(tools,"execute_tool_server",side_effect=transport), patch.object(middleware,"get_system_oauth_token",oauth):
                await asyncio.gather(run(self.one),run(self.two))
                self.assertTrue(all(args.args[1].id in {self.one.id,self.two.id} for args in oauth.call_args_list))
        for actor in (self.one,self.two):
            with self.assertRaisesRegex(ValueError,"denied"):
                await self.adapter.terminal_access(self.request,self.identifier,actor)

    async def test_missing_auth_revocation_and_removed_operation_block_transport(self):
        from open_webui.utils import tools
        from open_webui.utils import middleware

        self.connection["auth_type"]="session"
        await self.save()
        del self.request.state.token
        with self.assertRaisesRegex(ValueError,"session authorization"):
            await self.load()
        self.connection["auth_type"]="system_oauth"
        await self.save()
        with patch.object(middleware,"get_system_oauth_token",AsyncMock(return_value=None)):
            with self.assertRaisesRegex(ValueError,"OAuth authorization"):
                await self.load()
        self.connection["auth_type"]="none"
        await self.save()
        loaded,_=await self.load()
        transport=AsyncMock()
        with patch.object(tools,"execute_tool_server",transport):
            self.data["specs"]=[s for s in self.data["specs"] if s["name"]!="run_command"]
            with self.assertRaisesRegex(ValueError,"no longer available"):
                await loaded["run_command"]["callable"]()
            self.connection["config"]["access_grants"]=[]
            await self.save()
            with self.assertRaisesRegex(ValueError,"denied"):
                await loaded["read_file"]["callable"]()
            transport.assert_not_awaited()

    async def prepare_agent(self,identifier=None,chat_id=None):
        from open_webui.models.models import Models,ModelForm
        from open_webui.models.access_grants import AccessGrants
        from lite_handoff_router.model_execution import prepare_model
        from lite_handoff_router.runtime import begin

        identifier=identifier or "agent-"+uuid4().hex[:12]
        form=ModelForm(id=identifier,name=identifier,params={"system":"AGENT_PROMPT"},
                       meta={"lite_handoff":{"terminal_id":self.identifier}})
        row=await Models.insert_new_model(form,self.owner.id)
        await AccessGrants.grant_access("model",identifier,"user",self.one.id,"read")
        self.app.state.MODELS={identifier:{"id":identifier,"owned_by":"openai","info":row.model_dump()}}
        metadata={"chat_id":chat_id or str(uuid4()),"message_id":"message"}
        body={"model":"router","messages":[{"role":"user","content":"test"}]}
        state=begin(self.request,body,metadata,self.one.id,{},[],[],[])
        result=await prepare_model(self.adapter,self.request,body,self.one,metadata,state,"llm",{},target=identifier)
        return identifier,form,result

    async def test_native_refresh_preserves_terminal_scope_and_actor(self):
        from open_webui.models.models import Models
        from open_webui.utils import tools
        from open_webui.utils import middleware

        identifier,form,(draft,loaded)=await self.prepare_agent()
        refreshed=await self.adapter.refresh(loaded["run_command"]["callable"],{
            "__user__":self.owner.model_dump(),"__metadata__":{"chat_id":"foreign","terminal_id":"other"}})
        transport=AsyncMock(return_value=({"stdout":"done"},{}))
        with patch.object(tools,"execute_tool_server",transport):
            await refreshed(command="marker")
            self.assertEqual(transport.call_args.kwargs["headers"]["X-User-Id"],self.one.id)
            self.assertEqual(transport.call_args.kwargs["headers"]["X-Session-Id"],draft["metadata"]["chat_id"])
            form.meta.lite_handoff={"terminal_id":"other"}
            await Models.update_model_by_id(identifier,form)
            with self.assertRaisesRegex(ValueError,"detached"):
                await refreshed(command="must-not-execute")
            self.assertEqual(transport.await_count,1)

    async def test_cancel_terminal_wait_does_not_retry_or_stop_shared_backend(self):
        from open_webui.utils import tools
        from open_webui.utils import middleware

        _,_,(_,loaded)=await self.prepare_agent()
        started,waiting,cancelled=asyncio.Event(),asyncio.Event(),asyncio.Event()
        async def transport(**kwargs):
            started.set()
            try:
                await waiting.wait()
                return ({"stdout":"late"},{})
            finally:
                cancelled.set()
        transport_mock=AsyncMock(side_effect=transport)
        with patch.object(tools,"execute_tool_server",transport_mock):
            task=asyncio.create_task(loaded["run_command"]["callable"](command="pending"))
            await asyncio.wait_for(started.wait(),2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertTrue(cancelled.is_set())
            waiting.set()
            await asyncio.sleep(0)
            self.assertEqual(transport_mock.await_count,1)
            self.assertEqual(await self.adapter.terminal_access(self.request,self.identifier,self.owner),self.connection)

    async def test_model_binding_default_off_parent_override_inlet_and_owner_change(self):
        from open_webui.models.models import Models,Model
        from open_webui.internal.db import get_async_db_context
        from open_webui.utils import tools
        from lite_handoff_router.model_execution import prepare_model
        from lite_handoff_router.runtime import begin,metadata_draft

        identifier,form,(draft,loaded)=await self.prepare_agent()
        persisted=await Models.get_model_by_id(identifier)
        self.assertEqual(persisted.meta.model_dump()['lite_handoff']['terminal_id'],self.identifier)
        chat_id=draft['metadata']['chat_id']
        async def prepare():
            metadata={'chat_id':chat_id,'message_id':'message','terminal_id':'parent-other',
                      'terminal_skills':['foreign']}
            body={'model':identifier,'messages':[{'role':'user','content':'Question'}],'terminal_id':'parent-other'}
            state=begin(self.request,body,metadata,self.one.id,{},[],[],[])
            return await prepare_model(self.adapter,self.request,body,self.one,metadata_draft(metadata),state,'unused',{},target=identifier)
        prepared,loaded=await prepare()
        self.assertEqual(prepared['metadata']['terminal_id'],self.identifier)
        self.assertNotIn('terminal_skills',prepared['metadata'])
        self.assertNotIn('terminal_id',prepared)
        for binding in (None,{}, {'terminal_id':None},{'terminal_id':''}):
            if binding is None:
                form.meta=type(form.meta)()
            else:
                form.meta.lite_handoff=binding
            await Models.update_model_by_id(identifier,form)
            ordinary,ordinary_tools=await prepare()
            self.assertNotIn('terminal_id',ordinary['metadata'])
            self.assertNotIn('run_command',ordinary_tools)
        form.meta.lite_handoff={'terminal_id':self.identifier}
        form.meta.capabilities={'terminal':False}
        await Models.update_model_by_id(identifier,form)
        with self.assertRaisesRegex(ValueError,'disabled Terminal capability'): await prepare()
        form.meta.capabilities={'terminal':True}
        await Models.update_model_by_id(identifier,form)
        async def inlet(request,body,model,extra):
            body['metadata']['tools']['run_command']['callable']=AsyncMock()
            return body
        with patch.object(self.adapter,'inlets',side_effect=inlet):
            with self.assertRaisesRegex(ValueError,'protected Tool'): await prepare()
        _,loaded=await prepare()
        async with get_async_db_context() as db:
            row=await db.get(Model,identifier); row.user_id=self.two.id; await db.commit()
        transport=AsyncMock()
        with patch.object(tools,'execute_tool_server',transport):
            with self.assertRaisesRegex(ValueError,'owner changed'):
                await loaded['run_command']['callable'](command='must-not-run')
            transport.assert_not_awaited()

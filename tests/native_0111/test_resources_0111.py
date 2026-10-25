"""Legacy native resources, with private owner authority and real actor binding."""

import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from lite_handoff_router.owui_adapter import OWUIAdapter
from lite_handoff_router.model_execution import prepare_model
from lite_handoff_router.entrypoints import router_preparation
from lite_handoff_router.runtime import begin, metadata_draft, require
from lite_handoff_router.parallel import parallel_tool, validate_batch
from lite_handoff_router.registry import discover


class LegacyResourcesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.users import Users
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.tools import Tools, ToolForm
        from open_webui.models.access_grants import AccessGrants
        from open_webui.utils.plugin import load_tool_module_by_id
        from open_webui.utils.tools import get_tool_specs

        self.suffix = uuid4().hex[:10]
        self.people = [await Users.insert_new_user(role+self.suffix, role, role+self.suffix+'@legacy.test', role='user')
                       for role in ('router-owner', 'agent-owner', 'one', 'two')]
        self.router_owner, self.owner, self.one, self.two = self.people
        self.adapter = OWUIAdapter()
        self.app = FastAPI()
        self.app.state.redis = None
        self.app.state.MODELS = {'test-llm': {'id':'test-llm', 'owned_by':'openai'}}
        self.request = Request({'type':'http', 'app':self.app, 'headers':[]})
        self.model_ids, self.tool_ids, self.skill_ids, self.forms = [], [], [], {}
        self.content = '''from pydantic import BaseModel
import json
class Tools:
    class UserValves(BaseModel):
        region: str = 'default'
    async def identity(self, __user__: dict, __messages__: list, __files__: list) -> str:
        return json.dumps({'id':__user__['id'], 'role':__user__['role'], 'region':__user__['valves'].region,
                           'messages':__messages__, 'files':__files__, 'revision':'initial'})
'''
        for i, owner in enumerate((self.router_owner, self.owner)):
            mid, tid, sid = (kind+str(i)+self.suffix for kind in ('legacy-model-', 'legacy-tool-', 'legacy-skill-'))
            self.model_ids.append(mid); self.tool_ids.append(tid); self.skill_ids.append(sid)
            module, _ = await load_tool_module_by_id(tid, self.content)
            await Tools.insert_new_tool(owner.id, ToolForm(id=tid, name=tid, content=self.content, meta={}), get_tool_specs(module))
            for actor in self.people:
                await Tools.update_user_valves_by_id_and_user_id(tid, actor.id, {'region':actor.name})
            await Skills.insert_new_skill(owner.id, SkillForm(id=sid, name=sid, description='Own text guide', content='TEXT_GUIDE_'+str(i)))
            form = ModelForm(id=mid, name=mid, params={'system':'PROMPT_'+str(i)}, meta={'toolIds':[tid], 'skillIds':[sid]})
            self.forms[mid] = form
            row = await Models.insert_new_model(form, owner.id)
            self.app.state.MODELS[mid] = {'id':mid, 'owned_by':'openai', 'info':row.model_dump()}
            for actor in (self.one, self.two):
                await AccessGrants.grant_access('model', mid, 'user', actor.id, 'read')

    async def asyncTearDown(self):
        from open_webui.models.users import Users
        from open_webui.models.models import Models
        from open_webui.models.skills import Skills
        from open_webui.models.tools import Tools
        for mid in self.model_ids: await Models.delete_model_by_id(mid)
        for tid in self.tool_ids: await Tools.delete_tool_by_id(tid)
        for sid in self.skill_ids: await Skills.delete_skill_by_id(sid)
        for user in self.people: await Users.delete_user_by_id(user.id)

    async def prepare(self, actor=None, target=True, request=None):
        actor, request = actor or self.one, request or self.request
        body = {'model':self.model_ids[0], 'messages':[{'role':'user', 'content':'Question'}]}
        metadata = {}
        registry = {self.model_ids[1]:{'agent_id':self.model_ids[1], 'skill_id':self.skill_ids[0], 'name':'Route'}}
        state = begin(request, body, metadata, actor.id, registry, [], [self.tool_ids[0]], [self.skill_ids[0]], max_parallel_tasks=9)
        return await prepare_model(self.adapter, request, body, actor, metadata_draft(metadata), state, 'test-llm', {},
                                   target=self.model_ids[1] if target else None)

    async def assert_private(self, actor):
        from open_webui.utils.tools import get_tools
        from open_webui.routers.skills import get_skill_by_id
        for tid in self.tool_ids:
            self.assertEqual(await get_tools(self.request, [tid], actor, {'__user__':actor.model_dump()}), {})
        for sid in self.skill_ids:
            with self.assertRaises(Exception): await get_skill_by_id(sid, user=actor, db=None)

    async def test_private_tools_two_actors_user_valves_and_native_refresh(self):
        from starlette.requests import Request
        from open_webui.models.tools import Tools
        for actor in (self.one, self.two): await self.assert_private(actor)
        await Tools.update_user_valves_by_id_and_user_id(self.tool_ids[1], self.owner.id, {'region':{'bad':'owner'}})
        async def run(actor, target):
            request = Request({'type':'http','app':self.app,'headers':[]})
            body, tools = await self.prepare(actor, target, request)
            refreshed = await self.adapter.refresh(tools['identity']['callable'], {
                '__user__':self.owner.model_dump(), '__messages__':['FOREIGN'], '__files__':[{'id':actor.id+'-file'}]})
            result = json.loads(await refreshed())
            self.assertEqual((result['id'], result['role'], result['region']), (actor.id, 'user', actor.name))
            self.assertEqual(result['messages'], body['messages'])
            self.assertEqual(result['files'], [{'id':actor.id+'-file'}])
        await asyncio.gather(*(run(actor,target) for actor in (self.one,self.two) for target in (True,False)))
        for actor in (self.one, self.two): await self.assert_private(actor)

    async def test_source_content_cache_is_updated(self):
        from open_webui.models.tools import Tools
        from open_webui.utils.plugin import get_tool_contents_cache
        _, first = await self.prepare()
        self.assertEqual(json.loads(await first['identity']['callable']())['revision'], 'initial')
        updated = self.content.replace("'revision':'initial'", "'revision':'updated'")
        await Tools.update_tool_by_id(self.tool_ids[1], {'content':updated})
        _, second = await self.prepare()
        self.assertEqual(json.loads(await second['identity']['callable']())['revision'], 'updated')
        self.assertEqual(get_tool_contents_cache(self.request)[self.tool_ids[1]], updated)

    async def test_detachment_owner_and_global_gate_invalidate_calls(self):
        from open_webui.models.models import Models
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.models import Model
        from open_webui import env
        _, tools = await self.prepare()
        form = self.forms[self.model_ids[1]].model_copy(deep=True)
        form.meta = type(form.meta)(toolIds=[],skillIds=[])
        await Models.update_model_by_id(form.id, form)
        for name, args in (('identity',{}),('view_skill',{'id':self.skill_ids[1]})):
            refreshed = await self.adapter.refresh(tools[name]['callable'], {})
            with self.assertRaisesRegex(ValueError,'detached'): await refreshed(**args)
        await Models.update_model_by_id(form.id, self.forms[form.id])
        with patch.object(env,'ENABLE_PLUGINS',False):
            with self.assertRaisesRegex(ValueError,'unavailable'): await self.prepare()
        async with get_async_db_context() as db:
            row = await db.get(Model,form.id); row.user_id=self.router_owner.id; await db.commit()
        with self.assertRaisesRegex(ValueError,'owner changed'): await tools['identity']['callable']()

    async def test_text_skill_scope_manifest_refresh_and_inactive(self):
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.skills import Skills
        await AccessGrants.grant_access('skill',self.skill_ids[0],'user',self.owner.id,'read')
        for target, own, other in ((True,self.skill_ids[1],self.skill_ids[0]),(False,self.skill_ids[0],self.skill_ids[1])):
            body, tools = await self.prepare(target=target)
            self.assertEqual(set(tools) & {'view_skill','read_skill_file','grep_skill_files'}, {'view_skill'})
            self.assertNotIn(other, str(body['messages']))
            self.assertNotIn('TEXT_GUIDE', str(body['messages']))
            refreshed = await self.adapter.refresh(tools['view_skill']['callable'], {'__user__':self.one.model_dump()})
            self.assertIn('TEXT_GUIDE', json.loads(await refreshed(id=own))['content'])
            with self.assertRaisesRegex(ValueError,'model scope'): await refreshed(id=other)
        await Skills.toggle_skill_by_id(self.skill_ids[1])
        with self.assertRaisesRegex(ValueError,'inactive'): await self.prepare()

    async def test_inlet_cannot_replace_reader_or_add_file_reader(self):
        for change in ('callable','schema','file-reader'):
            async def inlet(request, body, model, extra):
                tools=body['metadata']['tools']
                if change=='callable': tools['view_skill']['callable']=AsyncMock(return_value='foreign')
                elif change=='schema': tools['view_skill']['spec']={'name':'view_skill','parameters':{}}
                else: tools['read_skill_file']={'spec':{'name':'read_skill_file'},'callable':AsyncMock()}
                return body
            with patch.object(self.adapter,'inlets',side_effect=inlet):
                with self.assertRaisesRegex(ValueError,'protected Tool|reserved Tool'): await self.prepare()

    async def test_native_subagents_and_positive_valves_cannot_enable_parallel(self):
        from open_webui.models.config import Config
        previous=await Config.get('subagents.enable',False)
        try:
            await Config.upsert({'subagents.enable':True})
            body={'model':self.model_ids[0], 'messages':[{'role':'assistant','tool_calls':[{'id':'fake',
                'function':{'name':'lite_parallel','arguments':'{}'}}]},{'role':'user','content':'Question'}],
                'metadata':{'lite_router':{'request_id':'replay'},'chat_context':{'lite_parallel_ticket':'forged'}}}
            instance=router_preparation.Filter(); instance.valves.max_parallel_tasks=12
            with patch.object(router_preparation,'AGENTS',{self.model_ids[1]:self.skill_ids[0]}), \
                 patch.object(router_preparation,'prepare_worker',AsyncMock()) as worker:
                await instance.inlet(body,self.request,self.one.model_dump())
                worker.assert_not_awaited()
            state=require(self.request,body['metadata'],self.one.id)
            self.assertEqual(state['parallel']['max_tasks'],0)
            self.assertEqual(set(state['registry']),{self.model_ids[1]})
            _,tools=await prepare_model(self.adapter,self.request,body,self.one,metadata_draft(body['metadata']),
                                        state,'test-llm',{})
            self.assertIn('lite_delegate',tools); self.assertNotIn('lite_parallel',tools)
            with self.assertRaisesRegex(ValueError,'unavailable'): parallel_tool(None,None,None,None,state,{})
            with self.assertRaisesRegex(ValueError,'unavailable'): await validate_batch(None,None,None,None,state,[])
            with self.assertRaisesRegex(ValueError,'unavailable'): await self.adapter.native_delegate(None,None,None,'',[])
        finally: await Config.upsert({'subagents.enable':previous})

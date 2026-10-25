"""Native header builder and owner ACL contracts of OWUI 0.11.1."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from lite_handoff_router.owui_adapter import OWUIAdapter


class LegacyServersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.users import Users
        from open_webui.models.config import Config
        self.people=[await Users.insert_new_user(n+uuid4().hex,n,uuid4().hex+'@server.test',role='user') for n in ('owner','one','two')]
        self.owner,self.one,self.two=self.people
        self.app=FastAPI(); self.app.state.redis=None
        self.request=Request({'type':'http','app':self.app,'headers':[(b'cookie',b'fixture=one')]})
        self.request.state.token=SimpleNamespace(credentials='session-'+self.one.id)
        self.adapter=OWUIAdapter(); self.sid='srv-'+uuid4().hex[:10]
        self.connection={'type':'openapi','url':'http://127.0.0.1/unused','auth_type':'none','info':{'id':self.sid},
                         'config':{'enable':True,'access_grants':[{'principal_type':'user','principal_id':self.owner.id,'permission':'read'}]}}
        self.data={'id':self.sid,'idx':0,'url':self.connection['url'],'specs':[
            {'name':n,'parameters':{'type':'object','properties':{}}} for n in ('identity','hidden')]}
        self.previous=await Config.get('tool_server.connections',[])
        await self.save()
        self.patch=patch('open_webui.utils.tools.get_tool_servers',AsyncMock(side_effect=lambda r:[self.data]))
        self.patch.start()

    async def save(self):
        from open_webui.models.config import Config
        await Config.upsert({'tool_server.connections':[self.connection]})

    async def asyncTearDown(self):
        from open_webui.models.config import Config
        from open_webui.models.users import Users
        self.patch.stop(); await Config.upsert({'tool_server.connections':self.previous})
        for person in self.people: await Users.delete_user_by_id(person.id)

    async def test_server_resolution_function_filters_owner_group_and_flags(self):
        from open_webui.models.groups import Groups,GroupForm
        from open_webui.utils import tools
        with self.assertRaisesRegex(ValueError,'denied'):
            await self.adapter.openapi_server(self.request,'server:'+self.sid,self.one)
        group=await Groups.insert_new_group(self.owner.id,GroupForm(name='server-owners-'+self.sid,description='Fixture'))
        try:
            await Groups.add_users_to_group(group.id,[self.owner.id])
            self.connection['config']['access_grants']=[{'principal_type':'group','principal_id':group.id,'permission':'read'}]
            self.connection['config']['function_name_filter_list']='identity'
            await self.save()
            for prefix in ('server:','server:openapi:'):
                loaded=await self.adapter.tools(self.request,[prefix+self.sid+'|identity,hidden'],self.owner,{'__user__':self.one.model_dump()})
                self.assertEqual(set(loaded),{'identity'})
            with patch.object(tools,'ENABLE_PLUGINS',False):
                with self.assertRaisesRegex(ValueError,'unavailable'):
                    await self.adapter.openapi_server(self.request,'server:'+self.sid,self.owner)
            self.connection['config']['enable']=False; await self.save()
            with self.assertRaisesRegex(ValueError,'unavailable'):
                await self.adapter.openapi_server(self.request,'server:'+self.sid,self.owner)
        finally: await Groups.delete_group_by_id(group.id)

    async def test_native_actor_auth_headers_cookies_and_no_client_or_owner_fallback(self):
        from starlette.requests import Request
        from open_webui.utils import tools,middleware
        seen=[]
        async def execute(**kwargs): seen.append(kwargs); return 'remote'
        async def oauth(request,actor): return {'access_token':'oauth-'+actor.id}
        self.app.state.oauth_client_manager=SimpleNamespace(get_oauth_token=AsyncMock(side_effect=lambda uid,sid:{'access_token':'external-'+uid}))
        self.connection.update(key='server-key',headers={'Actor':'{{USER_ID}}'})
        for auth in ('bearer','session','system_oauth','none','oauth_2.1','oauth_2.1_static'):
            self.connection['auth_type']=auth; await self.save()
            async def run(actor):
                request=Request({'type':'http','app':self.app,'headers':[(b'cookie',('fixture='+actor.id).encode())]})
                request.state.token=SimpleNamespace(credentials='session-'+actor.id)
                loaded=await self.adapter.tools(request,['server:'+self.sid],self.owner,{
                    '__user__':actor.model_dump(),'__oauth_token__':{'access_token':'CLIENT'},
                    '__metadata__':{'oauth_token':'CLIENT','chat_id':'chat-'+actor.id}})
                await loaded['identity']['callable']()
                call=next(c for c in seen if c['headers']['Actor']==actor.id)
                expected={'bearer':'Bearer server-key','session':'Bearer session-'+actor.id,
                    'system_oauth':'Bearer oauth-'+actor.id,'none':None,
                    'oauth_2.1':'Bearer external-'+actor.id,'oauth_2.1_static':'Bearer external-'+actor.id}[auth]
                self.assertEqual(call['headers'].get('Authorization'),expected)
                self.assertEqual(call['cookies'],{'fixture':actor.id} if auth in ('session','system_oauth') else {})
                self.assertNotIn('CLIENT',str(call)); self.assertNotIn(self.owner.id,str(call['headers']))
            seen.clear()
            with patch.object(tools,'execute_tool_server',side_effect=execute),patch.object(middleware,'get_system_oauth_token',side_effect=oauth):
                await asyncio.gather(run(self.one),run(self.two))
        for auth in ('session','system_oauth','oauth_2.1'):
            self.connection['auth_type']=auth; await self.save()
            request=Request({'type':'http','app':self.app,'headers':[]})
            self.app.state.oauth_client_manager.get_oauth_token=AsyncMock(return_value=None)
            with patch.object(middleware,'get_system_oauth_token',AsyncMock(return_value=None)):
                with self.assertRaisesRegex(ValueError,'execution-user'):
                    await self.adapter.tools(request,['server:'+self.sid],self.owner,{
                        '__user__':self.one.model_dump(),'__oauth_token__':{'access_token':'OWNER_OR_CLIENT'}})

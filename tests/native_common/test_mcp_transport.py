"""Real SDK transport and request-owned lifecycle, exercised in both releases."""

import asyncio
from contextlib import ExitStack
import json
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from lite_handoff_router.owui_adapter import OWUIAdapter
from lite_handoff_router.capabilities import McpSession, load_capabilities


class NativeMcpTransportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from mcp.server.fastmcp import FastMCP, Context
        from starlette.requests import Request
        import uvicorn
        from open_webui.models.users import Users
        from open_webui.models.config import Config
        self.owner,self.one,self.two=[await Users.insert_new_user(n+uuid4().hex,n,uuid4().hex+'@mcp.test',role='user') for n in ('owner','one','two')]
        self.adapter=OWUIAdapter(); self.seen=[]; self.lifecycle=[]; self.started=asyncio.Event()
        self.mcp=FastMCP('native-transport',stateless_http=True,json_response=True)
        @self.mcp.tool()
        async def identity(value:str, ctx:Context, delay:float=0)->str:
            headers=ctx.request_context.request.headers
            record={'value':value,'auth':headers.get('authorization'),'actor':headers.get('x-actor')}
            self.seen.append(record); self.started.set()
            await asyncio.sleep(delay)
            return json.dumps(record)
        @self.mcp.tool()
        async def hidden()->str: return 'must not be advertised'
        sock=socket.socket(); sock.bind(('127.0.0.1',0)); self.port=sock.getsockname()[1]
        self.server=uvicorn.Server(uvicorn.Config(self.mcp.streamable_http_app(),host='127.0.0.1',port=self.port,
                                                  log_level='error',access_log=False))
        self.server_task=asyncio.create_task(self.server.serve(sockets=[sock]))
        async with asyncio.timeout(5):
            while not self.server.started:
                if self.server_task.done(): self.server_task.result()
                await asyncio.sleep(.01)
        self.app=FastAPI(); self.app.state.redis=None
        self.app.state.oauth_client_manager=SimpleNamespace(get_oauth_token=AsyncMock(side_effect=lambda uid,sid:{'access_token':'oauth-'+uid}))
        self.sid='native-'+uuid4().hex[:10]
        self.connection={'type':'mcp','info':{'id':self.sid},'url':f'http://127.0.0.1:{self.port}/mcp',
            'auth_type':'session','headers':{'X-Actor':'{{USER_ID}}'},'config':{'enable':True,
            'function_name_filter_list':'identity','access_grants':[{'principal_type':'user','principal_id':self.owner.id,'permission':'read'}]}}
        self.previous=await Config.get('tool_server.connections',[])
        await Config.upsert({'tool_server.connections':[self.connection]})
        self.patches=ExitStack()
        from open_webui.utils import tools
        if self.adapter.profile.version=='0.11.1':
            from open_webui.utils.mcp import client as native_client
            self.client_module=native_client
            self.oauth_module=__import__('open_webui.utils.middleware',fromlist=['get_system_oauth_token'])
        else:
            self.client_module=tools; self.oauth_module=tools
        actual=self.client_module.MCPClient
        def instrumented(*args,**kwargs):
            client=actual(*args,**kwargs)
            for name in ('connect','list_tool_specs','call_tool','disconnect'):
                original=getattr(client,name)
                async def logged(*a,_name=name,_original=original,**kw):
                    self.lifecycle.append((id(client),_name,asyncio.current_task()))
                    return await _original(*a,**kw)
                setattr(client,name,logged)
            return client
        self.patches.enter_context(patch.object(self.client_module,'MCPClient',side_effect=instrumented))
        self.patches.enter_context(patch.object(self.oauth_module,'get_system_oauth_token',
            AsyncMock(side_effect=lambda r,u:{'access_token':'system-'+u.id})))
        self.sessions=[]

    async def asyncTearDown(self):
        from open_webui.models.config import Config
        from open_webui.models.users import Users
        for session in self.sessions: await session.close()
        self.patches.close()
        self.server.should_exit=True; await self.server_task
        await Config.upsert({'tool_server.connections':self.previous})
        for user in (self.owner,self.one,self.two): await Users.delete_user_by_id(user.id)
        clients={c for c,_,_ in self.lifecycle}
        for client in clients:
            calls=[(name,task) for cid,name,task in self.lifecycle if cid==client]
            self.assertEqual(len({task for _,task in calls}),1,'SDK enter/call/exit must use one owner task')
            self.assertEqual(calls[-1][0],'disconnect')

    def request(self,actor):
        from starlette.requests import Request
        request=Request({'type':'http','app':self.app,'headers':[]})
        request.state.token=SimpleNamespace(credentials='session-'+actor.id)
        return request

    async def open(self,actor):
        request=self.request(actor)
        extra={'__user__':actor.model_dump(),'__model__':{'id':'model'},'__metadata__':{'oauth_token':'CLIENT'}}
        session=McpSession(lambda:self.adapter.connect_mcp(request,self.sid,self.owner,extra))
        self.sessions.append(session)
        specs,_=await session.ready
        self.assertEqual([s['name'] for s in specs],['identity'])
        return session

    async def test_two_actors_auth_filters_repeat_calls_and_real_disconnect(self):
        from open_webui.models.config import Config
        for auth in ('session','system_oauth','oauth_2.1'):
            self.connection['auth_type']=auth; await Config.upsert({'tool_server.connections':[self.connection]})
            async def run(actor):
                with self.assertRaisesRegex(ValueError,'denied'): await self.adapter.mcp_access(self.sid,actor)
                session=await self.open(actor)
                for value in ('one','two'):
                    result=await session.call('identity',{'value':value})
                    self.assertIn(actor.id,str(result))
                await session.close()
                prefix={'session':'session-','system_oauth':'system-','oauth_2.1':'oauth-'}[auth]
                calls=[c for c in self.seen if c['actor']==actor.id][-2:]
                self.assertEqual(len(calls),2)
                self.assertTrue(all(c['auth']=='Bearer '+prefix+actor.id for c in calls))
                self.assertTrue(session.task.done())
            await asyncio.gather(run(self.one),run(self.two))

    async def test_stop_and_preparation_error_close_owned_session_without_replay(self):
        session=await self.open(self.one)
        task=asyncio.create_task(session.call('identity',{'value':'slow','delay':.15}))
        await asyncio.wait_for(self.started.wait(),3)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        await session.close()
        self.assertEqual(len(self.seen),1)
        with self.assertRaisesRegex(RuntimeError,'closed'): await session.call('identity',{'value':'late'})
        # A prepared MCP connection is also closed if a later configured one fails.
        request=self.request(self.two)
        with self.assertRaisesRegex(ValueError,'unavailable'):
            await load_capabilities(self.adapter,request,['server:mcp:'+self.sid,'server:mcp:missing'],self.owner,
                {'__user__':self.two.model_dump(),'__model__':{'id':'model'}})
        self.assertEqual(request.scope.get('lite_handoff_mcp'),{})

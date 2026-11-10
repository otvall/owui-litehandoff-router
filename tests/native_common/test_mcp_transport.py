"""Real SDK transport and request-owned lifecycle, on Open WebUI 0.13.3."""

import asyncio
from contextlib import ExitStack
import json
import socket
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from lite_handoff_router.owui_adapter import OWUIAdapter
from lite_handoff_router.capabilities import McpSession, load_capabilities


class NativeMcpTransportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from fastapi import FastAPI
        from mcp.server.mcpserver import MCPServer, Context
        from starlette.requests import Request
        import uvicorn
        from open_webui.models.users import Users
        from open_webui.models.config import Config
        self.owner,self.one,self.two=[await Users.insert_new_user(n+uuid4().hex,n,uuid4().hex+'@mcp.test',role='user') for n in ('owner','one','two')]
        self.adapter=OWUIAdapter(); self.seen=[]; self.lifecycle=[]; self.started=asyncio.Event()
        self.clients=[]
        self.server_calls=[]
        self.remote_release=asyncio.Event()
        self.mcp=MCPServer('native-transport')
        @self.mcp.tool()
        async def identity(value:str, ctx:Context, delay:float=0)->str:
            self.server_calls.append('identity')
            headers=ctx.request_context.request.headers
            record={'value':value,'auth':headers.get('authorization'),'actor':headers.get('x-actor')}
            self.seen.append(record); self.started.set()
            if value=='filter-0':
                self.connection['config']['function_name_filter_list']='hidden'
                await Config.upsert({'tool_server.connections':[self.connection]})
            if delay>=60:
                await self.remote_release.wait()
            else:
                await asyncio.sleep(delay)
            return json.dumps(record)
        @self.mcp.tool()
        async def hidden()->str:
            self.server_calls.append('hidden')
            return 'hidden result'
        sock=socket.socket(); sock.bind(('127.0.0.1',0)); self.port=sock.getsockname()[1]
        self.server=uvicorn.Server(uvicorn.Config(self.mcp.streamable_http_app(stateless_http=True,json_response=True),host='127.0.0.1',port=self.port,
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
        self.client_module=tools; self.oauth_module=tools
        actual=self.client_module.MCPClient
        def instrumented(*args,**kwargs):
            client=actual(*args,**kwargs)
            self.clients.append(client)
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
        self.remote_release.set()
        self.patches.close()
        self.server.should_exit=True; await self.server_task
        await Config.upsert({'tool_server.connections':self.previous})
        for user in (self.owner,self.one,self.two): await Users.delete_user_by_id(user.id)
        clients={c for c,_,_ in self.lifecycle}
        for client in clients:
            calls=[(name,task) for cid,name,task in self.lifecycle if cid==client]
            self.assertEqual(len({task for name,task in calls if name!='disconnect'}),1,
                             'SDK connect/calls must be serialized by one worker')
            self.assertEqual(calls[-1][0],'disconnect')

    def request(self,actor):
        from starlette.requests import Request
        request=Request({'type':'http','app':self.app,'headers':[]})
        request.state.token=SimpleNamespace(credentials='session-'+actor.id)
        return request

    async def open(self,actor):
        request=self.request(actor)
        extra={'__user__':actor.model_dump(),'__model__':{'id':'model'},'__metadata__':{'oauth_token':'CLIENT'}}
        session=McpSession(lambda register:self.adapter.connect_mcp(request,self.sid,self.owner,extra,register_client=register))
        self.sessions.append(session)
        specs,_=await session.ready
        specs=await self.adapter.mcp_specs(self.sid,self.owner,specs)
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

    async def test_blocked_sdk_courtesy_cancel_shares_disconnect_deadline(self):
        from mcp.shared.jsonrpc_dispatcher import JSONRPCDispatcher

        session = await self.open(self.one)
        transport = self.clients[-1].session_task
        abandoning = asyncio.Event()

        async def blocked_cancel(*args, **kwargs):
            abandoning.set()
            await asyncio.Event().wait()

        active = asyncio.create_task(session.call('identity', {'value':'blocked-cancel', 'delay':60}))
        await asyncio.wait_for(self.started.wait(), 3)
        queued = asyncio.create_task(session.call('identity', {'value':'never-send'}))
        await asyncio.sleep(0)
        # Keep the SDK's real shielded five-second courtesy-cancel helper. Only
        # its outbound write is blocked, modelling an unresponsive transport.
        with patch.object(JSONRPCDispatcher, '_cancel_outbound', blocked_cancel), \
             patch('lite_handoff_router.capabilities.MCP_CLOSE_TIMEOUT', .05), \
             self.assertLogs('lite_handoff_router.capabilities', 'ERROR') as logs:
            closing = asyncio.create_task(session.close())
            await asyncio.wait_for(abandoning.wait(), 1)
            await asyncio.wait_for(closing, .5)
        self.assertIn('close timed out', logs.output[0])
        self.assertTrue(session.worker.done())
        self.assertTrue(transport.done())
        self.assertIsNone(self.clients[-1].client)
        self.assertIsNone(self.clients[-1].session_task)
        self.assertTrue(active.done())
        self.assertTrue(queued.done())
        await asyncio.gather(active, queued, return_exceptions=True)
        self.assertEqual([record['value'] for record in self.seen], ['blocked-cancel'])

    async def run_export_request(self, scenario, actor, *, handoff=False):
        """Native Filter/Pipe + completion loop; only generation/events replaced."""
        from fastapi.responses import JSONResponse, StreamingResponse
        from open_webui.models.models import Models, ModelForm
        from open_webui.models.functions import Functions, FunctionForm
        from open_webui.models.skills import Skills, SkillForm
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.config import Config
        from open_webui.utils.plugin import load_function_module_by_id, set_function_module_in_cache
        from open_webui.utils import chat, middleware, tools as native_tools
        from open_webui import functions

        root=Path(__file__).resolve().parents[2]
        suffix=uuid4().hex
        pipe_id,router_id,agent_id,skill_id=['mcp-'+name+'-'+suffix for name in ('pipe','router','agent','route')]
        request=self.request(actor)
        request.state.max_tool_call_iterations=8
        self.app.state.MODELS={}
        pipe_content=(root/'dist/lite_handoff_router.py').read_text()
        pipe,kind,_,source=await load_function_module_by_id(pipe_id,pipe_content)
        await Functions.insert_new_function(self.owner.id,kind,FunctionForm(id=pipe_id,name='MCP router',content=pipe_content,meta={}),module=pipe)
        await Functions.update_function_by_id(pipe_id,{'is_active':True})
        await Functions.update_function_valves_by_id(pipe_id,{'orchestrator_model_id':'router-llm'})
        set_function_module_in_cache(request,pipe_id,pipe_content,pipe,source)
        for base_id in (pipe_id,'worker-llm','router-llm'):
            if not await Models.get_model_by_id(base_id):
                await Models.insert_new_model(ModelForm(id=base_id,name=base_id,params={},meta={}),self.owner.id)
            await AccessGrants.grant_access('model',base_id,'user',actor.id,'read')
        for identifier,base,meta in ((router_id,pipe_id,{}),
                                    (agent_id,'worker-llm',{'toolIds':['server:mcp:'+self.sid]})):
            row=await Models.insert_new_model(ModelForm(id=identifier,name=identifier,base_model_id=base,params={},meta=meta),self.owner.id)
            await AccessGrants.grant_access('model',identifier,'user',actor.id,'read')
            self.app.state.MODELS[identifier]={'id':identifier,'owned_by':'openai','info':row.model_dump()}
        self.app.state.MODELS[router_id]['pipe']={'type':'pipe'}
        self.app.state.MODELS[pipe_id]={'id':pipe_id,'pipe':{'type':'pipe'}}
        self.app.state.MODELS['router-llm']={'id':'router-llm','owned_by':'openai'}
        preparation,_,_,prep_source=await load_function_module_by_id('prep_'+suffix,(root/'dist/router_preparation.py').read_text())
        if handoff:
            await Skills.insert_new_skill(self.owner.id,SkillForm(id=skill_id,name=skill_id,description='MCP agent',content='Use agent',meta={}))
            prep_source.AGENTS={agent_id:skill_id}
        else:
            preparation.valves.base_tool_ids=['server:mcp:'+self.sid]
        openapi_data=[]
        if scenario=='openapi':
            preparation.valves.base_tool_ids=['server:one','server:two']
            connections=[]
            for index,server in enumerate(('one','two')):
                connections.append({'type':'openapi','url':'http://'+server+'.test','auth_type':'session',
                    'config':{'enable':True,'access_grants':[{'principal_type':'user','principal_id':self.owner.id,'permission':'read'}]}})
                openapi_data.append({'id':server,'idx':index,'url':connections[-1]['url'],'specs':[{
                    'name':'lookup','parameters':{'type':'object','properties':{'value':{'type':'string'}}}}]})
            await Config.upsert({'tool_server.connections':connections})
        await Config.upsert({'chat.context_compaction.enable':False})
        metadata={'chat_id':'local:'+suffix,'message_id':'answer-'+suffix,'session_id':'session-'+suffix,
                  'params':{'function_calling':'native','tool_approval_mode':'full'},'files':[]}
        body={'model':router_id,'stream':True,'messages':[{'role':'user','content':scenario}],'metadata':metadata}
        request.state.metadata=metadata
        emitted=[]
        generations=[]
        original_call=None

        async def emit(event): emitted.append(event)

        async def transport(request,form_data,user):
            nonlocal original_call
            names={t['function']['name'] for t in form_data.get('tools',[])}
            delegated=handoff and 'lite_delegate' in names
            number=len([n for n in generations if not n])
            generations.append(delegated)
            if scenario=='filter':
                if number==0:
                    original_call=request.state.metadata['tools'][self.sid+'_identity']['callable']
                else:
                    self.assertEqual(names,{self.sid+'_hidden'})
                    with self.assertRaisesRegex(ValueError,'permitted'):
                        await original_call(value='forbidden')
            if scenario=='openapi':
                self.assertEqual(names,{'lookup','server_two_lookup'})
            if not delegated and number and scenario=='provider_error':
                return JSONResponse({'error':{'message':'Expected provider failure'}},status_code=503)

            async def stream():
                if not delegated and number and scenario=='stream_error':
                    raise RuntimeError('Expected stream failure')
                if delegated or number<2:
                    name='lite_delegate' if delegated else self.sid+('_hidden' if scenario=='filter' and number else '_identity')
                    if scenario=='openapi': name='lookup' if number==0 else 'server_two_lookup'
                    arguments=({'agent_id':agent_id} if delegated else {} if name.endswith('_hidden') else
                               {'value':scenario+'-'+str(number),'delay':60 if scenario=='stop' else 0})
                    choice={'delta':{'tool_calls':[{'index':0,'id':uuid4().hex,'type':'function',
                             'function':{'name':name,'arguments':json.dumps(arguments)}}]},'finish_reason':'tool_calls'}
                else:
                    choice={'delta':{'content':'MCP_DONE'},'finish_reason':'stop'}
                yield 'data: '+json.dumps({'choices':[{'index':0,**choice}]})+'\n\n'
                yield 'data: [DONE]\n\n'
            return StreamingResponse(stream(),media_type='text/event-stream')

        context={'request':request,'form_data':body,'user':actor,'model':self.app.state.MODELS[router_id],
                 'metadata':metadata,'tasks':{},'events':[],'event_emitter':emit,'event_caller':None}
        async def openapi_transport(**kwargs):
            self.openapi_calls.append(kwargs)
            return kwargs['url']

        with ExitStack() as integration:
            if scenario=='openapi':
                self.openapi_calls=[]
                integration.enter_context(patch.object(native_tools,'get_tool_servers',AsyncMock(return_value=openapi_data)))
                integration.enter_context(patch.object(native_tools,'execute_tool_server',side_effect=openapi_transport))
            integration.enter_context(patch.object(chat,'generate_openai_chat_completion',side_effect=transport))
            integration.enter_context(patch.object(functions,'get_event_emitter',AsyncMock(return_value=emit)))
            integration.enter_context(patch.object(functions,'get_event_call',AsyncMock(return_value=None)))
            await preparation.inlet(body,request,actor.model_dump(),metadata)
            response=await chat.generate_chat_completion(request,body,actor)
            await middleware.process_chat_response(response,context)
        return request,emitted,generations

    async def test_exports_dispatch_colliding_openapi_schemas_through_native_loop(self):
        owner=asyncio.create_task(self.run_export_request('openapi',self.one))
        request,events,generations=await asyncio.wait_for(owner,10)
        self.assertIn('MCP_DONE',str(events))
        self.assertEqual([c['url'] for c in self.openapi_calls],['http://one.test','http://two.test'])
        self.assertEqual([c['name'] for c in self.openapi_calls],['lookup','lookup'])
        self.assertTrue(all(c['headers']['Authorization']=='Bearer session-'+self.one.id for c in self.openapi_calls))

    async def test_exports_close_automatically_after_success_errors_and_stop(self):
        # The exports are generated by the same builder used in the release suite.
        import runpy
        runpy.run_path(str(Path(__file__).resolve().parents[2]/'tools/build_functions.py'))['build']()
        for scenario,handoff in (('success',False),('success',True),('filter',False),('provider_error',False),('stream_error',False),('stop',True)):
            with self.subTest(scenario=scenario,handoff=handoff):
                self.started.clear()
                self.connection['config']['function_name_filter_list']='identity'
                from open_webui.models.config import Config
                await Config.upsert({'tool_server.connections':[self.connection]})
                first=len(self.clients)
                owner=asyncio.create_task(self.run_export_request(scenario,self.one,handoff=handoff))
                if scenario=='stop':
                    await asyncio.wait_for(self.started.wait(),5)
                    owner.cancel()
                outcome=await asyncio.wait_for(asyncio.gather(owner,return_exceptions=True),10)
                # Owner done callbacks, not test close(), must finish transport cleanup.
                async with asyncio.timeout(5):
                    while any(c.session_task is not None for c in self.clients[first:]):
                        await asyncio.sleep(.01)
                self.assertGreater(len(self.clients),first,outcome)
                calls=[(c,name) for c,name,_ in self.lifecycle if c in {id(x) for x in self.clients[first:]}]
                self.assertEqual(sum(n=='connect' for _,n in calls),1)
                self.assertEqual(sum(n=='disconnect' for _,n in calls),1)
                if scenario in ('success','filter'):
                    self.assertNotIsInstance(outcome[0],BaseException)
                    request,events,generations=outcome[0]
                    self.assertIn('MCP_DONE',str(events))
                    self.assertEqual(sum(not n for n in generations),3)
                    self.assertTrue(all(s.task.done() for s in request.scope['lite_handoff_mcp'].values()))
                else:
                    self.assertLessEqual(sum(n=='call_tool' for _,n in calls),1)
                    if scenario!='stop':
                        self.assertIn('Expected',str(outcome))

    async def test_cancelled_worker_does_not_close_other_actor_and_close_is_repeatable(self):
        sessions={}
        release=asyncio.Event()

        async def worker(actor):
            session=await self.open(actor)
            sessions[actor.id]=session
            if actor.id==self.one.id:
                await session.call('identity',{'value':'hang','delay':60})
            else:
                await release.wait()
                return await session.call('identity',{'value':'survivor'})

        one=asyncio.create_task(worker(self.one)); two=asyncio.create_task(worker(self.two))
        await asyncio.wait_for(self.started.wait(),5)
        while len(sessions)<2: await asyncio.sleep(0)
        one.cancel()
        await asyncio.gather(one,return_exceptions=True)
        await asyncio.gather(sessions[self.one.id].close(),sessions[self.one.id].close())
        self.assertFalse(sessions[self.two.id].task.done())
        release.set()
        self.assertIn(self.two.id,str(await two))
        await asyncio.wait_for(asyncio.shield(sessions[self.two.id].task),5)
        self.assertEqual(len([s for s in self.seen if s['actor']==self.one.id]),1)

    async def test_filter_refresh_and_invoke_revocation_on_live_connections(self):
        from open_webui.models.config import Config
        requests=[]
        connects_before=len([n for _,n,_ in self.lifecycle if n=='connect'])
        try:
            for actor in (self.one,self.two):
                request=self.request(actor); requests.append(request)
                extra={'__user__':actor.model_dump(),'__model__':{'id':'filter-model'}}
                self.connection['config']['function_name_filter_list']='identity'
                await Config.upsert({'tool_server.connections':[self.connection]})
                original,_=await load_capabilities(self.adapter,request,['server:mcp:'+self.sid],self.owner,extra)
                self.connection['config']['function_name_filter_list']=['hidden']
                await Config.upsert({'tool_server.connections':[self.connection]})
                count=len(self.server_calls)
                with self.assertRaisesRegex(ValueError,'permitted'):
                    await original[self.sid+'_identity']['callable'](value='must-not-send')
                self.assertEqual(len(self.server_calls),count)
                fresh,_=await load_capabilities(self.adapter,request,['server:mcp:'+self.sid],self.owner,extra)
                self.assertEqual(set(fresh),{self.sid+'_hidden'})
                self.assertIn('hidden result',str(await fresh[self.sid+'_hidden']['callable']()))
                for policy,allowed in ((['tity'],{'identity'}),('identity,hidden,!hidden',{'identity'}),
                                       (['!hidden'],{'identity'}),('',{'identity','hidden'}),([],{'identity','hidden'})):
                    self.connection['config']['function_name_filter_list']=policy
                    await Config.upsert({'tool_server.connections':[self.connection]})
                    loaded,_=await load_capabilities(self.adapter,request,['server:mcp:'+self.sid],self.owner,extra)
                    self.assertEqual(set(loaded),{self.sid+'_'+n for n in allowed})
                    self.assertIn(actor.id,str(await loaded[self.sid+'_identity']['callable'](value='allowed')))
                self.connection['config']['function_name_filter_list']=['absent']
                await Config.upsert({'tool_server.connections':[self.connection]})
                with self.assertRaisesRegex(ValueError,'no permitted Tools'):
                    await load_capabilities(self.adapter,request,['server:mcp:'+self.sid],self.owner,extra)
                with self.assertRaisesRegex(ValueError,'denied'):
                    await self.adapter.mcp_access(self.sid,actor)
            self.assertEqual(len([n for _,n,_ in self.lifecycle if n=='connect'])-connects_before,2)
            self.assertTrue(all(s['auth']=='Bearer session-'+s['actor'] for s in self.seen))
        finally:
            from lite_handoff_router.capabilities import close_mcp_sessions
            for request in requests:
                await close_mcp_sessions(request.scope.get('lite_handoff_mcp',{}).values())

    async def test_parallel_stop_and_deadline_close_real_mcp_transports(self):
        import sys
        from lite_handoff_router.parallel import run_registry
        root=Path(__file__).resolve().parents[2]
        with patch.object(sys,'path',[str(root),*sys.path]):
            from tests.test_parallel import GroupAdapter, ParallelExecutionTest
        support=ParallelExecutionTest()
        for deadline in (False,True):
            with self.subTest(deadline=deadline):
                request,metadata,state,user=support.setup_request(user=self.one)
                if deadline: state['parallel']['timeout_seconds']=2
                adapter=GroupAdapter()
                delegate=adapter.native_delegate
                sessions=[]

                async def native_dispatch(req,actor,meta,task,file_ids):
                    await delegate(req,actor,meta,task,file_ids)
                    ticket=meta['chat_context']['lite_parallel_ticket']
                    entry=run_registry(req)[ticket]
                    session=await self.open(self.one)
                    sessions.append(session)
                    entry['scope'].setdefault('lite_handoff_mcp',{})[ticket]=session
                    return await session.call('identity',{'value':ticket,'delay':60})

                adapter.native_delegate=native_dispatch
                seen_before=len(self.seen)
                work=asyncio.create_task(support.invoke(adapter,request,metadata,state,user,
                    [{'agent_id':'a','task':'MCP one'},{'agent_id':'b','task':'MCP two'}]))
                if deadline:
                    report=await asyncio.wait_for(work,5)
                    self.assertEqual(report['status'],'timed_out')
                else:
                    async with asyncio.timeout(5):
                        while len(self.seen)<seen_before+2: await asyncio.sleep(.01)
                    work.cancel()
                    with self.assertRaises(asyncio.CancelledError): await work
                self.assertEqual(len(sessions),2)
                self.assertTrue(all(s.task.done() for s in sessions))
                self.assertEqual(run_registry(request),{})
                self.assertEqual(len(self.seen)-seen_before,2)

"""Loopback-only 0.11.1 fixture: scripted provider, OpenAPI, real MCP and shell."""

import asyncio
from contextlib import asynccontextmanager
import json
import re
from pathlib import Path
import time

from fastapi import FastAPI,Request
from mcp.server.fastmcp import Context,FastMCP
import terminal_gate_provider as terminal

ROOT=Path(__file__).resolve().parent
terminal.ROOT=ROOT
terminal.WORK=ROOT/'work'
mcp=FastMCP('Legacy router acceptance',streamable_http_path='/',stateless_http=True,json_response=True)


def trace(kind,**data): terminal.trace(kind,**data)


def actor(request):
    identifier=request.headers.get('x-actor')
    users=json.loads((ROOT/'private-state.json').read_text())['users']
    person=next((u for u in users.values() if u['id']==identifier),None)
    return {'actor':identifier,'auth_matches_actor':bool(person and request.headers.get('authorization')=='Bearer '+person['token'])}


@mcp.tool()
async def mcp_echo(value:str,ctx:Context,delay:float=0)->str:
    who=actor(ctx.request_context.request)
    assert who['auth_matches_actor']
    trace('mcp_start',**who,value=value)
    try:
        await asyncio.sleep(delay)
        return json.dumps({'marker':'MCP_OK',**who,'value':value})
    finally: trace('mcp_end',**who,value=value)


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run(): yield


app=FastAPI(lifespan=lifespan)
app.mount('/mcp',mcp.streamable_http_app())
app.mount('/terminal',terminal.app)


@app.get('/v1/models')
async def models():
    return {'object':'list','data':[{'id':n,'object':'model','owned_by':'local'} for n in ('legacy-router-llm','legacy-a','legacy-b')]}


@app.get('/server/openapi.json')
async def spec():
    return {'openapi':'3.0.0','info':{'title':'Legacy OpenAPI fixture','version':'1'},'paths':{'/echo':{'post':{
        'operationId':'server_echo','summary':'Actor echo','requestBody':{'required':True,'content':{'application/json':{
        'schema':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}}},
        'responses':{'200':{'description':'Result','content':{'application/json':{'schema':{'type':'object'}}}}}}}}}


@app.post('/server/echo')
async def echo(request:Request):
    who=actor(request); assert who['auth_matches_actor']
    value=(await request.json())['value']
    trace('openapi',**who,value=value)
    return {'marker':'OPENAPI_OK',**who,'value':value}


@app.post('/v1/chat/completions')
async def completion(request:Request):
    body=await request.json(); model=body['model']; messages=body['messages']
    names={t['function']['name'] for t in body.get('tools',[])}
    index=next(i for i in range(len(messages)-1,-1,-1) if messages[i]['role']=='user')
    question=str(messages[index]['content']); current=messages[index+1:]
    calls={c['id']:c['function']['name'] for m in current for c in m.get('tool_calls',[])}
    results={calls.get(m.get('tool_call_id')):m.get('content') for m in current if m['role']=='tool'}
    systems=str([m for m in messages if m['role']=='system'])
    trace('provider',model=model,tools=sorted(names),question=question,temperature=body.get('temperature'),systems=systems)
    for result in results.values():
        if str(result).startswith('Error:'):
            return terminal.reply(model,'TOOL_ERROR '+str(result))
    if model=='legacy-router-llm':
        assert 'LEGACY_ROUTER_PROMPT' in systems and 'TG_TERMINAL_GUIDE' not in systems
        assert not {'lite_parallel','delegate_task','run_command','read_skill_file','grep_skill_files'} & names
        if 'force parallel' in question: return terminal.reply(model,tool='lite_parallel',args={'tasks':[]})
        if 'general' in question: return terminal.reply(model,'LEGACY_GENERAL_OK')
        letter='b' if 'ordinary' in question else 'a'
        if 'view_skill' not in results: return terminal.reply(model,tool='view_skill',args={'id':'legacy-route-'+letter})
        return terminal.reply(model,tool='lite_delegate',args={'agent_id':'legacy-agent-'+letter})
    letter=model[-1]
    assert 'LEGACY_'+letter.upper()+'_PROMPT' in systems and 'LEGACY_ROUTER_PROMPT' not in systems
    assert not {'lite_delegate','lite_parallel','delegate_task','read_skill_file','grep_skill_files'} & names
    assert body.get('temperature')==(.21 if letter=='a' else .41)
    if letter=='a': assert 'run_command' in names and systems.count('TG_TERMINAL_GUIDE')==1
    else: assert 'run_command' not in names and 'TG_TERMINAL_GUIDE' not in systems
    if 'view_skill' not in results: return terminal.reply(model,tool='view_skill',args={'id':'legacy-work-'+letter})
    if 'legacy_lookup' not in results: return terminal.reply(model,tool='legacy_lookup')
    assert 'LOCAL_OK' in str(results['legacy_lookup'])
    if 'server_echo' not in results: return terminal.reply(model,tool='server_echo',args={'value':'OPENAPI_'+question})
    assert 'OPENAPI_OK' in str(results['server_echo'])
    name=next(n for n in names if n.endswith('_mcp_echo'))
    if name not in results: return terminal.reply(model,tool=name,args={'value':'MCP_'+question,'delay':3 if 'slow mcp' in question else 0})
    assert 'MCP_OK' in str(results[name])
    if letter=='a':
        marker='LEGACY_MARKER_'+re.search(r'run=([a-f0-9-]{36})',question).group(1)
        path=marker+'.txt'
        if 'run_command' not in results:
            delay='sleep 3; ' if 'slow terminal' in question else ''
            return terminal.reply(model,tool='run_command',args={'command':delay+"printf '%s' '"+marker+"' > '"+path+"'"})
        if 'read_file' not in results: return terminal.reply(model,tool='read_file',args={'path':path})
        assert marker in str(results['read_file'])
    return terminal.reply(model,'LEGACY_FINAL_'+letter.upper()+'_OK '+json.dumps(results))


if __name__=='__main__':
    assert ROOT==Path('/tmp/lhr-0111-gate'),'Copy into the disposable fixture directory first'
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8774)

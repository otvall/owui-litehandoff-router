"""Disposable LLM script plus a terminal-compatible backend executing real shell commands."""

import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4

from fastapi import FastAPI,Request
from fastapi.responses import StreamingResponse

ROOT=Path(__file__).resolve().parent
WORK=ROOT/'work'
app=FastAPI()
PHASES={}


def trace(kind,**data):
    with (ROOT/'trace.jsonl').open('a') as output:
        output.write(json.dumps({'kind':kind,'time':time.monotonic(),**data})+'\n')


@app.get('/v1/models')
async def models():
    return {'object':'list','data':[{'id':'tgate-'+n,'object':'model','owned_by':'local'} for n in ('router','a','b','c')]}


def reply(model,content=None,tool=None,args=None):
    message={'role':'assistant','content':content}
    if tool:
        message['tool_calls']=[{'index':0,'id':'tg_'+uuid4().hex,'type':'function',
            'function':{'name':tool,'arguments':json.dumps(args or {})}}]
    async def events():
        for delta,finish in ((message,None),({},'tool_calls' if tool else 'stop')):
            yield 'data: '+json.dumps({'id':'tg_'+uuid4().hex,'object':'chat.completion.chunk',
                'created':int(time.time()),'model':model,'choices':[{'index':0,'delta':delta,'finish_reason':finish}]})+'\n\n'
        yield 'data: [DONE]\n\n'
    return StreamingResponse(events(),media_type='text/event-stream')


@app.post('/v1/chat/completions')
async def completion(request:Request):
    body=await request.json()
    model=body['model']
    messages=body['messages']
    names={t['function']['name'] for t in body.get('tools',[])}
    systems=str([m for m in messages if m['role']=='system'])
    question=str(next(m['content'] for m in reversed(messages) if m['role']=='user'))
    calls={c['id']:c['function']['name'] for m in messages for c in m.get('tool_calls',[])}
    results={calls.get(m.get('tool_call_id')):m.get('content') for m in messages if m['role']=='tool'}
    trace('provider',model=model,tools=sorted(names),question=question[:180])
    if model=='tgate-router':
        assert 'TG_ROUTER_PROMPT' in systems and 'TG_TERMINAL_GUIDE' not in systems
        assert not {'run_command','read_file'} & names
        if 'general' in question:
            return reply(model,'GENERAL_OK')
        if 'view_skill' not in results:
            return reply(model,tool='view_skill',args={'id':'tgate-route-a'})
        if 'handoff' in question:
            return reply(model,tool='lite_delegate',args={'agent_id':'tgate-agent-a'})
        if 'lite_parallel' in results:
            result=json.loads(results['lite_parallel'])
            if result.get('status')!='completed':
                return reply(model,'GROUP_'+str(result.get('status')))
            return reply(model,tool='lite_delegate',args={'agent_id':'tgate-agent-c'})
        return reply(model,tool='lite_parallel',args={'tasks':[
            {'agent_id':'tgate-agent-a','task':'First task '+question},
            {'agent_id':'tgate-agent-a' if 'same' in question else 'tgate-agent-b','task':'Second task '+question}]})
    letter=model[-1]
    assert model in {'tgate-a','tgate-b','tgate-c'}
    assert 'TG_'+letter.upper()+'_PROMPT' in systems and 'TG_ROUTER_PROMPT' not in systems
    assert not {'lite_delegate','lite_parallel','delegate_task'} & names
    if letter=='b':
        assert not {'run_command','read_file'} & names and 'TG_TERMINAL_GUIDE' not in systems
        if 'visualizer' in question:
            if 'Inline Visualizer' not in str(results.get('view_skill','')):
                return reply(model,tool='view_skill',args={'id':'tgate-visualizer-skill'})
            if 'visualize' not in results:
                return reply(model,tool='visualize',args={'title':'TG_VISUALIZER_REGRESSION'})
            assert 'mounted' in str(results['visualize'])
            return reply(model,'''@@@VIZ-START
<style>.tg{font:18px system-ui;padding:20px;background:#eef4ff;color:#123}button{padding:10px}</style>
<div class="tg"><h2>TG_VISUALIZER_REGRESSION</h2><svg viewBox="0 0 300 90" role="img" aria-label="Local SVG 42"><circle cx="45" cy="45" r="30" fill="#2563eb"/><text x="95" y="55">SVG 42</text></svg><button id="tg-toggle">Toggle regression</button><p id="tg-state">STATE=0</p></div>
<script>document.getElementById('tg-toggle').onclick=()=>{document.getElementById('tg-state').textContent='STATE=1'}</script>
@@@VIZ-END

Visualization regression complete.''')
        if 'view_skill' not in results:
            return reply(model,tool='view_skill',args={'id':'tgate-work-b'})
        if 'tgate_echo' not in results:
            return reply(model,tool='tgate_echo')
        assert 'B_OK' in str(results['tgate_echo'])
        return reply(model,'B_OK')
    assert {'run_command','read_file'} <= names and systems.count('TG_TERMINAL_GUIDE')==1
    if letter=='c':
        packet=next(m['content'] for m in messages if str(m.get('content','')).startswith('Confirmed parallel task results.'))
        assert 'A_OK' in packet
    assignment=next((str(m['content']) for m in messages if str(m.get('content','')).startswith('Assigned task ')),question)
    key=(model,assignment)
    phase,marker=PHASES.get(key,(0,letter.upper()+'_OK_'+uuid4().hex))
    if letter=='a' and 'view_skill' not in results:
        return reply(model,tool='view_skill',args={'id':'tgate-work-a'})
    PHASES[key]=(phase+1,marker)
    path=marker+'.txt'
    if phase==0:
        delay='sleep 5; ' if 'slow' in assignment and letter=='a' else ''
        return reply(model,tool='run_command',args={'command':delay+"printf '%s' '"+marker+"' > '"+path+"'"})
    if phase==1:
        assert 'run_command' in results and 'exit_code' in str(results['run_command'])
        return reply(model,tool='read_file',args={'path':path})
    assert marker in str(results.get('read_file'))
    return reply(model,('FINAL_OK ' if letter=='c' else '')+marker)


@app.get('/p/{letter}/openapi.json')
async def terminal_spec(letter:str):
    def operation(name,properties):
        return {'post':{'operationId':name,'summary':name,'description':'Execute on disposable terminal backend',
            'requestBody':{'required':True,'content':{'application/json':{'schema':{
                'type':'object','properties':properties,'required':list(properties)}}}},
            'responses':{'200':{'description':'Result','content':{'application/json':{'schema':{'type':'object'}}}}}}}
    return {'openapi':'3.0.0','info':{'title':'Real command acceptance terminal','version':'1'},
        'paths':{'/run':operation('run_command',{'command':{'type':'string'}}),
                 '/read':operation('read_file',{'path':{'type':'string'}})}}


@app.get('/p/{letter}/api/config')
async def terminal_config(letter:str):
    return {'features':{'system':True}}


@app.get('/p/{letter}/system')
async def terminal_system(letter:str):
    return {'prompt':'TG_TERMINAL_GUIDE '+letter}


@app.get('/p/{letter}/files/cwd')
async def terminal_cwd(letter:str):
    WORK.mkdir(exist_ok=True)
    return {'cwd':str(WORK)}


def identity(request,letter,operation):
    return {'terminal':letter,'operation':operation,'actor':request.headers.get('x-user-id'),
            'chat_id':request.headers.get('x-session-id'),'context':request.headers.get('x-terminal-context-id')}


@app.post('/p/{letter}/run')
async def run_command(letter:str,request:Request):
    body=await request.json()
    WORK.mkdir(exist_ok=True)
    who=identity(request,letter,'run_command')
    trace('command_start',**who)
    process=await asyncio.create_subprocess_shell(body['command'],cwd=WORK,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    stdout,stderr=await process.communicate()
    trace('command_end',**who,exit_code=process.returncode)
    return {'stdout':stdout.decode(),'stderr':stderr.decode(),'exit_code':process.returncode}


@app.post('/p/{letter}/read')
async def read_file(letter:str,request:Request):
    body=await request.json()
    path=(WORK/body['path']).resolve()
    assert path.is_relative_to(WORK.resolve())
    content=path.read_text()
    trace('read_file',**identity(request,letter,'read_file'),content=content)
    return {'content':content}


if __name__=='__main__':
    assert ROOT==Path('/tmp/lhr-terminal-gate'),'Run only inside the disposable fixture directory'
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8770)

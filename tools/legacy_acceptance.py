"""Run only in the disposable owui-router-0111-test; secrets never leave /tmp."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import urllib.request
from uuid import uuid4

import parallel_gate as gate

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'private-state.json'
gate.ROOT=ROOT; gate.TOKEN=ROOT/'admin-token'; gate.STATE=STATE


def state(): return json.loads(STATE.read_text())
def save(data): gate.private_json(STATE,data)


def disposable():
    assert ROOT==Path('/tmp/lhr-0111-gate')
    assert os.environ.get('LEGACY_DISPOSABLE')=='owui-router-0111-test'
    with urllib.request.urlopen('http://127.0.0.1:8080/api/version') as response:
        assert json.load(response)['version']=='0.11.1'
    assert not Path('/work').exists(), 'HTTP fixture must have no source checkout'


def bootstrap():
    disposable()
    assert not STATE.exists()
    password=secrets.token_urlsafe(28)
    request=urllib.request.Request('http://127.0.0.1:8080/api/v1/auths/signup',
        data=json.dumps({'name':'Legacy fixture admin','email':'legacy-admin@example.test','password':password}).encode(),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request) as response: admin=json.load(response)
    assert admin['role']=='admin'
    gate.private_json(gate.TOKEN,admin['token'])
    # TOKEN is a raw token file, not JSON.
    gate.TOKEN.write_text(admin['token']); gate.TOKEN.chmod(0o600)
    save({'admin':{**admin,'password':password},'users':{},'runs':[]})
    print('Isolated 0.11.1 administrator created')


def create(collection,form):
    if collection!='auths':
        assert form['id'].startswith('legacy')
        existing=gate.api('/api/v1/'+collection+('/export' if collection=='models' else '/'))
        if collection=='models': existing+=gate.api('/api/v1/models/base')
        if any(row['id']==form['id'] for row in existing):
            path='/api/v1/models/model/update' if collection=='models' else '/api/v1/'+collection+'/id/'+form['id']+'/update'
            return gate.api(path,form)
    return gate.api('/api/v1/'+collection+('/add' if collection=='auths' else '/create'),form)


async def set_owners(value):
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.models import Model
    from open_webui.models.tools import Tool
    from open_webui.models.skills import Skill
    from open_webui.models.tools import Tools
    for name in ('one','two'):
        await Tools.update_user_valves_by_id_and_user_id('legacy_lookup',value['users'][name]['id'],{'region':name})
    async with get_async_db_context() as session:
        for table,ids,owner in ((Model,['legacy-agent-a','legacy-agent-b'],value['users']['owner']['id']),
                               (Tool,['legacy_lookup'],value['users']['owner']['id']),
                               (Skill,['legacy-work-a','legacy-work-b'],value['users']['owner']['id'])):
            for identifier in ids:
                row=await session.get(table,identifier); assert row is not None; row.user_id=owner
        await session.commit()


LOCAL_TOOL='''from pydantic import BaseModel
import json
class Tools:
    class UserValves(BaseModel):
        region: str = 'default'
    async def legacy_lookup(self, __user__:dict, __messages__:list, __files__:list, __metadata__:dict)->str:
        assert 'LEGACY_ROUTER_PROMPT' not in str(__messages__)
        return json.dumps({'marker':'LOCAL_OK','actor':__user__['id'],'region':__user__['valves'].region,
                           'files':__files__,'chat_id':__metadata__['chat_id']})
'''


def install():
    disposable(); value=state()
    assert not value.get('installed')
    if 'snapshot' not in value:
        value['snapshot']={'openai':gate.api('/openai/config'),'config':gate.api('/api/v1/configs/export'),
                           'servers':gate.api('/api/v1/configs/tool_servers'),'terminals':gate.api('/api/v1/configs/terminal_servers')}
        save(value)
    for name in ('owner','one','two'):
        if name in value['users']: continue
        password=secrets.token_urlsafe(28)
        person=create('auths',{'name':'Legacy '+name,'email':'legacy-'+name+'@example.test','password':password,'role':'user'})
        # Auth add response differs by release; sign in as the real actor.
        person=gate.api('/api/v1/auths/signin',{'email':'legacy-'+name+'@example.test','password':password})
        value['users'][name]={**person,'password':password}; save(value)
    grants=[{'principal_type':'user','principal_id':value['users'][n]['id'],'permission':'read'} for n in ('one','two')]
    owner_grant=[{'principal_type':'user','principal_id':value['users']['owner']['id'],'permission':'read'}]
    for letter in 'ab':
        for prefix,text in (('route','Read profile and call lite_delegate alone'),('work','LEGACY_WORK_'+letter.upper())):
            create('skills',{'id':'legacy-'+prefix+'-'+letter,'name':'legacy-'+prefix+'-'+letter,
                'description':'Legacy '+prefix+' '+letter,'content':text,'meta':{},'access_grants':[]})
    create('tools',{'id':'legacy_lookup','name':'Legacy private lookup','content':LOCAL_TOOL,'meta':{},'access_grants':[]})
    for identifier,filename in (('legacy_preparation','router_preparation.py'),('legacy_pipe','lite_handoff_router.py')):
        content=(ROOT/'dist'/filename).read_text()
        if identifier=='legacy_preparation':
            content=content.replace('AGENTS: dict[str, str] = {}',"AGENTS: dict[str, str] = {'legacy-agent-a':'legacy-route-a','legacy-agent-b':'legacy-route-b'}")
        row=create('functions',{'id':identifier,'name':identifier,'content':content,'meta':{}})
        if not row['is_active']: gate.api('/api/v1/functions/id/'+identifier+'/toggle',{})
    gate.api('/api/v1/functions/id/legacy_pipe/valves/update',{'orchestrator_model_id':'legacy-router-llm'})
    gate.api('/api/v1/functions/id/legacy_preparation/valves/update',{'max_parallel_tasks':8})
    for identifier in ('legacy_pipe','legacy-router-llm','legacy-a','legacy-b'):
        create('models',{'id':identifier,'name':identifier,'params':{},'meta':{'hidden':True},'access_grants':grants})
    for letter in 'ab':
        meta={'hidden':True,'toolIds':['legacy_lookup','server:openapi:legacy-openapi|server_echo','server:mcp:legacy-mcp'],
              'skillIds':['legacy-work-'+letter]}
        if letter=='a': meta['lite_handoff']={'terminal_id':'legacy-terminal-a'}
        create('models',{'id':'legacy-agent-'+letter,'name':'Legacy agent '+letter,'base_model_id':'legacy-'+letter,
            'params':{'system':'LEGACY_'+letter.upper()+'_PROMPT','temperature':.21 if letter=='a' else .41,'function_calling':'native'},
            'meta':meta,'access_grants':grants})
    create('models',{'id':'legacy-router','name':'OWUI 0.11.1 Router acceptance','base_model_id':'legacy_pipe',
        'params':{'system':'LEGACY_ROUTER_PROMPT','function_calling':'native'},
        'meta':{'filterIds':['legacy_preparation'],'toolIds':[],'skillIds':[]},'access_grants':grants})
    asyncio.run(set_owners(value))
    gate.api('/openai/config/update',{**value['snapshot']['openai'],'ENABLE_OPENAI_API':True,
        'OPENAI_API_BASE_URLS':['http://127.0.0.1:8774/v1'],'OPENAI_API_KEYS':['fixture']})
    gate.api('/api/v1/configs/import',{'config':{'subagents.enable':True,'subagents.max_iterations':20,
        'chat.context_compaction.enable':False,'ui.enable_signup':False}})
    common={'url':'http://127.0.0.1:8774','path':'','auth_type':'session','key':'',
        'headers':{'X-Actor':'{{USER_ID}}'},'config':{'enable':True,'access_grants':owner_grant}}
    servers=[{**common,'url':common['url']+'/server','path':'openapi.json','type':'openapi','info':{'id':'legacy-openapi'},
              'config':{**common['config'],'function_name_filter_list':'server_echo'}},
             {**common,'url':common['url']+'/mcp/','type':'mcp','info':{'id':'legacy-mcp'}}]
    gate.api('/api/v1/configs/tool_servers',{'TOOL_SERVER_CONNECTIONS':servers})
    terminals=[{'id':'legacy-terminal-'+name,'name':'Legacy '+name,'url':'http://127.0.0.1:8774/terminal',
        'server_type':'orchestrator','policy_id':'a','enabled':True,'auth_type':'none',
        'config':{'access_grants':grants if name=='public' else owner_grant,'contexts':{'chat':{'context_id':'chat_id'}}}}
        for name in ('a','public')]
    gate.api('/api/v1/configs/terminal_servers',{'TERMINAL_SERVER_CONNECTIONS':terminals})
    gate.api('/api/models')
    value['installed']=True; save(value)
    print(json.dumps({'installed':True,'actors':2,'non_admin_owner':True,'source_checkout':False}))


def upload(user):
    value=state(); token=value['users'][user]['token']
    boundary='lhr-'+uuid4().hex
    content=('ACTOR_FILE_'+user).encode()
    payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="actor-{user}.bin"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n').encode()+content+f'\r\n--{boundary}--\r\n'.encode()
    request=urllib.request.Request('http://127.0.0.1:8080/api/v1/files/?process=false',data=payload,
        headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary})
    with urllib.request.urlopen(request) as response: file=json.load(response)
    path='/api/v1/files/'+file['id']+'/content'
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080'+path,headers={'Authorization':'Bearer '+token})) as response:
        assert response.read()==content
    return {'type':'file','id':file['id'],'name':file['filename'],'url':path}


def start(question,user='one',selected=False,attachment=False,continuation=None):
    value=state(); token=value['users'][user]['token']; gate.api('/api/models',token=token)
    uid,mid=str(uuid4()),str(uuid4()); question+=' run='+mid
    history=[]
    if continuation:
        chat=gate.api('/api/v1/chats/'+continuation['chat_id'],token=token)['chat']
        history=list(chat['history']['messages'].values())
    body={'model':'legacy-router','stream':True,'session_id':'legacy-'+uuid4().hex,'id':mid,'parent_id':None,'tools':[],
        'params':{'tool_approval_mode':'full'},'messages':history+[{'role':'user','content':question}],
        'user_message':{'id':uid,'parentId':continuation['message_id'] if continuation else None,'childrenIds':[mid],
            'role':'user','content':question,'models':['legacy-router'],'timestamp':int(time.time())}}
    file=upload(user) if attachment else None
    if file: body['files']=[file]; body['user_message']['files']=[file]
    if selected: body['terminal_id']='legacy-terminal-public'
    if continuation: body['chat_id']=continuation['chat_id']
    result=gate.api('/api/chat/completions',body,token=token)
    return {'chat_id':result['chat_id'],'message_id':mid,'user':user,'question':question,'file_id':file['id'] if file else None}


def remember(run):
    value=state(); value['runs'].append(run); save(value)


def finish(run):
    token=state()['users'][run['user']]['token']
    for _ in range(300):
        chat=gate.api('/api/v1/chats/'+run['chat_id'],token=token)
        message=chat['chat']['history']['messages'].get(run['message_id'],{})
        if message.get('done'): return message
        time.sleep(.1)
    raise AssertionError('Legacy request timeout: '+run['chat_id'])


def traces(): return [json.loads(line) for line in (ROOT/'trace.jsonl').read_text().splitlines()]


def private_access():
    for name in ('one','two'):
        token=state()['users'][name]['token']
        assert not any(t['id']=='legacy_lookup' for t in gate.api('/api/v1/tools/',token=token))
        for path in ('/api/v1/skills/id/legacy-work-a','/api/v1/terminals/legacy-terminal-a/files/cwd'):
            try: gate.api(path,token=token)
            except RuntimeError as error: assert any(': '+str(code)+':' in str(error) for code in (401,403,404))
            else: raise AssertionError('Private direct access unexpectedly allowed')


def check():
    disposable(); assert state().get('installed'); private_access(); reports=[]
    for question in ('handoff terminal','ordinary handoff','general'):
        run=start(question,selected=question.startswith('handoff'),attachment=question!='general'); remember(run); message=finish(run)
        assert not message.get('error'),message
        expected='LEGACY_GENERAL_OK' if question=='general' else 'LEGACY_FINAL_B_OK' if 'ordinary' in question else 'LEGACY_FINAL_A_OK'
        assert expected in message.get('content',''),message
        reports.append({**run,'marker':expected})
    with ThreadPoolExecutor(max_workers=2) as pool: runs=list(pool.map(lambda n:start('handoff concurrent',n,attachment=True),('one','two')))
    for run in runs: remember(run)
    with ThreadPoolExecutor(max_workers=2) as pool: messages=list(pool.map(finish,runs))
    for run,message in zip(runs,messages):
        assert 'LEGACY_FINAL_A_OK' in message.get('content','') and not message.get('error'),message
        reports.append({**run,'marker':'LEGACY_FINAL_A_OK'})
    # The next actual message in the same saved chat starts Router again.
    follow=start('general followup',continuation=reports[0]); remember(follow); follow_message=finish(follow)
    assert follow['chat_id']==reports[0]['chat_id']
    assert 'LEGACY_GENERAL_OK' in follow_message.get('content','') and not follow_message.get('error'),follow_message
    reports.append({**follow,'marker':'LEGACY_GENERAL_OK'})
    for run in reports:
        message=gate.api('/api/v1/chats/'+run['chat_id'],token=state()['users'][run['user']]['token'])['chat']['history']['messages'][run['message_id']]
        outputs={item['call_id']:item for item in message.get('output',[]) if item.get('type')=='function_call_output'}
        calls=[item for item in message.get('output',[]) if item.get('type')=='function_call']
        assert all(item['name'] not in {'lite_parallel','delegate_task','read_skill_file','grep_skill_files'} for item in calls)
        if run.get('file_id'):
            local=next(item for item in calls if item['name']=='legacy_lookup')
            result=json.loads(''.join(p.get('text','') for p in outputs[local['call_id']]['output']))
            assert result['actor']==state()['users'][run['user']]['id'] and result['region']==run['user']
            assert run['file_id'] in str(result['files'])
            other='two' if run['user']=='one' else 'one'
            try: gate.api('/api/v1/files/'+run['file_id'],token=state()['users'][other]['token'])
            except RuntimeError as error: assert any(': '+str(code)+':' in str(error) for code in (401,403,404))
            else: raise AssertionError('Attachment crossed actor boundary')
        records=[r for r in traces() if r.get('chat_id')==run['chat_id'] and r['kind'] in {'command_start','read_file'}]
        for record in records:
            assert record['actor']==state()['users'][run['user']]['id'] and record['context']=='chat:'+run['chat_id']
    private_access()
    evidence={'version':'0.11.1','backend_passed':True,'runs':reports,'production_modified':False,
        'exports':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'dist').glob('*.py')}}
    gate.private_json(ROOT/'legacy-evidence.json',evidence)
    print(json.dumps({'backend_passed':True,'runs':len(reports)}))


BREAKER = """from pydantic import BaseModel
import json
from pathlib import Path
class Filter:
    class Valves(BaseModel):
        mode: str = 'replace'
    def __init__(self): self.valves=self.Valves()
    async def inlet(self, body:dict, __request__, __metadata__:dict, __user__:dict):
        tools=body['metadata']['tools']
        if self.valves.mode=='refresh':
            from open_webui.utils.tools import get_updated_tool_function
            reader=await get_updated_tool_function(tools['view_skill']['callable'],{'__user__':__user__})
            try: await reader(id='legacy-work-b')
            except ValueError as error:
                assert 'model scope' in str(error)
                with Path('/tmp/lhr-0111-gate/inlet-evidence.jsonl').open('a') as out:
                    out.write(json.dumps({'native_refresh_scope_blocked':True})+'\\n')
            else: raise RuntimeError('Refresh bypassed model scope')
            return body
        async def unrestricted(**kwargs): return 'UNRESTRICTED'
        name='run_command' if self.valves.mode=='terminal' else 'view_skill'
        tools[name]['callable']=unrestricted
        return body
"""


def controls():
    disposable(); value=state(); outcomes=[]
    original_model=gate.api('/api/v1/models/model?id=legacy-agent-a')
    original_servers=gate.api('/api/v1/configs/tool_servers')
    original_terminals=gate.api('/api/v1/configs/terminal_servers')
    def copy(item): return json.loads(json.dumps(item))
    def negative(label):
        run=start('handoff negative '+label); remember(run); message=finish(run)
        assert message.get('error'),message
        assert not any(r['kind']=='provider' and r['model']=='legacy-a' and run['message_id'] in r['question'] for r in traces())
        assert not any(r['kind']=='command_start' and r['chat_id']==run['chat_id'] for r in traces())
        assert gate.children(run['chat_id'])==[]
        outcomes.append({'case':label,'error':message['error'],'no_agent_generation':True,'no_terminal_dispatch':True})
    try:
        model=copy(original_model); model['meta']['toolIds']=['legacy-missing-tool']
        gate.api('/api/v1/models/model/update',model); negative('missing resource')
        gate.api('/api/v1/models/model/update',original_model)
        servers=copy(original_servers); servers['TOOL_SERVER_CONNECTIONS'][0]['config']['enable']=False
        gate.api('/api/v1/configs/tool_servers',servers); negative('disabled server')
        gate.api('/api/v1/configs/tool_servers',original_servers)
        terminals=copy(original_terminals); terminals['TERMINAL_SERVER_CONNECTIONS'][0]['config']['access_grants']=[]
        gate.api('/api/v1/configs/terminal_servers',terminals); negative('revoked terminal owner access')
        gate.api('/api/v1/configs/terminal_servers',original_terminals)
        servers=copy(original_servers); servers['TOOL_SERVER_CONNECTIONS'][0]['auth_type']='system_oauth'
        gate.api('/api/v1/configs/tool_servers',servers); negative('missing actor OAuth')
        gate.api('/api/v1/configs/tool_servers',original_servers)
        # Actual server-side owner change, not an ignored client owner field.
        async def owner(identifier):
            from open_webui.internal.db import get_async_db_context
            from open_webui.models.models import Model
            async with get_async_db_context() as db:
                row=await db.get(Model,'legacy-agent-a'); row.user_id=identifier; await db.commit()
        asyncio.run(owner(value['users']['two']['id']))
        try: negative('owner changed')
        finally: asyncio.run(owner(value['users']['owner']['id']))
        row=create('functions',{'id':'legacy_breaker','name':'Legacy inlet control','content':BREAKER,'meta':{}})
        if not row['is_active']: gate.api('/api/v1/functions/id/legacy_breaker/toggle',{})
        model=copy(original_model); model['meta']['filterIds']=['legacy_breaker']
        gate.api('/api/v1/models/model/update',model)
        for mode in ('replace','terminal'):
            gate.api('/api/v1/functions/id/legacy_breaker/valves/update',{'mode':mode}); negative('inlet '+mode)
        gate.api('/api/v1/functions/id/legacy_breaker/valves/update',{'mode':'refresh'})
        run=start('handoff refresh scope'); remember(run); message=finish(run)
        assert 'LEGACY_FINAL_A_OK' in message.get('content','') and not message.get('error'),message
        assert 'native_refresh_scope_blocked' in (ROOT/'inlet-evidence.jsonl').read_text()
        outcomes.append({'case':'native refresh in destination inlet','scope_blocked':True})
    finally:
        gate.api('/api/v1/models/model/update',original_model)
        gate.api('/api/v1/configs/tool_servers',original_servers)
        gate.api('/api/v1/configs/terminal_servers',original_terminals)
    run=start('force parallel'); remember(run); message=finish(run)
    assert 'Parallel tasks are unavailable' in str(message.get('error')),message
    assert gate.children(run['chat_id'])==[]
    outcomes.append({'case':'generated unsupported parallel call','explicit_error':True,'child_dispatch':False})
    for kind in ('terminal','mcp'):
        run=start('handoff slow '+kind); remember(run)
        for _ in range(200):
            records=traces()
            started=any((r['kind']=='command_start' and r.get('chat_id')==run['chat_id']) if kind=='terminal'
                        else (r['kind']=='mcp_start' and run['message_id'] in r.get('value','')) for r in records)
            if started: break
            time.sleep(.05)
        else: raise AssertionError('Remote operation did not start: '+kind)
        gate.api('/api/tasks/chat/'+run['chat_id']+'/stop',{},token=value['users']['one']['token'])
        count=sum(r['kind']=='provider' and run['message_id'] in r['question'] for r in traces())
        time.sleep(4)
        assert gate.api('/api/tasks/chat/'+run['chat_id'],token=value['users']['one']['token'])['task_ids']==[]
        records=traces()
        assert sum(r['kind']=='provider' and run['message_id'] in r['question'] for r in records)==count
        if kind=='terminal':
            assert sum(r['kind']=='command_start' and r.get('chat_id')==run['chat_id'] for r in records)==1
            assert not any(r['kind']=='read_file' and r.get('chat_id')==run['chat_id'] for r in records)
        else:
            assert sum(r['kind']=='mcp_start' and run['message_id'] in r.get('value','') for r in records)==1
            assert not any(r['kind']=='command_start' and r.get('chat_id')==run['chat_id'] for r in records)
        assert gate.children(run['chat_id'])==[]
        outcomes.append({'case':'Stop during '+kind,'no_retry':True,'no_late_generation':True,'child_dispatch':False})
    for run in state()['runs']: assert gate.children(run['chat_id'])==[]
    gate.private_json(ROOT/'legacy-controls.json',{'passed':True,'outcomes':outcomes})
    print(json.dumps({'controls_passed':True,'cases':len(outcomes)}))


def restore():
    disposable(); value=state(); snapshot=value['snapshot']
    gate.api('/openai/config/update',snapshot['openai'])
    gate.api('/api/v1/configs/import',{'config':snapshot['config']})
    gate.api('/api/v1/configs/tool_servers',snapshot['servers'])
    gate.api('/api/v1/configs/terminal_servers',snapshot['terminals'])
    assert gate.api('/openai/config')==snapshot['openai']
    assert gate.api('/api/v1/configs/tool_servers')==snapshot['servers']
    assert gate.api('/api/v1/configs/terminal_servers')==snapshot['terminals']
    value['restored']=True; save(value)
    print('Isolated legacy configuration restored')


def cleanup():
    disposable(); value=state(); assert value.get('restored')
    for run in value['runs']:
        token=value['users'][run['user']]['token']
        assert gate.api('/api/tasks/chat/'+run['chat_id'],token=token)['task_ids']==[]
    for identifier in ['legacy-router','legacy-agent-a','legacy-agent-b','legacy_pipe','legacy-router-llm','legacy-a','legacy-b']:
        gate.api('/api/v1/models/model/delete',{'id':identifier})
    for collection,ids in (('tools',['legacy_lookup']),('skills',['legacy-'+prefix+'-'+letter for prefix in ('route','work') for letter in 'ab']),
                           ('functions',['legacy_preparation','legacy_pipe','legacy_breaker'])):
        existing={row['id'] for row in gate.api('/api/v1/'+collection+'/')}
        for identifier in ids:
            if identifier in existing: gate.api('/api/v1/'+collection+'/id/'+identifier+'/delete',method='DELETE')
    cleanup_files()
    for user in value['users'].values(): gate.api('/api/v1/users/'+user['id'],method='DELETE')
    for path in (ROOT/'work').glob('*.txt'): path.unlink()
    value['cleaned']=True; save(value)
    print(json.dumps({'cleaned':True,'configuration_restored':True,'fixture_users':3}))


def cleanup_files():
    disposable()
    identifiers={run['file_id'] for run in state()['runs'] if run.get('file_id')}
    for identifier in identifiers:
        try: gate.api('/api/v1/files/'+identifier,method='DELETE')
        except RuntimeError as error:
            if ': 404:' not in str(error): raise
    print(json.dumps({'fixture_attachments_removed':len(identifiers)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('action',choices=('bootstrap','install','check','controls','restore','cleanup','cleanup_files'))
    globals()[parser.parse_args().action]()

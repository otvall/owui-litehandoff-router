"""Product export acceptance on the disposable parallel gate fixtures."""

import argparse
import json
from pathlib import Path
import secrets
import time
import urllib.request
import urllib.error
from uuid import uuid4

import parallel_gate as gate

ROOT = Path(__file__).resolve().parent

PRESENTATION_TOOL = '''class Tools:
    async def make_presentation(self, __request__, __user__: dict, __messages__: list, __files__: list,
                                __event_emitter__=None) -> str:
        import json
        from io import BytesIO
        from html import escape
        from fastapi import UploadFile
        from open_webui.models.users import Users
        from open_webui.routers.files import upload_file_handler, get_file_by_id
        from open_webui.storage.provider import Storage
        from pathlib import Path
        packet = next(m['content'] for m in __messages__ if m['role']=='user'
                      and str(m.get('content','')).startswith('Confirmed parallel task results.'))
        values = json.loads(packet.split('\\n',1)[1])
        assert len(values)==2 and all(len(v['result'])>3000 for v in values)
        assert {f['id'] for v in values for f in v['files']} <= {f.get('id') for f in __files__}
        assert 'PGATE_C_PROMPT' in str(__messages__) and 'OWN_WORKING_A' not in str(__messages__)
        document = '<!doctype html><meta charset="utf-8"><title>Forecast presentation</title>'
        for v in values:
            document += '<section><h1>'+escape(v['agent_id'])+'</h1><p>'+escape(v['result'])+'</p></section>'
        user = await Users.get_user_by_id(__user__['id'])
        for value in values:
            for artifact in value['files']:
                source = await get_file_by_id(artifact['id'], user=user, db=None)
                assert b',42' in Path(Storage.get_file(source.path)).read_bytes()
        row = await upload_file_handler(__request__, file=UploadFile(file=BytesIO(document.encode()),filename='presentation.html'),
                                        metadata={}, process=False, process_in_background=False, user=user)
        file = {'type':'file','id':row.id,'name':'presentation.html','url':'/api/v1/files/'+row.id+'/content'}
        await __event_emitter__({'type':'files','data':{'files':[file]}})
        return json.dumps({'presentation':file,'forecasts':len(values)})
'''


def upsert(collection, identifier, form):
    rows = gate.api('/api/v1/' + collection + ('/all' if collection == 'models' else '/'))
    exists = any(r['id'] == identifier for r in rows)
    path = ('/api/v1/models/model/update' if exists else '/api/v1/models/create') if collection == 'models' else (
        '/api/v1/' + collection + '/id/' + identifier + '/update' if exists else '/api/v1/' + collection + '/create')
    return gate.api(path, form)


def install():
    state = json.loads(gate.STATE.read_text())
    if 'user2' not in state:
        password = secrets.token_urlsafe(24)
        user = gate.api('/api/v1/auths/add', {'name':'Parallel Second User','email':'lhr-pgate2@example.com',
                                           'password':password,'role':'user'})
        state['user2'] = {**user,'password':password}
        gate.private_json(gate.STATE,state)
    groups = []
    for index, user in enumerate((state['user'],state['user2']),1):
        name = 'Parallel team ' + str(index)
        existing = gate.api('/api/v1/groups/')
        existing = existing.get('groups',existing) if isinstance(existing,dict) else existing
        group = next((g for g in existing if g['name']==name),None)
        if not group:
            group = gate.api('/api/v1/groups/create',{'name':name,'description':'Disposable parallel acceptance','permissions':{}})
        gate.api('/api/v1/groups/id/'+group['id']+'/users/add',{'user_ids':[user['id']]})
        groups.append(group['id'])
    all_grants = [{'principal_type':'group','principal_id':g,'permission':'read'} for g in groups]
    first_grant = all_grants[:1]
    for collection in ('tools','skills','models'):
        rows = gate.api('/api/v1/'+collection+('/all' if collection=='models' else '/'))
        for row in rows:
            identifier = row['id']
            if identifier in {'pgate_lookup_a','work-a-pgate','route-a-pgate','pgate-a','pgate-router'}:
                full = gate.api('/api/v1/'+collection+'/id/'+identifier) if collection!='models' else row
                upsert(collection,identifier,{**full,'access_grants':all_grants})
            if identifier in {'agent-a-pgate','agent-b-pgate'}:
                upsert('models',identifier,{**row,'access_grants':all_grants if identifier=='agent-a-pgate' else first_grant})
    for identifier, content in (('lhr_parallel_preparation',(ROOT/'dist/router_preparation.py').read_text()),
                                ('lhr_parallel_pipe',(ROOT/'dist/lite_handoff_router.py').read_text())):
        if 'preparation' in identifier:
            content = content.replace('AGENTS: dict[str, str] = {}',
                "AGENTS: dict[str, str] = {'agent-a-pgate':'route-a-pgate','agent-b-pgate':'route-b-pgate','agent-c-pgate':'route-c-pgate'}")
        row = upsert('functions',identifier,{'id':identifier,'name':identifier,'content':content,'meta':{}})
        if not row['is_active']:
            gate.api('/api/v1/functions/id/'+identifier+'/toggle',{})
    for letter in 'ab':
        identifier='pgate_inlet_'+letter
        content='''class Filter:
    async def inlet(self, body: dict, __model__: dict, __metadata__: dict):
        assert body['model']=='agent-LETTER-pgate'
        assert 'PGATE_UPPER_PROMPT' in str(body['messages'])
        assert 'PGATE_ROUTER_PROMPT' not in str(body['messages'])
        assert 'profile_lookup' in __metadata__['tools']
        assert not {'lite_parallel','lite_delegate','delegate_task'} & set(__metadata__['tools'])
        body['messages'].append({'role':'system','content':'INLET_UPPER'})
        return body
'''.replace('LETTER',letter).replace('UPPER',letter.upper())
        row=upsert('functions',identifier,{'id':identifier,'name':identifier,'content':content,'meta':{}})
        if not row['is_active']: gate.api('/api/v1/functions/id/'+identifier+'/toggle',{})
        model=next(r for r in gate.api('/api/v1/models/all') if r['id']=='agent-'+letter+'-pgate')
        upsert('models',model['id'],{**model,'meta':{**model['meta'],'filterIds':[identifier]}})
    gate.api('/api/v1/functions/id/lhr_parallel_pipe/valves/update',{'orchestrator_model_id':'pgate-router'})
    upsert('tools','pgate_presentation',{'id':'pgate_presentation','name':'Presentation fixture','content':PRESENTATION_TOOL,
                                       'meta':{},'access_grants':all_grants})
    for identifier in ('route-c-pgate','work-c-pgate'):
        upsert('skills',identifier,{'id':identifier,'name':identifier,'description':'Presentation task',
            'content':'Use lite_delegate for final presentation; use lite_parallel for independent forecasts.',
            'meta':{},'access_grants':all_grants})
    for identifier, base, prompt, temperature, meta in (
        ('pgate-c',None,'',0.5,{'hidden':True}),
        ('lhr_parallel_pipe',None,'',0.5,{'hidden':True}),
        ('agent-c-pgate','pgate-c','PGATE_C_PROMPT',0.1,{'hidden':True,'toolIds':['pgate_presentation'],'skillIds':['work-c-pgate']}),
        ('lhr-parallel','lhr_parallel_pipe','PGATE_ROUTER_PROMPT',0.8,
         {'filterIds':['lhr_parallel_preparation'],'toolIds':[],'skillIds':[]}),
    ):
        upsert('models',identifier,{'id':identifier,'name':identifier,'base_model_id':base,
            'params':{'system':prompt,'temperature':temperature,'function_calling':'native'},'meta':meta,'access_grants':all_grants})
    servers = gate.api('/api/v1/configs/tool_servers')
    for server in servers['TOOL_SERVER_CONNECTIONS']:
        if (server.get('info') or {}).get('id')=='pgate_a':
            server['config']['access_grants']=all_grants
    gate.api('/api/v1/configs/tool_servers',servers)
    gate.api('/api/models')
    gate.private_json(ROOT/'product-groups.json',{'groups':groups,'users':[state['user']['id'],state['user2']['id']]})
    print('Product exports installed; two test groups configured')


def start(question='parallel product', user_key='user', chat_id=None, messages=None, parent_id=None):
    token = json.loads(gate.STATE.read_text())[user_key]['token']
    uid,aid=str(uuid4()),str(uuid4())
    question += ' run='+aid
    body={'model':'lhr-parallel','stream':True,'session_id':'parallel-'+uuid4().hex,'id':aid,'parent_id':parent_id,
        'tools':[],'params':{'tool_approval_mode':'full'},'messages':(messages or [])+[{'role':'user','content':question}],
        'user_message':{'id':uid,'parentId':parent_id,'childrenIds':[aid],'role':'user','content':question,
                        'models':['lhr-parallel'],'timestamp':int(time.time())}}
    if chat_id:
        body['chat_id']=chat_id
    result=gate.api('/api/chat/completions',body,token=token)
    run={'chat_id':result['chat_id'],'message_id':aid,'user_key':user_key}
    gate.private_json(ROOT/'last-product-run.json',run)
    return run


def finish(run):
    token=json.loads(gate.STATE.read_text())[run['user_key']]['token']
    for _ in range(400):
        chat=gate.api('/api/v1/chats/'+run['chat_id'],token=token)
        message=chat['chat']['history']['messages'].get(run['message_id'],{})
        if message.get('done'):
            report={**run,'message':message,'children':gate.children(run['chat_id'])}
            gate.private_json(ROOT/('product-'+run['message_id']+'.json'),report)
            print(json.dumps({**run,'content':(message.get('content') or '')[:160],'error':message.get('error'),
                              'children':report['children'],'files':message.get('files')}))
            return report
        time.sleep(.1)
    raise AssertionError('Product run did not finish: '+run['chat_id'])


def group_result(message):
    call=next(o for o in message['output'] if o.get('name')=='lite_parallel')
    output=next(o for o in message['output'] if o['type']=='function_call_output' and o['call_id']==call['call_id'])
    return json.loads(''.join(p.get('text','') for p in output['output']))


def download(url,token):
    with urllib.request.urlopen(urllib.request.Request(gate.BASE+url,headers={'Authorization':'Bearer '+token})) as response:
        return response.read()


def verify(report):
    state=json.loads(gate.STATE.read_text())
    owner=state[report['user_key']]['token']
    other=state['user2' if report['user_key']=='user' else 'user']['token']
    message=report['message']
    assert not message.get('error')
    calls=[o for o in message['output'] if o['type']=='function_call']
    assert [c['name'] for c in calls]==['view_skill','lite_parallel','lite_delegate','make_presentation']
    assert all(c['status']=='completed' for c in calls)
    group=group_result(message)
    assert group['status']=='completed' and len(group['results'])==2
    assert 'ticket' not in json.dumps(group) and 'nonce' not in json.dumps(group)
    expected=['agent-a-pgate','agent-b-pgate'] if report['user_key']=='user' else ['agent-a-pgate']*2
    assert [r['agent_id'] for r in group['results']]==expected
    intervals=[]
    trace=[json.loads(line) for line in (ROOT/'tool-trace.jsonl').read_text().splitlines()]
    ids=[]
    for index,result in enumerate(group['results']):
        assert result['status']=='completed' and len(result['result'])>3000
        cid=result['chat_url'].rsplit('/',1)[1]; ids.append(cid)
        child=gate.api('/api/v1/chats/'+cid,token=owner)
        meta=child['meta']; correlation=meta['lite_parallel']
        assert meta['internal'] is True and meta['type']=='subagent' and meta['parent_chat_id']==report['chat_id']
        assert meta['parent_message_id']==report['message_id'] and correlation['task_index']==index
        assert correlation['agent_id']==result['agent_id']
        saved=child['chat']['history']['messages'][correlation['message_id']]
        own_calls=[o for o in saved['output'] if o['type']=='function_call']
        assert len(own_calls)==9 and [o['status'] for o in own_calls]==['completed']*3+['failed']*3+['completed']*3
        assert saved['content']==result['result'] and saved['done'] and not saved.get('error')
        for file in result['files']:
            assert b',42' in download(file['url'],owner)
            try: download(file['url'],other)
            except urllib.error.HTTPError as error: assert error.code in {401,403,404}
            else: raise AssertionError('Foreign user read worker file')
        try: gate.api('/api/v1/chats/'+cid,token=other)
        except RuntimeError as error: assert any(': '+str(code)+':' in str(error) for code in (401,403,404))
        else: raise AssertionError('Foreign user read child chat')
        points={p['event']:p['time'] for p in trace if p['chat_id']==cid}
        intervals.append(points)
        assert gate.api('/api/tasks/chat/'+cid,token=owner)['task_ids']==[]
    overlap=min(p['end'] for p in intervals)-max(p['start'] for p in intervals)
    assert overlap>0
    listed=gate.api('/api/v1/chats/?include_folders=true',token=owner)
    searched=gate.api('/api/v1/chats/search?text=Assigned%20task',token=owner)
    assert not set(ids)&{c['id'] for c in listed+searched}
    assert len(message.get('sources',[]))==2
    assert len(report['children'])==2
    assert message['files'] and b'<section>' in download(message['files'][0]['url'],owner)
    print(json.dumps({'passed':True,**{k:report[k] for k in ('chat_id','message_id','user_key')},
                      'overlap_seconds':overlap,'children':ids,'file_access_and_isolation':True}))


def check():
    reports=[]
    for user in ('user','user2'):
        report=finish(start(user_key=user))
        verify(report); reports.append(report)
    first=reports[0]
    follow=finish(start('general question after C',chat_id=first['chat_id'],parent_id=first['message_id']))
    assert 'General answer' in follow['message']['content'] and len(follow['children'])==2
    assert not [o for o in follow['message']['output'] if o['type']=='function_call']
    gate.private_json(ROOT/'product-evidence.json',{'passed':True,'runs':[
        {k:r[k] for k in ('chat_id','message_id','user_key')} for r in reports],
        'fresh_router_followup':follow['message_id']})


def lifecycle():
    state=json.loads(gate.STATE.read_text()); owner=state['user']['token']
    for action in ('stop','timeout','delete'):
        before=gate.api('/api/v1/functions/id/lhr_parallel_preparation/valves')
        try:
            if action=='timeout':
                gate.api('/api/v1/functions/id/lhr_parallel_preparation/valves/update',{**before,'parallel_timeout_seconds':2})
            run=start('parallel product slow own answer')
            for _ in range(200):
                ids=gate.children(run['chat_id'])
                if len(ids)==2: break
                time.sleep(.05)
            assert len(ids)==2
            if action=='stop':
                gate.api('/api/tasks/chat/'+run['chat_id']+'/stop',{},token=owner)
            elif action=='delete':
                gate.api('/api/v1/chats/'+run['chat_id'],token=owner,method='DELETE')
            if action!='delete':
                report=finish(run)
                assert len(report['message'].get('sources',[]))==2
                assert not any(o.get('name')=='lite_delegate' for o in report['message'].get('output',[]))
                if action=='timeout':
                    assert group_result(report['message'])['status']=='timed_out'
            for cid in ids:
                assert gate.api('/api/tasks/chat/'+cid,token=owner)['task_ids']==[]
                if action=='delete':
                    try: gate.api('/api/v1/chats/'+cid,token=owner)
                    except RuntimeError as error: assert any(': '+str(code)+':' in str(error) for code in (401,403,404))
                    else: raise AssertionError('Native cascade did not delete child')
            print(json.dumps({'passed':True,'lifecycle':action,**run,'children':ids}))
        finally:
            gate.api('/api/v1/functions/id/lhr_parallel_preparation/valves/update',before)


def controls():
    before=gate.api('/api/v1/functions/id/lhr_parallel_preparation/valves')
    try:
        gate.api('/api/v1/functions/id/lhr_parallel_preparation/valves/update',{**before,'max_parallel_tasks':0})
        report=finish(start('parallel disabled'))
        assert report['children']==[] and 'unavailable' in report['message']['content']
        for question in ('handoff a','handoff b','general question'):
            report=finish(start(question,chat_id=report['chat_id'],parent_id=report['message_id']))
            assert not report['message'].get('error') and report['children']==[]
            if question=='general question': assert 'General answer' in report['message']['content']
            else: assert 'FORECAST_'+question[-1].upper() in report['message']['content']
        print(json.dumps({'passed':True,'disabled_zero_and_same_chat_a_b_router':report['chat_id']}))
    finally:
        gate.api('/api/v1/functions/id/lhr_parallel_preparation/valves/update',before)
    model=next(r for r in gate.api('/api/v1/models/all') if r['id']=='agent-b-pgate')
    try:
        upsert('models',model['id'],{**model,'meta':{**model['meta'],'toolIds':['pgate_missing_required']}})
        report=finish(start('parallel resource error'))
        result=group_result(report['message'])
        assert result['status']=='partial' and result['results'][0]['status']=='completed'
        assert result['results'][1]['status']=='failed' and 'Required model Tools' in result['results'][1]['error']
        assert not any(o.get('name')=='lite_delegate' for o in report['message']['output'])
        print(json.dumps({'passed':True,'missing_required_worker_resource':report['chat_id']}))
    finally:
        upsert('models',model['id'],model)
    report=finish(start('parallel overflow C'))
    assert 'Presentation context length exceeded' in str(report['message'].get('error'))
    assert not any(o.get('name')=='make_presentation' for o in report['message']['output'])
    print(json.dumps({'passed':True,'explicit_destination_overflow':report['chat_id']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['install','run','check','lifecycle','controls'])
    parser.add_argument('--question',default='parallel product')
    parser.add_argument('--user',default='user',choices=['user','user2'])
    args=parser.parse_args()
    if args.action=='install':
        install()
    elif args.action=='check':
        check()
    elif args.action=='lifecycle':
        lifecycle()
    elif args.action=='controls':
        controls()
    else:
        finish(start(args.question,args.user))

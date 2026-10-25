"""Selective terminal acceptance, only in the disposable owui-router-test container."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import time
import urllib.request
import urllib.error
from uuid import uuid4

import parallel_gate as gate

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'terminal-private-state.json'
gate.ROOT=ROOT
gate.TOKEN=ROOT/'admin-token'
gate.STATE=STATE


def state(): return json.loads(STATE.read_text())
def save(value): gate.private_json(STATE,value)


def disposable():
    assert ROOT==Path('/tmp/lhr-terminal-gate')
    assert os.environ.get('TERMINAL_DISPOSABLE')=='owui-router-test'
    assert gate.api('/api/version')['version']=='0.12.3'


def create(collection,form):
    path='/api/v1/'+collection+('/create' if collection!='auths' else '/add')
    return gate.api(path,form)


def ready():
    for _ in range(100):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8770/v1/models',timeout=1) as response:
                if {'tgate-router','tgate-a','tgate-b','tgate-c'} <= {m['id'] for m in json.load(response)['data']}:
                    return
        except (urllib.error.URLError,TimeoutError):
            pass
        time.sleep(.05)
    raise RuntimeError('Disposable terminal provider is not ready')


async def owners(owner):
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.models import Model
    from open_webui.models.tools import Tool
    from open_webui.models.skills import Skill
    async with get_async_db_context() as session:
        for table,ids in ((Model,['tgate-agent-'+c for c in 'abc']),
                          (Tool,['tgate_echo']),(Skill,['tgate-work-'+c for c in 'ab'])):
            for identifier in ids:
                row=await session.get(table,identifier)
                assert row is not None
                row.user_id=owner
        await session.commit()


def install():
    disposable()
    ready()
    assert not STATE.exists(),'Restore/clean previous terminal fixture before a new installation'
    assert not any(m['id'].startswith('tgate') for m in gate.api('/api/v1/models/all'))
    value={'snapshot':{'openai':gate.api('/openai/config'),'config':gate.api('/api/v1/configs/export'),
                       'terminals':gate.api('/api/v1/configs/terminal_servers')},'users':{},'runs':[]}
    save(value)
    for name in ('owner','one','two'):
        password=secrets.token_urlsafe(24)
        user=create('auths',{'name':'Terminal fixture '+name,'email':'tgate-'+name+'@example.test',
                             'password':password,'role':'user'})
        value['users'][name]={**user,'password':password}
        save(value)
    grants=[{'principal_type':'user','principal_id':value['users'][name]['id'],'permission':'read'} for name in ('one','two')]
    for letter in 'abc':
        create('skills',{'id':'tgate-route-'+letter,'name':'Terminal routing '+letter,
            'description':'Execute terminal fixture '+letter,'files':[{'path':'SKILL.md','content':'# Route '+letter}],
            'meta':{},'access_grants':[]})
    for letter in 'ab':
        create('skills',{'id':'tgate-work-'+letter,'name':'Terminal working '+letter,
            'description':'Own working instructions','files':[{'path':'SKILL.md','content':'# WORK_'+letter}],
            'meta':{},'access_grants':[]})
    create('tools',{'id':'tgate_echo','name':'Terminal ordinary control','meta':{},'access_grants':[],
        'content':'''import json
class Tools:
    async def tgate_echo(self, __user__: dict, __metadata__: dict) -> str:
        assert not __metadata__.get('terminal_id')
        return json.dumps({'marker':'B_OK','actor':__user__['id']})
'''})
    for identifier,filename in (('tgate_preparation','router_preparation.py'),('tgate_pipe','lite_handoff_router.py')):
        content=(ROOT/'dist'/filename).read_text()
        if identifier=='tgate_preparation':
            content=content.replace('AGENTS: dict[str, str] = {}',
                "AGENTS: dict[str, str] = {'tgate-agent-a':'tgate-route-a','tgate-agent-b':'tgate-route-b','tgate-agent-c':'tgate-route-c'}")
        row=create('functions',{'id':identifier,'name':identifier,'content':content,'meta':{}})
        if not row['is_active']: gate.api('/api/v1/functions/id/'+identifier+'/toggle',{})
    gate.api('/api/v1/functions/id/tgate_pipe/valves/update',{'orchestrator_model_id':'tgate-router'})
    for identifier in ['tgate_pipe']+['tgate-'+c for c in ('router','a','b','c')]:
        create('models',{'id':identifier,'name':identifier,'base_model_id':None,
            'params':{},'meta':{'hidden':True},'access_grants':grants})
    for letter in 'abc':
        meta={'hidden':True,'toolIds':['tgate_echo'] if letter=='b' else [],
              'skillIds':['tgate-work-'+letter] if letter in 'ab' else []}
        if letter!='b': meta['lite_handoff']={'terminal_id':'tgate-terminal-'+letter}
        create('models',{'id':'tgate-agent-'+letter,'name':'Terminal agent '+letter,
            'base_model_id':'tgate-'+letter,'params':{'system':'TG_'+letter.upper()+'_PROMPT','function_calling':'native'},
            'meta':meta,'access_grants':grants})
    create('models',{'id':'tgate-router-chat','name':'Selective terminal acceptance','base_model_id':'tgate_pipe',
        'params':{'system':'TG_ROUTER_PROMPT','function_calling':'native'},
        'meta':{'filterIds':['tgate_preparation'],'toolIds':[],'skillIds':[]},'access_grants':grants})
    asyncio.run(owners(value['users']['owner']['id']))
    original=value['snapshot']['openai']
    gate.api('/openai/config/update',{**original,'OPENAI_API_BASE_URLS':[*original['OPENAI_API_BASE_URLS'],'http://127.0.0.1:8770/v1'],
        'OPENAI_API_KEYS':[*original['OPENAI_API_KEYS'],'fixture']})
    gate.api('/api/v1/configs/import',{'config':{'subagents.enable':True,'subagents.max_concurrent':4,
        'subagents.max_iterations':20,'chat.context_compaction.enable':False}})
    connections=[]
    for letter in ('a','c','public'):
        allowed=grants if letter=='public' else [{'principal_type':'user','principal_id':value['users']['owner']['id'],'permission':'read'}]
        connections.append({'id':'tgate-terminal-'+letter,'name':'Terminal fixture '+letter,'url':'http://127.0.0.1:8770',
            'server_type':'orchestrator','policy_id':letter,'enabled':True,'auth_type':'none',
            'config':{'access_grants':allowed,'contexts':{'chat':{'context_id':'chat_id'}}}})
    value['connections']=connections
    save(value)
    previous=value['snapshot']['terminals']['TERMINAL_SERVER_CONNECTIONS'] or []
    gate.api('/api/v1/configs/terminal_servers',{'TERMINAL_SERVER_CONNECTIONS':previous+connections})
    gate.api('/api/models')
    print(json.dumps({'installed':True,'production_modified':False,'actors':2,'private_terminals':2}))


def start(question,user='one',selected=False):
    value=state()
    gate.api('/api/models',token=value['users'][user]['token'])
    uid,mid=str(uuid4()),str(uuid4())
    question+=' run='+mid
    body={'model':'tgate-router-chat','stream':True,'session_id':'tgate-'+uuid4().hex,'id':mid,'parent_id':None,
        'tools':[],'messages':[{'role':'user','content':question}],'params':{'tool_approval_mode':'full'},
        'user_message':{'id':uid,'parentId':None,'childrenIds':[mid],'role':'user','content':question,
                        'models':['tgate-router-chat'],'timestamp':int(time.time())}}
    if selected: body['terminal_id']='tgate-terminal-public'
    result=gate.api('/api/chat/completions',body,token=value['users'][user]['token'])
    run={'chat_id':result['chat_id'],'message_id':mid,'user':user,'question':question}
    # Writes happen on the caller thread, including concurrent execution below.
    return run


def remember(run):
    value=state()
    value['runs'].append(run)
    save(value)


def finish(run):
    token=state()['users'][run['user']]['token']
    for _ in range(300):
        chat=gate.api('/api/v1/chats/'+run['chat_id'],token=token)
        message=chat['chat']['history']['messages'].get(run['message_id'],{})
        if message.get('done'):
            return {'run':run,'message':message,'children':gate.children(run['chat_id'])}
        time.sleep(.1)
    raise AssertionError('Terminal acceptance timeout: '+run['chat_id'])


def traces():
    return [json.loads(line) for line in (ROOT/'trace.jsonl').read_text().splitlines()]


def private_access():
    value=state()
    for name in ('one','two'):
        token=value['users'][name]['token']
        available=gate.api('/api/v1/terminals/',token=token)
        assert not {t['id'] for t in available}&{'tgate-terminal-a','tgate-terminal-c'}
        try: gate.api('/api/v1/terminals/tgate-terminal-a/files/cwd',token=token)
        except RuntimeError as error: assert ': 403:' in str(error)
        else: raise AssertionError('Direct terminal access unexpectedly allowed')


def check():
    disposable()
    private_access()
    reports=[]
    for question in ('handoff terminal','mixed terminal','same terminal','general terminal'):
        run=start(question,selected=question.startswith('handoff'))
        remember(run)
        report=finish(run)
        assert not report['message'].get('error'),report
        content=report['message'].get('content','')
        assert ('A_OK' if question.startswith('handoff') else 'GENERAL_OK' if question.startswith('general') else 'FINAL_OK') in content,report
        if question.startswith(('mixed','same')): assert len(report['children'])==2,report
        reports.append(report)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs=list(pool.map(lambda user:start('mixed concurrent',user),('one','two')))
    for run in runs: remember(run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent=list(pool.map(finish,runs))
    for report in concurrent:
        assert 'FINAL_OK' in report['message'].get('content','') and not report['message'].get('error'),report
    reports+=concurrent
    value=state()
    command_records=[r for r in traces() if r['kind']=='command_start']
    for report in reports:
        run=report['run']
        ids={run['chat_id'],*report['children']}
        actual=[r for r in command_records if r.get('chat_id') in ids]
        if 'general' in run['question']: assert not actual
        else:
            assert actual and all(r['actor']==value['users'][run['user']]['id'] for r in actual)
            assert all(r['context']=='chat:'+r['chat_id'] for r in actual)
    private_access()
    evidence={'passed':True,'backend':'terminal-compatible actual subprocess shell; not stock Open Terminal',
              'reports':[{**r['run'],'children':r['children'],'content':r['message'].get('content','')} for r in reports],
              'commands':len(command_records),'production_modified':False}
    gate.private_json(ROOT/'terminal-evidence.json',evidence)
    print(json.dumps({'passed':True,'runs':len(reports),'commands':len(command_records)}))


def controls():
    disposable()
    value=state()
    before=gate.api('/api/v1/configs/terminal_servers')
    try:
        changed=json.loads(json.dumps(before))
        next(c for c in changed['TERMINAL_SERVER_CONNECTIONS'] if c['id']=='tgate-terminal-a')['enabled']=False
        gate.api('/api/v1/configs/terminal_servers',changed)
        run=start('mixed disabled')
        remember(run)
        report=finish(run)
        assert 'FINAL_OK' not in report['message'].get('content','')
        assert not any(r['kind']=='command_start' and r['chat_id'] in report['children'] for r in traces())
    finally: gate.api('/api/v1/configs/terminal_servers',before)
    outcomes=[]
    for mode in ('stop','deadline'):
        if mode=='deadline':
            gate.api('/api/v1/functions/id/tgate_preparation/valves/update',{'parallel_timeout_seconds':1})
        run=start('same slow '+mode)
        remember(run)
        token=value['users']['one']['token']
        for _ in range(150):
            children=gate.children(run['chat_id'])
            if any(r['kind']=='command_start' and r.get('chat_id') in children for r in traces()): break
            time.sleep(.05)
        else: raise AssertionError('Terminal command did not start')
        if mode=='stop': gate.api('/api/tasks/chat/'+run['chat_id']+'/stop',{},token=token)
        else:
            result=finish(run)
            assert 'FINAL_OK' not in result['message'].get('content','')
        time.sleep(6)
        for child in children:
            assert gate.api('/api/tasks/chat/'+child,token=token)['task_ids']==[]
            assert len([r for r in traces() if r['kind']=='command_start' and r.get('chat_id')==child])<=1
            assert not any(r['kind']=='read_file' and r.get('chat_id')==child for r in traces())
        saved=gate.api('/api/v1/chats/'+run['chat_id'],token=token)['chat']['history']['messages'][run['message_id']]
        assert 'FINAL_OK' not in saved.get('content','')
        outcomes.append({'mode':mode,'chat_id':run['chat_id'],'children':children,'no_replay':True})
        gate.api('/api/v1/functions/id/tgate_preparation/valves/update',{'parallel_timeout_seconds':300})
    gate.private_json(ROOT/'terminal-controls.json',{'disabled_resource':True,'lifecycle':outcomes})
    print(json.dumps({'controls':True,'lifecycle_cases':len(outcomes)}))


def revocation():
    disposable()
    before=gate.api('/api/v1/configs/terminal_servers')
    try:
        changed=json.loads(json.dumps(before))
        connection=next(c for c in changed['TERMINAL_SERVER_CONNECTIONS'] if c['id']=='tgate-terminal-a')
        connection['config']['access_grants']=[]
        gate.api('/api/v1/configs/terminal_servers',changed)
        run=start('mixed owner access revoked')
        remember(run)
        report=finish(run)
        assert 'GROUP_partial' in report['message'].get('content',''),report
        call=next(o for o in report['message']['output'] if o.get('name')=='lite_parallel')
        result=next(o for o in report['message']['output'] if o.get('type')=='function_call_output' and o['call_id']==call['call_id'])
        group=json.loads(''.join(p.get('text','') for p in result['output']))
        assert [r['status'] for r in group['results']]==['failed','completed']
        assert 'denied' in group['results'][0]['error']
        assert not any(r['kind']=='command_start' and r.get('chat_id') in report['children'] for r in traces())
        gate.private_json(ROOT/'terminal-revocation.json',{'owner_access_revoked':True,**run,'children':report['children']})
        print(json.dumps({'owner_access_revoked':True,'no_terminal_dispatch':True}))
    finally:
        gate.api('/api/v1/configs/terminal_servers',before)


def visualizer():
    disposable()
    ready()
    value=state()
    token=value['users']['one']['token']
    settings=gate.api('/api/v1/users/user/settings',token=token) or {}
    gate.api('/api/v1/users/user/settings/update',{**settings,'ui':{**settings.get('ui',{}),
        'iframeSandboxAllowSameOrigin':True,'iframeSandboxAllowScripts':True}},token=token)
    assets=ROOT/'fixtures'/'inline_visualizer'
    manifest=json.loads((assets/'manifest.json').read_text())
    for item in manifest['files']:
        if item['path'].startswith('2.2.3/'):
            assert hashlib.sha256((assets/item['path']).read_bytes()).hexdigest()==item['sha256']
    grant=[{'principal_type':'user','principal_id':value['users']['owner']['id'],'permission':'read'}]
    if not any(t['id']=='tgate_visualizer' for t in gate.api('/api/v1/tools/')):
        create('tools',{'id':'tgate_visualizer','name':'Inline Visualizer regression','content':(assets/'2.2.3/tool.py').read_text(),
            'meta':{},'access_grants':grant})
    gate.api('/api/v1/tools/id/tgate_visualizer/valves/update',{'security_level':'offline','chime':False})
    if not any(s['id']=='tgate-visualizer-skill' for s in gate.api('/api/v1/skills/')):
        create('skills',{'id':'tgate-visualizer-skill','name':'visualize','description':'Inline Visualizer 2.2.3',
            'files':[{'path':'SKILL.md','content':(assets/'2.2.3/SKILL.md').read_text()}],'meta':{},'access_grants':grant})
    model=next(m for m in gate.api('/api/v1/models/all') if m['id']=='tgate-agent-b')
    model['meta']['toolIds']=list(dict.fromkeys(model['meta']['toolIds']+['tgate_visualizer']))
    model['meta']['skillIds']=list(dict.fromkeys(model['meta']['skillIds']+['tgate-visualizer-skill']))
    gate.api('/api/v1/models/model/update',model)
    run=start('mixed terminal visualizer: create an interactive SVG visualization')
    remember(run)
    report=finish(run)
    assert 'FINAL_OK' in report['message'].get('content','') and not report['message'].get('error'),report
    assert not report['message'].get('embeds')
    rendered=[]
    for child in report['children']:
        chat=gate.api('/api/v1/chats/'+child,token=token)
        message=chat['chat']['history']['messages'][chat['chat']['history']['currentId']]
        if message.get('embeds'):
            assert len(message['embeds'])==1 and 'TG_VISUALIZER_REGRESSION' in message['content']
            assert any(o.get('name')=='visualize' and o.get('status')=='completed' for o in message['output'])
            rendered.append(child)
    assert len(rendered)==1
    evidence={**run,'visualizer_child':rendered[0],'tool_skill_version':'2.2.3','backend_passed':True,'browser':'pending'}
    gate.private_json(ROOT/'terminal-visualizer-evidence.json',evidence)
    print(json.dumps(evidence))


def restore():
    disposable()
    value=state()
    snapshot=value['snapshot']
    gate.api('/openai/config/update',snapshot['openai'])
    gate.api('/api/v1/configs/import',{'config':snapshot['config']})
    gate.api('/api/v1/configs/terminal_servers',snapshot['terminals'])
    assert gate.api('/openai/config')==snapshot['openai']
    assert gate.api('/api/v1/configs/terminal_servers')==snapshot['terminals']
    value['restored']=True
    save(value)
    print('Shared configuration restored')


def cleanup():
    disposable()
    value=state()
    assert value.get('restored')
    for run in value['runs']:
        token=value['users'][run['user']]['token']
        assert gate.api('/api/tasks/chat/'+run['chat_id'],token=token)['task_ids']==[]
    for identifier in ['tgate-router-chat']+['tgate-agent-'+c for c in 'abc']+['tgate_pipe']+['tgate-'+c for c in ('router','a','b','c')]:
        gate.api('/api/v1/models/model/delete',{'id':identifier})
    for collection,ids in (('tools',['tgate_echo','tgate_visualizer']),('skills',['tgate-route-'+c for c in 'abc']+['tgate-work-'+c for c in 'ab']+['tgate-visualizer-skill']),
                          ('functions',['tgate_preparation','tgate_pipe'])):
        existing={row['id'] for row in gate.api('/api/v1/'+collection+'/')}
        for identifier in ids:
            if identifier in existing: gate.api('/api/v1/'+collection+'/id/'+identifier+'/delete',method='DELETE')
    user_ids=[user['id'] for user in value['users'].values()]
    with sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True) as db:
        files=[row[0] for row in db.execute('SELECT id FROM file WHERE user_id IN ('+
              ','.join('?' for _ in user_ids)+')',user_ids)]
    for identifier in files: gate.api('/api/v1/files/'+identifier,method='DELETE')
    for user in value['users'].values(): gate.api('/api/v1/users/'+user['id'],method='DELETE')
    for path in (ROOT/'work').glob('*.txt'): path.unlink()
    value['cleaned']=True
    save(value)
    print(json.dumps({'cleaned':True,'fixture_users':len(value['users']),'configuration_restored':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['bootstrap','install','check','controls','revocation','visualizer','restore','cleanup'])
    action=parser.parse_args().action
    if action=='bootstrap':
        assert ROOT==Path('/tmp/lhr-terminal-gate')
        asyncio.run(gate.bootstrap())
    else: globals()[action]()

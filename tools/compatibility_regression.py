"""Recheck prepared 0.12.3 product fixtures with new exports, then restore them."""

import ast
import asyncio
import time
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
assert ROOT==Path('/tmp/lhr-0123-compat-regression')
assert os.environ.get('ROUTER_COMPAT_DISPOSABLE')=='owui-router-test'
sys.path.insert(0,'/tmp/lhr-parallel-gate')
import parallel_gate as gate
import parallel_acceptance as product

gate.TOKEN=ROOT/'admin-token'
gate.STATE=ROOT/'parallel-private-state.json'
product.ROOT=ROOT
assert gate.api('/api/version')['version']=='0.12.3'
def cleanup_reports(state):
    # Remove only chats and artifacts recorded in this private directory. Existing
    # prepared models/users/Tools/Skills/MCP services are baseline fixtures.
    parents={}; files={}
    for path in ROOT.glob('product-*.json'):
        report=json.loads(path.read_text())
        if 'message' not in report: continue
        token=state[report['user_key']]['token']; parents[report['chat_id']]=token
        for file in report['message'].get('files') or []: files[file['id']]=token
        if any(o.get('name')=='lite_parallel' for o in report['message'].get('output',[])):
            for result in product.group_result(report['message'])['results']:
                for file in result.get('files') or []: files[file['id']]=token
    for kind,items in (('chats',parents),('files',files)):
        for identifier,token in items.items():
            try: gate.api('/api/v1/'+kind+'/'+identifier,token=token,method='DELETE')
            except RuntimeError as error:
                if ': 404:' not in str(error): raise
    gate.private_json(ROOT/'cleanup.json',{'restored':True,'created_parents_removed':len(parents),'created_files_removed':len(files)})


state=json.loads(Path('/tmp/lhr-parallel-gate/private-state.json').read_text())
for key in ('user','user2'):
    user=state[key]
    user.update(gate.api('/api/v1/auths/signin',{'email':user['email'],'password':user['password']}))
gate.private_json(gate.STATE,state)
if sys.argv[1:]==['cleanup']:
    cleanup_reports(state)
    print('Recorded compatibility chats/files removed')
    sys.exit(0)
if not (ROOT/'tool-trace.jsonl').exists():
    (ROOT/'tool-trace.jsonl').symlink_to('/tmp/lhr-parallel-gate/tool-trace.jsonl')
functions={identifier:gate.api('/api/v1/functions/id/'+identifier)
           for identifier in ('lhr_parallel_preparation','lhr_parallel_pipe')}
valves={identifier:gate.api('/api/v1/functions/id/'+identifier+'/valves') for identifier in functions}
before=gate.api('/openai/config')
gate.private_json(ROOT/'snapshot.json',{'functions':functions,'valves':valves,'openai':before})

async def session_snapshot():
    from sqlalchemy import select
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.oauth_sessions import OAuthSession
    ids=[state[key]['id'] for key in ('user','user2','resource_owner')]
    async with get_async_db_context() as db:
        rows=(await db.execute(select(OAuthSession).where(OAuthSession.user_id.in_(ids),
            OAuthSession.provider.in_(['mcp:pgate_a','mcp:pgate_b'])))).scalars().all()
        return [{c.name:getattr(row,c.name) for c in OAuthSession.__table__.columns} for row in rows]


async def refresh_sessions():
    from open_webui.models.oauth_sessions import OAuthSessions
    for key in ('user','user2','resource_owner'):
        uid=state[key]['id']
        for server in ('pgate_a','pgate_b'):
            await OAuthSessions.delete_sessions_by_user_id_and_provider(uid,'mcp:'+server)
            assert await OAuthSessions.create_session(uid,'mcp:'+server,{'access_token':'resource-fixture-'+uid,'expires_at':int(time.time())+7200})


async def restore_sessions(snapshot):
    from sqlalchemy import delete
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.oauth_sessions import OAuthSession
    ids=[state[key]['id'] for key in ('user','user2','resource_owner')]
    async with get_async_db_context() as db:
        await db.execute(delete(OAuthSession).where(OAuthSession.user_id.in_(ids),OAuthSession.provider.in_(['mcp:pgate_a','mcp:pgate_b'])))
        db.add_all([OAuthSession(**row) for row in snapshot]); await db.commit()


sessions=asyncio.run(session_snapshot())
gate.private_json(ROOT/'oauth-snapshot.json',sessions)

try:
    asyncio.run(refresh_sessions())
    for identifier,old in functions.items():
        name='router_preparation.py' if 'preparation' in identifier else 'lite_handoff_router.py'
        content=(ROOT/'dist'/name).read_text()
        if 'preparation' in identifier:
            tree=ast.parse(old['content'])
            assignment=next(n for n in tree.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='AGENTS')
            catalog=ast.literal_eval(assignment.value)
            content=content.replace('AGENTS: dict[str, str] = {}','AGENTS: dict[str, str] = '+repr(catalog))
        gate.api('/api/v1/functions/id/'+identifier+'/update',{**old,'content':content})
    urls=list(before['OPENAI_API_BASE_URLS']); keys=list(before['OPENAI_API_KEYS'])
    if 'http://127.0.0.1:8768/v1' not in urls:
        urls.append('http://127.0.0.1:8768/v1'); keys.append('fixture')
    gate.api('/openai/config/update',{**before,'OPENAI_API_BASE_URLS':urls,'OPENAI_API_KEYS':keys})
    gate.api('/api/models')
    product.check()
finally:
    asyncio.run(restore_sessions(sessions))
    assert asyncio.run(session_snapshot())==sessions
    for identifier,old in functions.items():
        gate.api('/api/v1/functions/id/'+identifier+'/update',old)
        gate.api('/api/v1/functions/id/'+identifier+'/valves/update',valves[identifier])
        assert gate.api('/api/v1/functions/id/'+identifier)['content']==old['content']
        assert gate.api('/api/v1/functions/id/'+identifier+'/valves')==valves[identifier]
    gate.api('/openai/config/update',before)
    assert gate.api('/openai/config')==before
    gate.api('/api/models')
    cleanup_reports(state)

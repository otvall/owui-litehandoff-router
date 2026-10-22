"""Disposable native parallel probe. Run only inside owui-router-test.

Credentials and configuration snapshots stay in this script's private directory.
This probe never replaces the installed LHR Functions or the native handler.
"""

import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
TOKEN = ROOT / 'admin-token'
STATE = ROOT / 'private-state.json'
BASE = 'http://127.0.0.1:8080'


def private_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(value, output)


def api(path, data=None, *, token=None, method=None):
    request = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Authorization': 'Bearer ' + (token or TOKEN.read_text().strip()),
                 'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'{path}: {error.code}: {error.read().decode()}') from None


async def bootstrap():
    # The Docker entrypoint exports the signing key only to its server process.
    secret = Path('/app/backend/.webui_secret_key')
    if secret.exists():
        os.environ['WEBUI_SECRET_KEY'] = secret.read_text().strip()
    from open_webui.models.auths import Auths
    from open_webui.models.users import Users
    from open_webui.utils.auth import create_token
    admin = await Users.get_user_by_id('f05db527-8cec-4076-9e78-641b6e8201d6')
    assert admin and admin.role == 'admin', 'Expected owner of the disposable test instance'
    auth = await Auths.get_auth_by_id(admin.id)
    assert auth and auth.active
    value = create_token({'id': admin.id, 'typ': 'session', 'auth_method': 'password',
                          'session_stamp': auth.session_stamp, 'auth_time': int(time.time())},
                         expires_delta=timedelta(hours=4))
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(value)


TOOL = '''import asyncio
import io
import json
from pathlib import Path
import time

class Tools:
    async def profile_lookup(self, query: str, __user__: dict, __messages__: list, __metadata__: dict) -> str:
        assert 'PGATE_LETTER_PROMPT' in str(__messages__)
        assert 'PGATE_ROUTER_PROMPT' not in str(__messages__)
        assert 'OTHER_PRIVATE_SKILL' not in str(__messages__)
        trace = Path('/tmp/lhr-parallel-gate/tool-trace.jsonl')
        record = {'agent': 'LETTER', 'event': 'start', 'time': time.monotonic(), 'actor': __user__['id'],
                  'chat_id': __metadata__['chat_id'], 'message_id': __metadata__['message_id']}
        with trace.open('a') as out:
            out.write(json.dumps(record) + '\\n')
        try:
            await asyncio.sleep(60 if 'slow' in query else 1)
            return 'LOOKUP_LETTER_COMPLETE'
        finally:
            record.update(event='end', time=time.monotonic())
            with trace.open('a') as out:
                out.write(json.dumps(record) + '\\n')

    async def profile_finish(self, __request__, __user__: dict, __messages__: list, __metadata__: dict, __event_emitter__=None) -> str:
        assert 'LOOKUP_LETTER_COMPLETE' in str(__messages__)
        from fastapi import UploadFile
        from open_webui.models.users import UserModel
        from open_webui.routers.files import upload_file_handler
        item = await upload_file_handler(__request__, file=UploadFile(filename='forecast-LETTER.csv',
            file=io.BytesIO(b'sector,value\\nLETTER,42\\n')), metadata={}, process=False,
            process_in_background=False, user=UserModel(**__user__))
        if hasattr(item, 'model_dump'):
            item = item.model_dump()
        file = {'type': 'file', 'id': item['id'], 'name': item['filename'],
                'url': '/api/v1/files/' + item['id'] + '/content'}
        await __event_emitter__({'type': 'files', 'data': {'files': [file]}})
        return json.dumps({'forecast': 'LETTER', 'file': file, 'actor': __user__['id']})
'''


def setup():
    assert ROOT == Path('/tmp/lhr-parallel-gate'), 'Run only in the disposable test directory'
    assert api('/api/version')['version'] == '0.12.3'
    admin = api('/api/v1/auths/')
    assert admin['role'] == 'admin'
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if 'openai' not in state:
        state['openai'] = api('/openai/config')
        state['config'] = api('/api/v1/configs/export')
        private_json(STATE, state)
    if 'servers' not in state:
        state['servers'] = api('/api/v1/configs/tool_servers')
        private_json(STATE, state)
    if 'user' not in state:
        password = secrets.token_urlsafe(24)
        user = api('/api/v1/auths/add', {'name': 'Parallel Gate User', 'email': 'lhr-pgate@example.com',
                                      'password': password, 'role': 'user'})
        state['user'] = {**user, 'password': password}
        private_json(STATE, state)
    grant = [{'principal_type': 'user', 'principal_id': state['user']['id'], 'permission': 'read'}]

    def upsert(collection, identifier, form):
        rows = api('/api/v1/' + collection + ('/all' if collection == 'models' else '/'))
        exists = any(row['id'] == identifier for row in rows)
        if collection == 'models':
            path = '/api/v1/models/model/update' if exists else '/api/v1/models/create'
        else:
            path = '/api/v1/' + collection + '/id/' + identifier + '/update' if exists else '/api/v1/' + collection + '/create'
        return api(path, form)

    for letter in 'ab':
        identifier = 'pgate_lookup_' + letter
        upsert('tools', identifier, {'id': identifier, 'name': identifier,
            'content': TOOL.replace('LETTER', letter.upper()), 'meta': {}, 'access_grants': grant})
        for prefix in ('route', 'work'):
            identifier = prefix + '-' + letter + '-pgate'
            upsert('skills', identifier, {'id': identifier, 'name': identifier, 'description': identifier,
                'content': 'OWN_WORKING_' + letter.upper(), 'meta': {}, 'access_grants': grant})
    upsert('skills', 'other-pgate', {'id': 'other-pgate', 'name': 'Other gate skill',
        'description': 'Unattached gate skill', 'content': 'OTHER_PRIVATE_SKILL', 'meta': {}, 'access_grants': grant})
    for identifier, base, prompt, temperature, meta in (
        ('pgate-a', None, '', 0.5, {'hidden': True}),
        ('pgate-b', None, '', 0.5, {'hidden': True}),
        ('pgate-router', None, '', 0.5, {'hidden': True}),
        ('lhr_pgate_pipe', None, '', 0.5, {'hidden': True}),
        ('agent-a-pgate', 'pgate-a', 'PGATE_A_PROMPT', 0.2,
         {'hidden': True, 'toolIds': ['pgate_lookup_a', 'server:mcp:pgate_a'], 'skillIds': ['work-a-pgate']}),
        ('agent-b-pgate', 'pgate-b', 'PGATE_B_PROMPT', 0.4,
         {'hidden': True, 'toolIds': ['pgate_lookup_b', 'server:mcp:pgate_b'], 'skillIds': ['work-b-pgate']}),
    ):
        upsert('models', identifier, {'id': identifier, 'name': identifier, 'base_model_id': base,
            'params': {'system': prompt, 'temperature': temperature, 'function_calling': 'native'},
            'meta': meta, 'access_grants': grant})
    config = state['openai']
    urls = [*config['OPENAI_API_BASE_URLS'], 'http://127.0.0.1:8768/v1']
    assert all(url.startswith('http://127.0.0.1:') for url in urls)
    api('/openai/config/update', {**config, 'OPENAI_API_BASE_URLS': urls,
                                'OPENAI_API_KEYS': [*config['OPENAI_API_KEYS'], 'local-gate']})
    api('/api/v1/configs/import', {'config': {'subagents.enable': True, 'subagents.max_concurrent': 4,
        'subagents.max_output': 100, 'subagents.max_iterations': 20, 'chat.context_compaction.enable': False}})
    connections = [c for c in state['servers']['TOOL_SERVER_CONNECTIONS']
                   if (c.get('info') or {}).get('id') not in {'pgate_a', 'pgate_b'}]
    for letter in 'ab':
        connections.append({'url': 'http://127.0.0.1:8766/mcp/', 'path': '', 'type': 'mcp',
            'auth_type': 'none', 'key': '', 'headers': {'X-Execution-User': '{{USER_ID}}'},
            'config': {'enable': True, 'access_grants': grant},
            'info': {'id': 'pgate_' + letter, 'name': 'Parallel Gate MCP ' + letter.upper()}})
    api('/api/v1/configs/tool_servers', {'TOOL_SERVER_CONNECTIONS': connections})
    print(json.dumps({'configured': True, 'owui': '0.12.3', 'user_id': state['user']['id'],
                      'provider': 'loopback:8768', 'fixtures': ['agent-a-pgate', 'agent-b-pgate', 'other-pgate']}))


def install():
    state = json.loads(STATE.read_text())
    grants = [{'principal_type': 'user', 'principal_id': state['user']['id'], 'permission': 'read'}]
    for identifier, export, fragment in (
        ('lhr_pgate_preparation', 'router_preparation.py', 'parallel_gate_filter.py'),
        ('lhr_pgate_pipe', 'lite_handoff_router.py', 'parallel_gate_pipe.py'),
    ):
        content = (ROOT / 'dist' / export).read_text() + '\n' + (ROOT / fragment).read_text()
        exists = any(row['id'] == identifier for row in api('/api/v1/functions/'))
        form = {'id': identifier, 'name': 'Parallel Gate ' + identifier, 'content': content, 'meta': {}}
        row = api('/api/v1/functions/id/' + identifier + '/update' if exists else '/api/v1/functions/create', form)
        if not row['is_active']:
            api('/api/v1/functions/id/' + identifier + '/toggle', {})
    api('/api/v1/functions/id/lhr_pgate_pipe/valves/update', {'orchestrator_model_id': 'pgate-router'})
    form = {'id': 'lhr-pgate', 'name': 'Parallel Gate', 'base_model_id': 'lhr_pgate_pipe',
        'params': {'system': 'PGATE_ROUTER_PROMPT', 'temperature': 0.8, 'function_calling': 'native'},
        'meta': {'filterIds': ['lhr_pgate_preparation'], 'toolIds': [], 'skillIds': []}, 'access_grants': grants}
    exists = any(row['id'] == 'lhr-pgate' for row in api('/api/v1/models/all'))
    api('/api/v1/models/model/update' if exists else '/api/v1/models/create', form)
    api('/api/models')
    print('Disposable gate Functions installed; LHR Functions unchanged')


def start(question='parallel gate', approval='full'):
    state = json.loads(STATE.read_text())
    token = state['user']['token']
    user_id, answer_id = str(uuid4()), str(uuid4())
    question = question + ' run=' + answer_id
    started = api('/api/chat/completions', {
        'model': 'lhr-pgate', 'stream': True, 'session_id': 'gate-' + uuid4().hex,
        'id': answer_id, 'parent_id': None, 'tools': [], 'params': {'tool_approval_mode': approval},
        'messages': [{'role': 'user', 'content': question}],
        'user_message': {'id': user_id, 'parentId': None, 'childrenIds': [answer_id],
            'role': 'user', 'content': question, 'models': ['lhr-pgate'], 'timestamp': int(time.time())},
    }, token=token)
    chat_id = started['chat_id']
    run = {'chat_id': chat_id, 'message_id': answer_id}
    private_json(ROOT / 'last-run.json', run)
    return run


def finish(run):
    token = json.loads(STATE.read_text())['user']['token']
    chat_id, answer_id = run['chat_id'], run['message_id']
    for _ in range(400):
        chat = api('/api/v1/chats/' + chat_id, token=token)
        message = chat['chat']['history']['messages'].get(answer_id, {})
        if message.get('done'):
            if message.get('error'):
                print(json.dumps({'chat_id': chat_id, 'message_id': answer_id, 'error': message['error']}))
                return
            path = ROOT / ('results-' + answer_id + '.json')
            if not path.exists():
                print(json.dumps({'chat_id': chat_id, 'message_id': answer_id,
                                  'interrupted': True, 'sources': message.get('sources')}))
                return
            report = json.loads(path.read_text())
            print(json.dumps({'chat_id': chat_id, 'message_id': answer_id,
                'results': [{k: v for k, v in r.items() if k not in {'result', 'output'}} |
                            {'result_length': len(r.get('result') or '')} for r in report['results']],
                'sources': message.get('sources')}))
            return
        time.sleep(0.1)
    raise AssertionError('Gate timed out: ' + chat_id)


def run(question='parallel gate', approval='full'):
    finish(start(question, approval))


def verify():
    last = json.loads((ROOT / 'last-run.json').read_text())
    report = json.loads((ROOT / ('results-' + last['message_id'] + '.json')).read_text())
    token = json.loads(STATE.read_text())['user']['token']
    trace = [json.loads(line) for line in (ROOT / 'tool-trace.jsonl').read_text().splitlines()]
    intervals = []
    for result in report['results']:
        assert result['status'] == 'completed' and result['done'] and not result['error']
        calls = [c for c in result['output'] if c['type'] == 'function_call']
        assert [c['name'] for c in calls[:7]] == ['view_skill', 'read_skill_file', 'grep_skill_files',
            'view_skill', 'read_skill_file', 'grep_skill_files', 'profile_lookup']
        assert calls[7]['name'].endswith('_mcp_echo') and calls[8]['name'] == 'profile_finish'
        assert [c['status'] for c in calls] == ['completed'] * 3 + ['failed'] * 3 + ['completed'] * 3
        assert result['native_summary_length'] < len(result['result'])
        assert 'FORECAST_' in result['result']
        for file in result['files']:
            request = urllib.request.Request(BASE + file['url'], headers={'Authorization': 'Bearer ' + token})
            with urllib.request.urlopen(request) as response:
                assert b',42' in response.read()
        child_id = result['chat_url'].rsplit('/', 1)[1]
        points = {r['event']: r['time'] for r in trace if r['chat_id'] == child_id}
        intervals.append(points)
    overlap = min(p['end'] for p in intervals) - max(p['start'] for p in intervals)
    assert overlap > 0
    cleanup = json.loads((ROOT / ('cleanup-' + last['message_id'] + '.json')).read_text())
    assert not cleanup['remaining_tickets'] and not any(cleanup['child_tasks'].values())
    assert cleanup['mcp_closed'] == [True, True]
    evidence = {'passed': True, **last, 'overlap_seconds': overlap, 'children': [r['chat_url'] for r in report['results']],
        'own_tool_chains': True, 'three_foreign_readers_denied': True, 'full_saved_output': True,
        'files_downloaded_by_owner': True, 'mcp_closed': True}
    (ROOT / 'gate-evidence.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence))


def children(parent):
    with sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True) as db:
        rows = db.execute("SELECT id FROM chat WHERE json_extract(meta, '$.internal')=1 "
                          "AND json_extract(meta, '$.parent_chat_id')=?", (parent,)).fetchall()
    return [row[0] for row in rows]


def stop_case(dispatch=False):
    token = json.loads(STATE.read_text())['user']['token']
    current = start('parallel gate ' + ('dispatch slow' if dispatch else 'slow'))
    for _ in range(150):
        ids = children(current['chat_id'])
        lookup_started = False
        path = ROOT / 'tool-trace.jsonl'
        if path.exists():
            starts = [json.loads(line) for line in path.read_text().splitlines()]
            lookup_started = sum(r['event'] == 'start' and r['chat_id'] in ids for r in starts) == 2
        if len(ids) == 2 and (dispatch or lookup_started):
            break
        time.sleep(0.05)
    else:
        raise AssertionError('Cancellation point not reached')
    for child_id in ids:
        chat = api('/api/v1/chats/' + child_id, token=token)
        message = chat['chat']['history']['messages'][chat['chat']['history']['currentId']]
        assert message.get('done') is False, 'Must cancel live work, not an already completed task'
        assert not (message.get('done') and not message.get('error')), 'Placeholder is not success'
    api('/api/tasks/chat/' + current['chat_id'] + '/stop', {}, token=token)
    cleanup_path = ROOT / ('cleanup-' + current['message_id'] + '.json')
    for _ in range(100):
        if cleanup_path.exists():
            break
        time.sleep(0.05)
    assert cleanup_path.exists()
    cleanup = json.loads(cleanup_path.read_text())
    assert not cleanup['remaining_tickets'] and not any(cleanup['child_tasks'].values())
    assert all(cleanup['mcp_closed'])
    if not dispatch:
        assert cleanup['mcp_closed'] == [True, True]
    parent = api('/api/v1/chats/' + current['chat_id'], token=token)
    message = parent['chat']['history']['messages'][current['message_id']]
    assert len(message.get('sources', [])) == 2, 'Links must survive cancellation before Tool result'
    print(json.dumps({'passed': True, 'case': 'dispatch' if dispatch else 'tool-continuation',
                      **current, 'children': ids, 'cleanup': cleanup}))


def approvals():
    config = api('/api/v1/configs/export')
    before = config.get('chat.tool_permissions.enable', False)
    api('/api/v1/configs/import', {'config': {'chat.tool_permissions.enable': True}})
    try:
        paused = start('parallel gate pause')
        finish(paused)
        path = ROOT / ('results-' + paused['message_id'] + '.json')
        report = json.loads(path.read_text())
        assert all(r['done'] is False and r['status'] == 'failed' and r['error'] for r in report['results'])
        owner = json.loads(STATE.read_text())['user']['token']
        for child_id in children(paused['chat_id']):
            api('/api/tasks/chat/' + child_id + '/stop', {}, token=owner)
        rejected = start('parallel gate approval rejected', 'ask')
        finish(rejected)
        assert children(rejected['chat_id']) == [], 'Unsupported mode must reject before dispatch'
        parent = api('/api/v1/chats/' + rejected['chat_id'], token=owner)
        message = parent['chat']['history']['messages'][rejected['message_id']]
        assert 'does not support paused tool approvals' in str(message.get('error'))
        print(json.dumps({'passed': True, 'paused_children_not_success': paused,
                          'unsupported_mode_rejected_before_dispatch': rejected}))
    finally:
        api('/api/v1/configs/import', {'config': {'chat.tool_permissions.enable': before}})


def timeout_case(dispatch=False):
    current = start('parallel gate ' + ('dispatch slow timeout' if dispatch else 'slow timeout'))
    finish(current)
    report = json.loads((ROOT / ('results-' + current['message_id'] + '.json')).read_text())
    cleanup = json.loads((ROOT / ('cleanup-' + current['message_id'] + '.json')).read_text())
    assert all(r['status'] == 'timed_out' for r in report['results'])
    assert cleanup['remaining_tickets'] == 0 and not any(cleanup['child_tasks'].values())
    assert all(cleanup['mcp_closed'])
    assert not cleanup['worker_errors']
    owner = json.loads(STATE.read_text())['user']['token']
    parent = api('/api/v1/chats/' + current['chat_id'], token=owner)
    assert len(parent['chat']['history']['messages'][current['message_id']].get('sources', [])) == 2
    evidence = {'passed': True, 'case': 'dispatch-timeout' if dispatch else 'tool-timeout',
                **current, 'cleanup': cleanup}
    (ROOT / (evidence['case'] + '-evidence.json')).write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['bootstrap', 'setup', 'install', 'run', 'verify', 'stop', 'dispatch-stop', 'approvals', 'timeout', 'dispatch-timeout'])
    parser.add_argument('--question', default='parallel gate')
    parser.add_argument('--approval', default='full')
    args = parser.parse_args()
    if args.action == 'bootstrap':
        asyncio.run(bootstrap())
        print('Private test session created; no credentials printed')
    elif args.action == 'setup':
        setup()
    elif args.action == 'install':
        install()
    elif args.action == 'run':
        run(args.question, args.approval)
    elif args.action == 'verify':
        verify()
    elif args.action in {'stop', 'dispatch-stop'}:
        stop_case(args.action == 'dispatch-stop')
    elif args.action in {'timeout', 'dispatch-timeout'}:
        timeout_case(args.action == 'dispatch-timeout')
    else:
        approvals()

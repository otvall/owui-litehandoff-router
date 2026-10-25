"""Offline fixtures and native/browser acceptance for Inline Visualizer.

Run mutations only inside the explicitly named disposable OWUI container.
Upstream assets are verified before any HTTP request or installation.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / 'fixtures' / 'inline_visualizer'
COMMITS = {
    '2.1.1': '5f549492316505af4ec6f2de0bdee781b1e778a5',
    '2.2.3': 'bcc224db138b6901a8d4b99e7f8a8859bea5629b',
}
STATE = ROOT / 'visualizer-private-state.json'


def transport():
    import parallel_gate as gate
    gate.ROOT = ROOT
    gate.TOKEN = ROOT / 'admin-token'
    gate.STATE = STATE
    return gate


def disposable():
    assert ROOT == Path('/tmp/lhr-visualizer-gate'), 'Use the disposable container directory'
    assert os.environ.get('VISUALIZER_DISPOSABLE') == 'owui-router-test'
    gate = transport()
    assert gate.api('/api/version')['version'] == '0.12.3'
    assert gate.api('/api/v1/auths/')['role'] == 'admin'
    return gate


def verify_assets(directory=ASSETS):
    manifest = json.loads((directory / 'manifest.json').read_text())
    expected = {f'{v}/{name}' for v in COMMITS for name in ('tool.py', 'SKILL.md')}
    assert {f['path'] for f in manifest['files']} == expected
    assert len(manifest['files']) == len(expected)
    for item in manifest['files']:
        version = item['tool_version']
        assert item['commit'] == COMMITS[version]
        name = Path(item['path']).name
        assert item['path'] == f'{version}/{name}'
        assert item['url'] == (
            'https://raw.githubusercontent.com/Classic298/open-webui-plugins/'
            f'{COMMITS[version]}/inline-visualizer-v2/{name}')
        data = (directory / item['path']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item['sha256'], item['path'] + ': SHA-256 mismatch'
        if name == 'tool.py':
            assert re.search(r'^version: ' + re.escape(version) + r'$', data.decode(), re.M)
        else:
            assert 'name: visualize' in data.decode()
    return manifest


def upsert(collection, identifier, form, token=None):
    gate = transport()
    rows = gate.api('/api/v1/' + collection + ('/all' if collection == 'models' else '/'), token=token)
    exists = any(row['id'] == identifier for row in rows)
    if collection == 'models':
        path = '/api/v1/models/model/update' if exists else '/api/v1/models/create'
    else:
        path = f'/api/v1/{collection}/id/{identifier}/update' if exists else f'/api/v1/{collection}/create'
    return gate.api(path, form, token=token)


# Test boundary only. The original source above this suffix remains byte-identical.
# Observe the original callable once, forwarding the real native callbacks.
MEASURE = '''
import inspect as _inspect
import json as _json
import time as _time
from pathlib import Path as _Path
_original = Tools.FUNCTION
async def _measured(self, title="Visualization", __event_call__=None,
                    __event_emitter__=None, __metadata__=None, __request__=None, __user__=None):
    from open_webui.socket.main import get_user_id_from_session_pool
    meta = __metadata__ or {}
    record = {"chat_id": meta.get("chat_id"), "message_id": meta.get("message_id"),
              "version": "VERSION", "actor": __user__["id"],
              "connected": get_user_id_from_session_pool(meta.get("session_id")) == __user__["id"]}
    def log(event, **extra):
        with _Path('/tmp/lhr-visualizer-gate/tool-trace.jsonl').open('a') as out:
            out.write(_json.dumps({**record, "event": event, "time": _time.monotonic(),
                "connected_now": get_user_id_from_session_pool(meta.get("session_id")) == __user__["id"], **extra})+'\\n')
    async def call(event):
        log("browser_call", type=event["type"])
        result = await __event_call__(event)
        log("browser_return")
        return result
    async def emit(event):
        log("emit", type=event["type"])
        return await __event_emitter__(event)
    log("start")
    try:
        kwargs = {"title":title, "__event_call__":call if __event_call__ else None,
                  "__event_emitter__":emit if __event_emitter__ else None}
        result = await _original(self, **{k:v for k,v in kwargs.items() if k in _inspect.signature(_original).parameters})
        log("return")
        return result
    finally:
        log("end")
_measured.__name__ = _original.__name__
_measured.__doc__ = _original.__doc__
Tools.FUNCTION = _measured
'''

AUDIT = '''import json
from pathlib import Path
class Filter:
    async def inlet(self, body: dict, __request__, __metadata__: dict, __user__: dict):
        from open_webui.socket.main import get_user_id_from_session_pool, get_session_ids_by_user_id
        with Path('/tmp/lhr-visualizer-gate/cleanup-trace.jsonl').open('a') as out:
            out.write(json.dumps({"request_chat": __metadata__.get("chat_id"),
                "session_connected": get_user_id_from_session_pool(__metadata__.get("session_id")) is not None})+'\\n')
        if 'VG_INSPECT_ONLY' in str(body.get('messages', [])):
            record = {"inspection": True, "time": __import__('time').monotonic(),
                      "tickets": len(getattr(__request__.app.state, 'lite_handoff_parallel_runs', {})),
                      "viewer_sockets": len(get_session_ids_by_user_id(__user__['id']))}
            with Path('/tmp/lhr-visualizer-gate/cleanup-trace.jsonl').open('a') as out:
                out.write(json.dumps(record)+'\\n')
        return body
    async def outlet(self, body: dict, __request__, __metadata__: dict):
        record = {"chat_id": __metadata__.get("chat_id"),
                  "tickets": len(getattr(__request__.app.state, 'lite_handoff_parallel_runs', {}))}
        with Path('/tmp/lhr-visualizer-gate/cleanup-trace.jsonl').open('a') as out:
            out.write(json.dumps(record)+'\\n')
        return body
'''


def install(version, assets=ASSETS):
    manifest = verify_assets(assets)  # Must precede even a version/auth HTTP request.
    gate = disposable()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if 'snapshot' not in state:
        state['snapshot'] = {'openai': gate.api('/openai/config'), 'config': gate.api('/api/v1/configs/export')}
        state['users'] = {}
        gate.private_json(STATE, state)
    for key in ('owner', 'viewer', 'other'):
        if key not in state['users']:
            password = secrets.token_urlsafe(24)
            user = gate.api('/api/v1/auths/add', {'name': 'Visualizer fixture ' + key,
                'email': f'lhr-visualizer-{key}@example.com', 'password': password, 'role': 'user'})
            state['users'][key] = {**user, 'password': password}
            gate.private_json(STATE, state)
        else:
            user = state['users'][key]
            state['users'][key].update(gate.api('/api/v1/auths/signin',
                {'email': user['email'], 'password': user['password']}))
    grants = [{'principal_type': 'user', 'principal_id': state['users']['viewer']['id'], 'permission': 'read'}]
    for name in ('router', 'a', 'b', 's'):
        identifier = 'vg-' + name
        upsert('models', identifier, {'id': identifier, 'name': identifier, 'base_model_id': None,
            'params': {}, 'meta': {'hidden': True}, 'access_grants': grants})
    mapping = {f'vg-agent-{c}': f'vg-route-{c}' for c in 'abs'}
    for identifier, filename in (('vg_preparation', 'router_preparation.py'), ('vg_pipe', 'lite_handoff_router.py')):
        content = (ROOT / 'dist' / filename).read_text()
        if identifier == 'vg_preparation':
            content = content.replace('AGENTS: dict[str, str] = {}', 'AGENTS: dict[str, str] = ' + repr(mapping))
        row = upsert('functions', identifier, {'id': identifier, 'name': identifier, 'content': content, 'meta': {}})
        if not row['is_active']:
            gate.api(f'/api/v1/functions/id/{identifier}/toggle', {})
    row = upsert('functions', 'vg_audit', {'id': 'vg_audit', 'name': 'Visualizer fixture observation', 'content': AUDIT, 'meta': {}})
    if not row['is_active']:
        gate.api('/api/v1/functions/id/vg_audit/toggle', {})
    gate.api('/api/v1/functions/id/vg_pipe/valves/update', {'orchestrator_model_id': 'vg-router'})
    gate.api('/api/v1/functions/id/vg_preparation/valves/update',
             {'base_tool_ids': [], 'base_skill_ids': [], 'parallel_timeout_seconds': 60})
    function = 'visualize' if version == '2.2.3' else 'render_visualization'
    for letter in 'ab':
        if version == '2.1.1' and any(t['id'] == 'vg_visualizer_' + letter for t in gate.api('/api/v1/tools/')):
            gate.api('/api/v1/tools/id/vg_visualizer_' + letter + '/valves/update', {'security_level': 'strict', 'chime': False})
        content = (assets / version / 'tool.py').read_text() + MEASURE.replace('FUNCTION', function).replace('VERSION', version)
        upsert('tools', 'vg_visualizer_' + letter, {'id': 'vg_visualizer_' + letter,
            'name': 'Visualizer fixture ' + letter, 'content': content, 'meta': {}, 'access_grants': []})
        tool = gate.api('/api/v1/tools/id/vg_visualizer_' + letter)
        assert [s['name'] for s in tool['specs']] == [function], tool['specs']
        gate.api('/api/v1/tools/id/vg_visualizer_' + letter + '/valves/update',
                 {'security_level': 'offline' if version == '2.2.3' else 'strict', 'chime': False})
        upsert('skills', 'vg-work-' + letter, {'id': 'vg-work-' + letter, 'name': 'VG visualize ' + letter,
            'description': 'Pinned Inline Visualizer handbook', 'content': (assets / version / 'SKILL.md').read_text(),
            'meta': {}, 'access_grants': []})
    for letter in 'abs':
        upsert('skills', 'vg-route-' + letter, {'id': 'vg-route-' + letter, 'name': 'Route ' + letter,
            'description': 'Visualizer fixture assignment', 'content': 'Use lite_parallel for independent visualizations.',
            'meta': {}, 'access_grants': []})
        upsert('models', 'vg-agent-' + letter, {'id': 'vg-agent-' + letter, 'name': 'VG agent ' + letter,
            'base_model_id': 'vg-' + letter, 'params': {'system': 'VG_AGENT_' + letter.upper(), 'function_calling': 'native'},
            'meta': {'hidden': True, 'toolIds': ['vg_visualizer_' + letter] if letter != 's' else [],
                     'skillIds': ['vg-work-' + letter] if letter != 's' else []}, 'access_grants': grants})
    upsert('models', 'vg-router-chat', {'id': 'vg-router-chat', 'name': 'Visualizer acceptance Router',
        'base_model_id': 'vg_pipe', 'params': {'system': 'VG_ROUTER', 'function_calling': 'native'},
        'meta': {'filterIds': ['vg_preparation', 'vg_audit'], 'toolIds': [], 'skillIds': []}, 'access_grants': grants})
    # A Pipe must have a catalog row for OWUI's native internal subagent dispatch.
    upsert('models', 'vg_pipe', {'id': 'vg_pipe', 'name': 'Visualizer fixture transport', 'base_model_id': None,
        'params': {}, 'meta': {'hidden': True}, 'access_grants': grants})
    gate.api('/openai/config/update', {'ENABLE_OPENAI_API': True, 'OPENAI_API_BASE_URLS': ['http://127.0.0.1:8769/v1'],
        'OPENAI_API_KEYS': ['local-test'], 'OPENAI_API_CONFIGS': {}})
    gate.api('/api/v1/configs/import', {'config': {'ollama.enable': False, 'chat.context_compaction.enable': False}})
    viewer = state['users']['viewer']['token']
    if 'viewer_settings' not in state:
        state['viewer_settings'] = gate.api('/api/v1/users/user/settings', token=viewer) or {}
        gate.private_json(STATE, state)
    settings = gate.api('/api/v1/users/user/settings', token=viewer) or {}
    gate.api('/api/v1/users/user/settings/update', {**settings, 'ui': {**settings.get('ui', {}),
        'iframeSandboxAllowSameOrigin': True, 'iframeSandboxAllowScripts': True,
        'title': {'auto': False}, 'followUpGeneration': False, 'tagsGeneration': False}}, token=viewer)
    asyncio.run(assign_fixture_owner(state['users']['owner']['id']))
    gate.api('/api/models')
    state['version'] = version
    state['restored'] = False
    state['manifest'] = manifest
    state['function'] = function
    state['exports'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'dist').glob('*.py')}
    gate.private_json(STATE, state)
    verify_installation()
    print(json.dumps({'installed': version, 'function': function, 'router': 'vg-router-chat', 'private_resources': True}))


async def assign_fixture_owner(owner):
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.models import Model
    from open_webui.models.tools import Tool
    from open_webui.models.skills import Skill
    async with get_async_db_context() as session:
        for table, ids in ((Model, ['vg-agent-' + c for c in 'abs']),
                           (Tool, ['vg_visualizer_' + c for c in 'ab']),
                           (Skill, ['vg-work-' + c for c in 'ab'])):
            for identifier in ids:
                row = await session.get(table, identifier)
                assert row is not None
                row.user_id = owner
        await session.commit()


def verify_installation():
    gate = disposable()
    state = json.loads(STATE.read_text())
    models = {m['id']: m for m in gate.api('/api/v1/models/all')}
    meta = models['vg-router-chat']['meta']
    assert {key: meta[key] for key in ('filterIds', 'toolIds', 'skillIds')} == {
        'filterIds': ['vg_preparation', 'vg_audit'], 'toolIds': [], 'skillIds': []}
    assert models['vg-router-chat']['base_model_id'] == 'vg_pipe'
    for c in 'abs':
        model = models['vg-agent-' + c]
        assert model['user_id'] == state['users']['owner']['id']
        assert model['meta']['toolIds'] == (['vg_visualizer_' + c] if c != 's' else [])
        assert model['meta']['skillIds'] == (['vg-work-' + c] if c != 's' else [])
    for collection, identifiers in [('tools', ['vg_visualizer_a', 'vg_visualizer_b']), ('skills', ['vg-work-a', 'vg-work-b'])]:
        for identifier in identifiers:
            row = gate.api(f'/api/v1/{collection}/id/{identifier}')
            assert row['user_id'] == state['users']['owner']['id'] and not row.get('access_grants')


def restore():
    gate = disposable()
    state = json.loads(STATE.read_text())
    before = state['snapshot']
    gate.api('/openai/config/update', before['openai'])
    gate.api('/api/v1/configs/import', {'config': before['config']})
    if 'viewer_settings' in state:
        original = state['viewer_settings']
        viewer = state['users']['viewer']['token']
        current = gate.api('/api/v1/users/user/settings', token=viewer) or {}
        gate.api('/api/v1/users/user/settings/update', {**original, 'ui': {
            **{k: None for k in current.get('ui', {}) if k not in original.get('ui', {})},
            **original.get('ui', {})}}, token=viewer)
        assert (gate.api('/api/v1/users/user/settings', token=viewer) or {}).get('ui', {}) == original.get('ui', {})
    assert gate.api('/openai/config') == before['openai']
    config = gate.api('/api/v1/configs/export')
    for key in ('ollama.enable', 'chat.context_compaction.enable'):
        assert config.get(key) == before['config'].get(key)
    state['restored'] = True
    gate.private_json(STATE, state)
    print('Original provider and shared configuration restored')


def latest(chat_id=None):
    gate = disposable()
    state = json.loads(STATE.read_text())
    token = state['users']['viewer']['token']
    if not chat_id:
        with sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True) as db:
            row = db.execute("SELECT id FROM chat WHERE user_id=? AND coalesce(json_extract(meta, '$.internal'),0)=0 ORDER BY updated_at DESC LIMIT 1",
                             (state['users']['viewer']['id'],)).fetchone()
        assert row, 'Submit the scenario in the real browser first'
        chat_id = row[0]
    chat = gate.api('/api/v1/chats/' + chat_id, token=token)
    aid = chat['chat']['history']['currentId']
    message = chat['chat']['history']['messages'][aid]
    return {'chat_id': chat_id, 'message_id': aid, 'message': message, 'children': gate.children(chat_id)}


def trace_rows(name):
    path = ROOT / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def verify(chat_id=None):
    gate = disposable()
    state = json.loads(STATE.read_text())
    token = state['users']['viewer']['token']
    report = latest(chat_id)
    message = report['message']
    assert message.get('done') and not message.get('error'), 'Parent not successfully complete'
    assert not message.get('embeds'), 'Worker embed leaked into main chat'
    call = next(o for o in message['output'] if o.get('name') == 'lite_parallel')
    output = next(o for o in message['output'] if o['type'] == 'function_call_output' and o['call_id'] == call['call_id'])
    group = json.loads(''.join(p.get('text', '') for p in output['output']))
    assert group['status'] == 'completed' and len(group['results']) == 2, group
    trace = trace_rows('tool-trace.jsonl')
    children = []
    for index, result in enumerate(group['results']):
        cid = result['chat_url'].rsplit('/', 1)[1]
        child = gate.api('/api/v1/chats/' + cid, token=token)
        meta = child['meta']
        correlation = meta['lite_parallel']
        assert meta['internal'] is True and meta['type'] == 'subagent'
        assert meta['parent_chat_id'] == report['chat_id'] and meta['parent_message_id'] == report['message_id']
        assert correlation['task_index'] == index and correlation['agent_id'] == result['agent_id']
        saved = child['chat']['history']['messages'][correlation['message_id']]
        assert saved['done'] and not saved.get('error') and saved['content'] == result['result']
        points = [p for p in trace if p['chat_id'] == cid]
        item = {'chat_id': cid, 'message_id': correlation['message_id'], 'agent_id': result['agent_id'],
                'embed_count': len(saved.get('embeds', [])), 'done': saved['done']}
        if result['agent_id'] != 'vg-agent-s':
            calls = [o for o in saved['output'] if o['type'] == 'function_call']
            assert [o['name'] for o in calls] == ['view_skill', state['function']]
            assert all(o['status'] == 'completed' for o in calls)
            assert len(saved.get('embeds', [])) == 1
            assert '@@@VIZ-START' in saved['content'] and '@@@VIZ-END' in saved['content']
            marker = re.search(r'VGFIX-[a-f0-9]+-[12]', saved['content']).group()
            assert 'id="vg-svg"' in saved['content'] and 'id="vg-toggle"' in saved['content']
            events = [p['event'] for p in points]
            assert events.count('start') == events.count('return') == events.count('end') == 1, events
            assert events.count('browser_call') == 1
            callbacks = [p for p in points if p['event'] == 'browser_return']
            assert not callbacks or (len(callbacks) == 1 and not callbacks[0]['connected_now']), events
            begin = next(p for p in points if p['event'] == 'start')
            end = next(p for p in points if p['event'] == 'return')
            duration = end['time'] - begin['time']
            assert duration <= 8, duration
            item.update(marker=marker, tool_seconds=duration, start=begin['time'], end=end['time'],
                        connected_at_entry=begin['connected'], connected_at_return=end['connected_now'])
        else:
            assert not saved.get('embeds') and not points
        assert gate.api('/api/tasks/chat/' + cid, token=token)['task_ids'] == []
        try:
            gate.api('/api/v1/chats/' + cid, token=state['users']['other']['token'])
        except RuntimeError as error:
            assert any(': ' + str(code) + ':' in str(error) for code in (401, 403, 404))
        else:
            raise AssertionError('Unrelated user read execution')
        children.append(item)
    visual = [c for c in children if c.get('marker')]
    assert len({c['marker'] for c in visual}) == len(visual)
    if len(visual) == 2:
        assert min(c['end'] for c in visual) - max(c['start'] for c in visual) > 0
        for child in visual:
            content = gate.api('/api/v1/chats/' + child['chat_id'], token=token)['chat']['history']['messages'][child['message_id']]['content']
            assert not any(c['marker'] in content for c in visual if c != child)
    listed = gate.api('/api/v1/chats/?include_folders=true', token=token)
    searched = gate.api('/api/v1/chats/search?text=Assigned%20task', token=token)
    assert not set(report['children']) & {c['id'] for c in listed + searched}
    assert gate.api('/api/tasks/chat/' + report['chat_id'], token=token)['task_ids'] == []
    cleanup = [p for p in trace_rows('cleanup-trace.jsonl') if p.get('chat_id') == report['chat_id']]
    assert cleanup and cleanup[-1]['tickets'] == 0
    evidence = {'backend_passed': True, 'browser_rendering': 'pending separate verification',
                'version': state['version'], 'chat_id': report['chat_id'], 'message_id': report['message_id'],
                'children': children, 'exports': state['exports']}
    gate.private_json(ROOT / ('evidence-' + report['chat_id'] + '.json'), evidence)
    print(json.dumps(evidence))


def negative(chat_id=None):
    gate = disposable()
    state = json.loads(STATE.read_text())
    assert state['version'] == '2.1.1'
    for _ in range(100):
        report = latest(chat_id)
        points = [p for p in trace_rows('tool-trace.jsonl') if p['chat_id'] in report['children']]
        if points:
            break
        time.sleep(.1)
    token = state['users']['viewer']['token']
    assert not report['message'].get('done') and len(report['children']) == 2
    assert [p['event'] for p in points] == ['start', 'browser_call'], points
    assert points[0]['connected'] and points[1]['type'] == 'execute'
    elapsed = time.monotonic() - points[0]['time']
    if elapsed < 6:
        time.sleep(6 - elapsed)
        elapsed = time.monotonic() - points[0]['time']
    points = [p for p in trace_rows('tool-trace.jsonl') if p['chat_id'] in report['children']]
    assert [p['event'] for p in points] == ['start', 'browser_call'], points
    assert 6 <= elapsed < 50, elapsed
    gate.api('/api/tasks/chat/' + report['chat_id'] + '/stop', {}, token=token)
    for _ in range(100):
        if not any(gate.api('/api/tasks/chat/' + cid, token=token)['task_ids'] for cid in [report['chat_id']] + report['children']):
            break
        time.sleep(.1)
    else:
        raise AssertionError('Native tasks remained after Stop')
    evidence = {'negative_reproduced': True, 'version': '2.1.1', 'chat_id': report['chat_id'],
                'children': report['children'], 'pending_seconds': elapsed, 'real_session': True, 'native_tasks_remaining': 0}
    gate.private_json(ROOT / 'negative-evidence.json', evidence)
    print(json.dumps(evidence))


def audit():
    """A fresh native request observes the process registry, without a browser."""
    gate = disposable()
    state = json.loads(STATE.read_text())
    viewer = state['users']['viewer']
    viewer.update(gate.api('/api/v1/auths/signin', {'email': viewer['email'], 'password': viewer['password']}))
    gate.private_json(STATE, state)
    uid, aid = str(uuid4()), str(uuid4())
    token = state['users']['viewer']['token']
    payload = {'model': 'vg-router-chat', 'stream': True,
        'session_id': None, 'id': aid, 'tools': [], 'params': {'tool_approval_mode': 'full'},
        'messages': [{'role': 'user', 'content': 'VG_INSPECT_ONLY'}],
        'user_message': {'id': uid, 'parentId': None, 'childrenIds': [aid], 'role': 'user',
            'content': 'VG_INSPECT_ONLY', 'models': ['vg-router-chat'], 'timestamp': int(time.time())}}
    started = time.monotonic()
    # Without a socket OWUI returns the Pipe's native SSE directly. Consume it;
    # do not invent a session ID to obtain the saved-task JSON envelope.
    request = urllib.request.Request(gate.BASE + '/api/chat/completions', data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        assert b'Inspection complete' in response.read()
    record = [r for r in trace_rows('cleanup-trace.jsonl') if r.get('inspection')][-1]
    assert record['time'] >= started and record['tickets'] == 0
    gate.private_json(ROOT / 'audit-evidence.json', record)
    print(json.dumps(record))


def export_evidence():
    gate = disposable()
    state = json.loads(STATE.read_text())
    evidence = {'manifest': state['manifest'], 'exports': state['exports'],
        'runs': [json.loads(p.read_text()) for p in sorted(ROOT.glob('evidence-*.json'))],
        'negative': json.loads((ROOT / 'negative-evidence.json').read_text()),
        'tool_trace': trace_rows('tool-trace.jsonl'), 'cleanup_trace': trace_rows('cleanup-trace.jsonl')}
    evidence['controls'] = {name: json.loads((ROOT / (name + '-evidence.json')).read_text())
                            for name in ('direct', 'existing-control', 'audit')
                            if (ROOT / (name + '-evidence.json')).exists()}
    evidence['controls_not_recorded'] = sorted({'direct', 'existing-control', 'audit'} - evidence['controls'].keys())
    with urllib.request.urlopen('http://127.0.0.1:8769/stats', timeout=5) as response:
        evidence['provider_counters'] = json.load(response)
    gate.private_json(ROOT / 'visualizer-evidence.json', evidence)
    print('Sanitized evidence saved; no credentials or session IDs included')


def cleanup():
    gate = disposable()
    state = json.loads(STATE.read_text())
    assert state.get('restored'), 'Restore shared settings before deleting fixtures'
    assert (ROOT / 'visualizer-evidence.json').is_file(), 'Export evidence before cleanup'
    record = json.loads((ROOT / 'audit-evidence.json').read_text())
    assert record['tickets'] == record['viewer_sockets'] == 0, 'Close fixture browser sessions and run audit first'
    viewer = state['users']['viewer']
    with sqlite3.connect('file:/app/backend/data/webui.db?mode=ro', uri=True) as db:
        chats = [row[0] for row in db.execute('SELECT id FROM chat WHERE user_id=?', (viewer['id'],))]
    for cid in chats:
        assert gate.api('/api/tasks/chat/' + cid, token=viewer['token'])['task_ids'] == []
    # Deleting the brand-new fixture users natively cascades only their test chats.
    for identifier in ['vg-router-chat', 'vg_pipe'] + ['vg-agent-' + c for c in 'abs'] + ['vg-' + c for c in ('router', 'a', 'b', 's')]:
        gate.api('/api/v1/models/model/delete', {'id': identifier})
    for collection, ids in [('tools', ['vg_visualizer_' + c for c in 'ab']),
                            ('skills', ['vg-work-' + c for c in 'ab'] + ['vg-route-' + c for c in 'abs']),
                            ('functions', ['vg_preparation', 'vg_pipe', 'vg_audit'])]:
        for identifier in ids:
            gate.api(f'/api/v1/{collection}/id/{identifier}/delete', method='DELETE')
    for user in state['users'].values():
        gate.api('/api/v1/users/' + user['id'], method='DELETE')
    state['cleaned'] = True
    gate.private_json(STATE, state)
    report = {'cleaned': True, 'fixture_chats': len(chats), 'tickets': 0, 'viewer_sockets': 0,
              'shared_settings_restored': True, 'production_modified': False}
    gate.private_json(ROOT / 'cleanup-evidence.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['verify-assets', 'bootstrap', 'install', 'verify-installation', 'restore', 'latest', 'verify', 'negative', 'audit', 'export-evidence', 'cleanup'])
    parser.add_argument('--assets', type=Path, default=ASSETS)
    parser.add_argument('--version', choices=list(COMMITS), default='2.2.3')
    parser.add_argument('--chat')
    args = parser.parse_args()
    if args.action == 'verify-assets':
        verify_assets(args.assets)
        print('Verified four pinned upstream files; no network access')
    elif args.action == 'bootstrap':
        assert ROOT == Path('/tmp/lhr-visualizer-gate')
        assert os.environ.get('VISUALIZER_DISPOSABLE') == 'owui-router-test'
        asyncio.run(transport().bootstrap())
    elif args.action == 'install':
        install(args.version, args.assets)
    elif args.action in ('latest', 'verify', 'negative'):
        if args.action == 'latest':
            report = latest(args.chat)
            print(json.dumps({**{k: v for k, v in report.items() if k != 'message'},
                'done': report['message'].get('done'), 'error': report['message'].get('error')}))
        else:
            {'verify': verify, 'negative': negative}[args.action](args.chat)
    else:
        {'verify-installation': verify_installation, 'restore': restore, 'audit': audit,
         'export-evidence': export_evidence, 'cleanup': cleanup}[args.action]()

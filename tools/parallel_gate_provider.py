"""Deterministic loopback provider used only by the parallel execution gate."""

import json
from pathlib import Path
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parent
app = FastAPI()
RUNS = {}


@app.get('/v1/models')
async def models():
    return {'object': 'list', 'data': [
        {'id': 'pgate-' + name, 'object': 'model', 'owned_by': 'local'}
        for name in ('a', 'b', 'c', 'router')
    ]}


@app.post('/v1/chat/completions')
async def completion(request: Request):
    body = await request.json()
    model = body['model']
    if model == 'pgate-router':
        return router_reply(body)
    if model == 'pgate-c':
        return presentation_reply(body)
    assert model in {'pgate-a', 'pgate-b'}, 'Router inference is forbidden inside workers'
    letter = model[-1]
    other = 'b' if letter == 'a' else 'a'
    messages = body.get('messages', [])
    systems = '\n'.join(str(m.get('content', '')) for m in messages if m.get('role') == 'system')
    assert 'PGATE_' + letter.upper() + '_PROMPT' in systems
    assert 'PGATE_' + other.upper() + '_PROMPT' not in systems
    assert 'PGATE_ROUTER_PROMPT' not in systems
    assert 'OTHER_PRIVATE_SKILL' not in str(messages)
    assert body['temperature'] == (0.2 if letter == 'a' else 0.4)
    names = {t['function']['name'] for t in body.get('tools', [])}
    assert not {'lite_delegate', 'lite_parallel', 'delegate_task'} & names
    own = 'work-' + letter + '-pgate'
    assert own in systems and 'work-' + other + '-pgate' not in systems
    # Denied foreign Skill reads are correctly removed from the agent projection.
    # Advance the scripted provider independently of that filtered history.
    question = str(next(m['content'] for m in reversed(messages) if m['role'] == 'user'))
    assignment = next((m['content'] for m in messages if m['role'] == 'user'
                       and str(m.get('content', '')).startswith('Assigned task ')), question)
    key = (model, str(assignment))
    phase = RUNS.get(key, 0)
    RUNS[key] = phase + 1
    steps = [
        ('view_skill', {'id': own}),
        ('read_skill_file', {'id': own, 'path': 'SKILL.md'}),
        ('grep_skill_files', {'id': own, 'pattern': 'OWN_WORKING'}),
        ('view_skill', {'id': 'other-pgate'}),
        ('read_skill_file', {'id': 'other-pgate', 'path': 'SKILL.md'}),
        ('grep_skill_files', {'id': 'other-pgate', 'pattern': 'OTHER_PRIVATE'}),
        ('profile_lookup', {'query': question}),
        ('pgate_' + letter + '_mcp_echo', {'value': 'MCP_' + letter.upper()}),
        ('profile_finish', {}),
    ]
    with (ROOT / 'provider-trace.jsonl').open('a') as trace:
        trace.write(json.dumps({'time': time.monotonic(), 'model': model, 'phase': phase,
                                'temperature': body['temperature'], 'tools': sorted(names)}) + '\n')
    if phase < len(steps):
        name, args = steps[phase]
        assert name in names
        message = {'role': 'assistant', 'content': None, 'tool_calls': [{
            'index': 0, 'id': 'gate_' + uuid4().hex, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)},
        }]}
        finish = 'tool_calls'
    else:
        message = {'role': 'assistant', 'content': 'FORECAST_' + letter.upper() + ': ' + ('complete data ' * 300)}
        finish = 'stop'
    return stream_reply(model, message, finish)


def tool_reply(model, name, arguments):
    return stream_reply(model, {'role': 'assistant', 'content': None, 'tool_calls': [{
        'index': 0, 'id': 'gate_' + uuid4().hex, 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps(arguments)}}]}, 'tool_calls')


def router_reply(body):
    messages = body['messages']
    assert 'PGATE_ROUTER_PROMPT' in str([m for m in messages if m['role'] == 'system'])
    assert not any(str(m.get('content', '')).startswith('Assigned task ') for m in messages)
    names = {t['function']['name']: t['function'] for t in body.get('tools', [])}
    question = str(next(m['content'] for m in reversed(messages) if m['role'] == 'user'))
    if 'vanilla delay' in question:
        return stream_reply(body['model'], {'role':'assistant','content':'Vanilla done'}, 'stop', delay=60)
    if 'general question' in question:
        return stream_reply(body['model'], {'role': 'assistant', 'content': 'General answer after parallel work'}, 'stop')
    calls = {c['id']: c['function']['name'] for m in messages for c in m.get('tool_calls', [])}
    results = {calls.get(m.get('tool_call_id')): m['content'] for m in messages if m['role'] == 'tool'}
    if 'handoff a' in question or 'handoff b' in question:
        letter='b' if 'handoff b' in question else 'a'
        if 'view_skill' not in results:
            return tool_reply(body['model'],'view_skill',{'id':'route-'+letter+'-pgate'})
        return tool_reply(body['model'],'lite_delegate',{'agent_id':'agent-'+letter+'-pgate'})
    if 'lite_parallel' in results:
        result = json.loads(results['lite_parallel'])
        if result.get('status') != 'completed' or 'own answer' in question:
            return stream_reply(body['model'], {'role': 'assistant', 'content': 'Group outcome: ' + str(result.get('status') or result)}, 'stop')
        return tool_reply(body['model'], 'lite_delegate', {'agent_id': 'agent-c-pgate'})
    if 'lite_parallel' not in names:
        return stream_reply(body['model'], {'role': 'assistant', 'content': 'Parallel capability unavailable'}, 'stop')
    if 'view_skill' not in results:
        return tool_reply(body['model'], 'view_skill', {'id': 'route-a-pgate'})
    permitted = names['lite_parallel']['parameters']['properties']['tasks']['items']['properties']['agent_id']['enum']
    second = 'agent-b-pgate' if 'agent-b-pgate' in permitted else 'agent-a-pgate'
    return tool_reply(body['model'], 'lite_parallel', {'tasks': [
        {'agent_id': 'agent-a-pgate', 'task': 'Forecast A. ' + question},
        {'agent_id': second, 'task': 'Forecast second sphere. ' + question}]})


def presentation_reply(body):
    messages = body['messages']
    if 'overflow C' in str(messages):
        from fastapi.responses import JSONResponse
        return JSONResponse({'error':{'message':'Presentation context length exceeded'}},status_code=400)
    systems = str([m for m in messages if m['role'] == 'system'])
    assert 'PGATE_C_PROMPT' in systems and 'PGATE_ROUTER_PROMPT' not in systems
    assert body['temperature'] == 0.1
    packet = next(m['content'] for m in messages if m['role'] == 'user'
                  and str(m.get('content', '')).startswith('Confirmed parallel task results.'))
    values = json.loads(packet.split('\n', 1)[1])
    assert len(values) == 2 and all(len(v['result']) > 3000 and v['files'] for v in values)
    assert not {'lite_delegate', 'lite_parallel', 'profile_lookup'} & {
        c['function']['name'] for m in messages for c in m.get('tool_calls', [])}
    assert 'OWN_WORKING_A' not in str(messages) and 'OWN_WORKING_B' not in str(messages)
    if not any(m['role'] == 'tool' for m in messages):
        return tool_reply(body['model'], 'make_presentation', {})
    return stream_reply(body['model'], {'role': 'assistant', 'content': 'Presentation ready from both complete forecasts.'}, 'stop')


def stream_reply(model, message, finish, delay=0):
    base = {'id': 'gate-' + uuid4().hex, 'object': 'chat.completion.chunk',
            'model': model, 'created': int(time.time())}

    async def stream():
        if delay:
            import asyncio
            await asyncio.sleep(delay)
        for delta, reason in ((message, None), ({}, finish)):
            yield 'data: ' + json.dumps({**base, 'choices': [
                {'index': 0, 'delta': delta, 'finish_reason': reason}
            ]}) + '\n\n'
        yield 'data: [DONE]\n\n'
    return StreamingResponse(stream(), media_type='text/event-stream')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8768)

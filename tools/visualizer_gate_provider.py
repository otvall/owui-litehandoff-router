"""Deterministic local provider; the Tool, Skill and native loop remain real."""

import asyncio
from collections import Counter
import json
from pathlib import Path
import re
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from parallel_gate_provider import stream_reply, tool_reply

ROOT = Path(__file__).resolve().parent
app = FastAPI()
COUNTS = Counter()


def trace(**record):
    with (ROOT / 'visualizer-provider-trace.jsonl').open('a') as out:
        out.write(json.dumps({'time': time.monotonic(), **record}) + '\n')


@app.get('/v1/models')
async def models():
    return {'object': 'list', 'data': [{'id': 'vg-' + n, 'object': 'model', 'owned_by': 'local'}
                                      for n in ('router', 'a', 'b', 's')]}


@app.get('/stats')
async def stats():
    return dict(COUNTS)


def results(messages):
    calls = {c['id']: c['function']['name'] for m in messages for c in m.get('tool_calls', [])}
    return {calls.get(m.get('tool_call_id')): m['content'] for m in messages if m['role'] == 'tool'}


def fragment(marker):
    return f'''<style>.vg-box{{font:18px system-ui;padding:20px;color:#123;background:#eef4ff}}button{{padding:10px}}</style>
<div class="vg-box" id="vg-root" data-marker="{marker}"><h2>{marker}</h2>
<svg id="vg-svg" viewBox="0 0 400 100" role="img" aria-label="Local SVG {marker}"><circle id="vg-dot" cx="50" cy="50" r="35" fill="#2563eb"/><text x="105" y="60">Local SVG 42</text></svg>
<button id="vg-toggle" type="button">Toggle {marker}</button><p id="vg-state" role="status">STATE=0</p></div>
<script>document.getElementById('vg-toggle').addEventListener('click',function(){{document.getElementById('vg-state').textContent='STATE=1';document.getElementById('vg-dot').setAttribute('fill','#16a34a');}});</script>'''


def rendered(model, marker, live):
    base = {'id': 'vg-' + uuid4().hex, 'object': 'chat.completion.chunk', 'model': model, 'created': int(time.time())}

    async def stream():
        chunks = [f'Local visualization {marker}.\n\n@@@VIZ-START\n', fragment(marker), '\n@@@VIZ-END\n\nVisualization complete.']
        for index, chunk in enumerate(chunks):
            if live and index == 1:
                (ROOT / ('generating-' + marker)).touch()
                for _ in range(1200):
                    if (ROOT / 'continue-generation').exists():
                        break
                    await asyncio.sleep(.1)
                else:
                    raise AssertionError('Live browser gate was not released within 120 seconds')
            yield 'data: ' + json.dumps({**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': chunk}, 'finish_reason': None}]}) + '\n\n'
            await asyncio.sleep(.2)
        yield 'data: ' + json.dumps({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}) + '\n\n'
        yield 'data: [DONE]\n\n'
        trace(model=model, marker=marker, phase='stream_finished')
    return StreamingResponse(stream(), media_type='text/event-stream')


@app.post('/v1/chat/completions')
async def completion(request: Request):
    body = await request.json()
    model = body['model']
    COUNTS[model] += 1
    messages = body['messages']
    names = {t['function']['name']: t['function'] for t in body.get('tools', [])}
    done = results(messages)
    systems = str([m for m in messages if m['role'] == 'system'])
    if model == 'vg-router':
        assert 'VG_ROUTER' in systems
        assert not {'visualize', 'render_visualization'} & names.keys()
        question = str(next(m['content'] for m in reversed(messages) if m['role'] == 'user'))
        if 'VG_INSPECT_ONLY' in question:
            return stream_reply(model, {'role': 'assistant', 'content': 'Inspection complete'}, 'stop')
        trace(model=model, phase=sorted(done), tools=sorted(names))
        if 'view_skill' not in done:
            return tool_reply(model, 'view_skill', {'id': 'vg-route-a'})
        if 'VG_DIRECT' in question:
            return tool_reply(model, 'lite_delegate', {'agent_id': 'vg-agent-a'})
        if 'lite_parallel' in done:
            outcome = json.loads(done['lite_parallel'])
            return stream_reply(model, {'role': 'assistant', 'content': 'Visualizer group: ' + outcome['status']}, 'stop')
        second = 'a' if 'VG_REPEAT' in question else 'b' if 'VG_PAIR' in question else 's'
        run = uuid4().hex[:12]
        tasks = [{'agent_id': 'vg-agent-' + c, 'task': f'Create an interactive visualization VGFIX-{run}-{i}. {question}'}
                 for i, c in enumerate(('a', second), 1)]
        return tool_reply(model, 'lite_parallel', {'tasks': tasks})
    assert model in {'vg-a', 'vg-b', 'vg-s'}
    letter = model[-1]
    assert 'VG_AGENT_' + letter.upper() in systems and 'VG_ROUTER' not in systems
    assert not {'lite_delegate', 'lite_parallel', 'delegate_task'} & names.keys()
    assignment = str(next((m['content'] for m in messages if m['role'] == 'user' and str(m['content']).startswith('Assigned task ')),
                          next(m['content'] for m in reversed(messages) if m['role'] == 'user')))
    match = re.search(r'VGFIX-[a-f0-9]+-[12]', assignment)
    marker = match.group() if match else 'VGFIX-direct'
    trace(model=model, marker=marker, phase=sorted(done), tools=sorted(names))
    if letter == 's':
        assert not {'visualize', 'render_visualization'} & names.keys()
        return stream_reply(model, {'role': 'assistant', 'content': 'Simple second worker ' + marker}, 'stop')
    assert 'vg-work-' + letter in systems and 'vg-work-' + ('b' if letter == 'a' else 'a') not in systems
    functions = names.keys() & {'visualize', 'render_visualization'}
    assert len(functions) == 1, names.keys()
    function = next(iter(functions))
    if 'view_skill' not in done:
        return tool_reply(model, 'view_skill', {'id': 'vg-work-' + letter})
    assert 'Inline Visualizer' in done['view_skill'] and '@@@VIZ-START' in done['view_skill']
    if function not in done:
        response = tool_reply(model, function, {'title': marker})
        if 'VG_DISCONNECT' in assignment:
            async def delayed():
                await asyncio.sleep(12)
                async for chunk in response.body_iterator:
                    yield chunk
            return StreamingResponse(delayed(), media_type='text/event-stream')
        return response
    assert 'mounted' in done[function]
    return rendered(model, marker, 'VG_LIVE' in assignment)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8769)

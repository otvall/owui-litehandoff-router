# Appended to an unmodified exported Pipe only in the disposable probe.
_GatePipeBase = Pipe


class Pipe(_GatePipeBase):
    async def pipe(self, body: dict, __request__, __user__: dict, __metadata__: dict,
                   __event_emitter__=None, __event_call__=None, __oauth_token__=None):
        import asyncio
        import time
        from pathlib import Path
        from open_webui.models.chats import Chats
        from open_webui.utils.subagents import delegate
        if (__request__.scope.get('lite_handoff_router') or {}).get('gate_worker'):
            return await super().pipe(body, __request__, __user__, __metadata__,
                __event_emitter__, __event_call__, __oauth_token__)
        state = require(__request__, __metadata__, __user__['id'])
        if __metadata__.get('params', {}).get('tool_approval_mode', 'full') != 'full':
            raise ValueError('Parallel gate does not support paused tool approvals; no workers dispatched')
        results = [m for m in body.get('messages', []) if m.get('role') == 'tool']
        if not hasattr(__request__.app.state, 'lhr_gate_runs'):
            __request__.app.state.lhr_gate_runs = {}
        event_lock = asyncio.Lock()

        async def bound(entry):
            async with event_lock:
                await emit_bound(entry)

        async def emit_bound(entry):
            chat_id, _ = entry['child']
            url = '/c/' + chat_id
            # Native persisted citation cards provide links even if Stop prevents a Tool result.
            await __event_emitter__({'type': 'source', 'data': {
                'source': {'name': entry['agent_id'] + ' — Открыть выполнение', 'url': url},
                'document': ['[Открыть выполнение](' + url + ')'],
                'metadata': [{'source': url, 'name': entry['agent_id']}],
            }})
            await __event_emitter__({'type': 'status', 'data': {
                'description': 'Выполняется ' + entry['agent_id'], 'done': False}})

        async def lite_parallel(tasks: list) -> str:
            entries = []
            handler = __request__.app.state.CHAT_COMPLETION_HANDLER
            catalog = deepcopy(__request__.app.state.MODELS)

            async def one(task):
                ticket = uuid4().hex
                entry = {'user_id': __user__['id'], 'agent_id': task['agent_id'],
                         'parent_chat_id': __metadata__['chat_id'],
                         'parent_message_id': __metadata__['message_id'], 'bound': bound, 'task': task['task']}
                entries.append(entry)
                __request__.app.state.lhr_gate_runs[ticket] = entry
                try:
                    result = await delegate(task['task'], '', False, request=__request__,
                        user_data=__user__, metadata={'model_id': state['router_id'],
                            'chat_context': {'lhr_gate_ticket': ticket,
                                'params': {'tool_approval_mode': 'ask' if 'pause' in task['task'] else 'full'}},
                            'session_id': __metadata__.get('session_id')},
                        parent_chat_id=__metadata__['chat_id'], parent_message_id=__metadata__['message_id'])
                    if not entry.get('child'):
                        return {'agent_id': task['agent_id'], 'error': result, 'unbound': True}
                    chat_id, message_id = entry['child']
                    saved = await Chats.get_message_by_id_and_message_id(chat_id, message_id)
                    record = {'agent_id': task['agent_id'], 'chat_url': '/c/' + chat_id,
                        'status': 'completed' if saved.get('done') and not saved.get('error') else 'failed',
                        'native_summary_length': len(result), 'done': saved.get('done'),
                        'error': saved.get('error') or (None if saved.get('done') else 'Incomplete or paused child execution'),
                        'result': saved.get('content'),
                        'files': saved.get('files'), 'output': saved.get('output')}
                    return record
                finally:
                    # Revoke authority before any fallible persistence/cleanup awaits.
                    __request__.app.state.lhr_gate_runs.pop(ticket, None)
                    async def cleanup_worker():
                        if not entry.get('child'):
                            for child_id in await Chats.get_internal_chat_ids_by_parent_id(state['chat_id'], __user__['id']):
                                child = await Chats.get_chat_by_id(child_id)
                                if child.meta.get('parent_message_id') != state['message_id']:
                                    continue
                                history = child.chat.get('history', {})
                                rows = list(history.get('messages', {}).values())
                                if any(row.get('role') == 'user' and row.get('content') == task['task'] for row in rows):
                                    entry['child'] = (child_id, history['currentId'])
                                    await bound(entry)
                                    break
                        for session in entry.get('scope', {}).get('lite_handoff_mcp', {}).values():
                            await session.close()
                    cleanup_task = asyncio.create_task(cleanup_worker())
                    # Native Stop may cancel both the child and its waiting parent.
                    # Finish persistence under that second cancellation as well.
                    while not cleanup_task.done():
                        try:
                            await asyncio.shield(cleanup_task)
                        except asyncio.CancelledError:
                            continue
                    cleanup_task.result()
            workers = [asyncio.create_task(one(task)) for task in tasks]
            try:
                collection = asyncio.gather(*workers)
                value = await asyncio.wait_for(collection, 2) if 'timeout' in tasks[0]['task'] else await collection
            except asyncio.TimeoutError:
                value = [{'agent_id': task['agent_id'], 'status': 'timed_out',
                          'error': 'Native probe group deadline exceeded'} for task in tasks]
            finally:
                for worker in workers:
                    if not worker.done() and not worker.cancelling():
                        worker.cancel()
                settled = await asyncio.gather(*workers, return_exceptions=True)
                await asyncio.sleep(0)
                from open_webui.tasks import item_tasks
                children = await Chats.get_internal_chat_ids_by_parent_id(state['chat_id'], __user__['id'])
                cleanup = {'parent_chat_id': state['chat_id'], 'children': children,
                    'remaining_tickets': len(__request__.app.state.lhr_gate_runs),
                    'child_tasks': {cid: item_tasks.get(cid, []) for cid in children},
                    'worker_errors': [repr(result) for result in settled
                        if isinstance(result, Exception)],
                    'mcp_closed': [s.task.done() for entry in entries
                        for s in entry.get('scope', {}).get('lite_handoff_mcp', {}).values()]}
                Path('/tmp/lhr-parallel-gate/cleanup-' + state['message_id'] + '.json').write_text(json.dumps(cleanup))
            assert __request__.app.state.CHAT_COMPLETION_HANDLER is handler
            assert __request__.app.state.MODELS == catalog
            Path('/tmp/lhr-parallel-gate/results-' + state['message_id'] + '.json').write_text(
                json.dumps({'parent_chat_id': state['chat_id'], 'results': value}))
            return json.dumps(value)

        live = __metadata__['tools']
        live.clear()
        spec = {'name': 'lite_parallel', 'description': 'Disposable native execution experiment',
                'parameters': {'type': 'object', 'properties': {'tasks': {'type': 'array',
                    'items': {'type': 'object'}}}, 'required': ['tasks']}}
        live['lite_parallel'] = {'spec': spec, 'callable': lite_parallel}
        question = str(next(m['content'] for m in body['messages'] if m['role'] == 'user'))
        tasks = [{'agent_id': 'agent-' + letter + '-pgate',
                  'task': '[Gate task ' + str(index) + '] ' + question + ' forecast ' + letter}
                 for index, letter in enumerate('ab', 1)]

        async def stream():
            if results:
                delta = {'role': 'assistant', 'content': 'Parallel gate finished. See execution links.'}
                finish = 'stop'
            else:
                delta = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'index': 0, 'id': 'pgate_' + uuid4().hex, 'type': 'function',
                    'function': {'name': 'lite_parallel', 'arguments': json.dumps({'tasks': tasks})}}]}
                finish = 'tool_calls'
            for part, reason in ((delta, None), ({}, finish)):
                yield 'data: ' + json.dumps({'id': 'pgate', 'object': 'chat.completion.chunk',
                    'model': state['router_id'], 'choices': [{'index': 0, 'delta': part,
                                                            'finish_reason': reason}]}) + '\n\n'
            yield 'data: [DONE]\n\n'
        from starlette.responses import StreamingResponse
        return StreamingResponse(stream(), media_type='text/event-stream')

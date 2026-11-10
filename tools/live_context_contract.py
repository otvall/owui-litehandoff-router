"""Assertions for opt-in real LLM evidence; no network or OWUI imports."""

def validate_live_report(report, *, mode, missing=False):
    assert not report.get('error'), report.get('error')
    group = report['group']
    if mode == 'same':
        assert group['status'] == 'completed', group['status']
        assert not group.get('rejected_followup'), 'An additional assignment was rejected'
        results = group['results']
        assert len(results) == 2, 'Expected two actual worker results'
        assert len({r['task_id'] for r in results}) == 2, 'Duplicate task IDs'
        assert len({r['chat_url'] for r in results}) == 2, 'Duplicate execution chats'
        for result in results:
            assert result['status'] == 'completed', result['status']
            assert all(value in result['task'] for value in ('FTP-42', '2026-11-01', '2026-11-30'))
            if missing:
                assert 'Недостаточно данных' in result['result'], result['result']
    calls_by_execution = {}
    for payload, output in zip(report['inputs'], report['outputs'], strict=True):
        execution = payload['execution_id']
        calls = [c for c in output['message'].get('tool_calls', [])
                 if c['function']['name'] == 'worker_lookup']
        calls_by_execution[execution] = calls_by_execution.get(execution, 0) + len(calls)
    for payload in report['inputs']:
        count = calls_by_execution[payload['execution_id']]
        if payload['child']:
            assert count == 1, f'Worker lookup count: {count}'
            assert 'Сделай 2 параллельных' not in str(payload['messages'])
        elif payload.get('synthesis'):
            assert count == 0, f'Synthesis repeated lookup: {count}'
        else:
            assert count <= 1, f'Lookup repeated in main execution: {count}'
    assert 'SYNTHETIC-REPORT-ORANGE' in report['final'], report['final']

import copy
import unittest
from tools.live_context_contract import validate_live_report


class LiveContextContractTest(unittest.TestCase):
    def report(self):
        return {'group': {'status': 'completed', 'results': [
            {'status': 'completed', 'task_id': str(i), 'chat_url': '/c/'+str(i),
             'task': 'FTP-42 2026-11-01 2026-11-30', 'result': '100'} for i in range(2)]},
            'inputs': [{'execution_id': str(i), 'child': True, 'messages': []} for i in range(2)] +
                      [{'execution_id': 'main', 'child': False, 'synthesis': True, 'messages': []}],
            'outputs': [{'message': {'tool_calls': [{'function': {'name': 'worker_lookup'}}]}} for _ in range(2)] +
                       [{'message': {'content': 'SYNTHETIC-REPORT-ORANGE'}}],
            'final': 'SYNTHETIC-REPORT-ORANGE'}

    def test_actual_two_results_and_one_lookup_per_worker(self):
        validate_live_report(self.report(), mode='same')

    def test_failed_or_fabricated_runs_are_not_accepted(self):
        original = self.report()
        bad = []
        unavailable = copy.deepcopy(original); unavailable['error'] = {'message': 'Provider unavailable'}; bad.append(unavailable)
        one = copy.deepcopy(original); one['group']['results'].pop(); bad.append(one)
        duplicate = copy.deepcopy(original); duplicate['group']['results'][1]['task_id'] = '0'; bad.append(duplicate)
        rejected = copy.deepcopy(original); rejected['group']['rejected_followup'] = True; bad.append(rejected)
        repeat = copy.deepcopy(original); repeat['outputs'][0]['message']['tool_calls'] *= 2; bad.append(repeat)
        synthesis = copy.deepcopy(original); synthesis['outputs'][2] = copy.deepcopy(original['outputs'][0]); bad.append(synthesis)
        for report in bad:
            with self.subTest(report=report), self.assertRaises(AssertionError):
                validate_live_report(report, mode='same')

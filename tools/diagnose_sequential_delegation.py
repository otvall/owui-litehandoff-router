"""Diagnostic replay of the 2026-11-10 real-LLM sequential delegation trace.

Default replays the captured symptom and requires handoff to be blocked; --batch checks successful delegation.
No LLM, network, credentials or production state required.
"""
import asyncio,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_parallel import ParallelExecutionTest,GroupAdapter,call,observe
from lite_handoff_router.parallel import parallel_tool,downstream_data
from lite_handoff_router.protocol import delegate_tool

async def main():
 fixture=ParallelExecutionTest()
 request,metadata,state,user=fixture.setup_request()
 adapter=GroupAdapter()
 tool=parallel_tool(adapter,request,user,metadata,state,{})['callable']
 item={'agent_id':'a','task':'Forecast FTP-42 for 2026-11-01 to 2026-11-30; observations 100,100,100.'}
 if '--batch' in sys.argv:
  observe(state,call('delegate_task','first',item),call('delegate_task','second',item))
  await asyncio.gather(tool(**item),tool(**item))
 else:
  observe(state,call('delegate_task','first',item))
  await tool(**item)
  observe(state,call('delegate_task','second',item))
  try:await tool(**item)
  except ValueError as error:print('second_call_error:',error)
 observe(state,call('lite_delegate','handoff',{'agent_id':'a'}))
 if '--batch' in sys.argv:
  await delegate_tool(adapter,request,user,metadata,state)['callable']('a')
 else:
  try:
   await delegate_tool(adapter,request,user,metadata,state)['callable']('a')
  except ValueError as error:
   assert 'rejected' in str(error)
  else:
   raise AssertionError('REPRO: handoff accepted after additional group rejection')
  assert not state['pending']
 packet=await downstream_data(adapter,user,metadata,state)
 print('actual_worker_starts:',len(adapter.started))
 print('group_status:',state['parallel']['status'])
 print('handoff_accepted:',bool(state['pending']))
 print('rejected_attempt_in_packet:','Only one parallel group' in str(packet))
 assert len(adapter.started)==(2 if '--batch' in sys.argv else 1)
 assert bool(state['pending'])==('--batch' in sys.argv)
 if '--batch' not in sys.argv: assert packet is None
asyncio.run(main())

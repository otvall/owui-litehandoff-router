"""Opt-in real OpenRouter evaluation on disposable OWUI native fixtures.

No key is stored here. Set LHR_OPENROUTER_KEY_FILE to a private transient file.
Only synthetic fixture inputs are submitted. Run inside the isolated 0.13.3 image.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools'), str(ROOT/'tests/native')]
from live_context_contract import validate_live_report
from native_environment import configure
configure(data_root='/tmp/lhr-real-llm-data')
import open_webui.config
from test_native_context import NativeContextTest

MODEL = os.environ.get('LHR_OPENROUTER_MODEL', 'nvidia/nemotron-3.5-lightning:free')
if not MODEL.endswith(':free'):
    raise ValueError('This acceptance runner permits free models only')
PARALLEL_PARAMETER = os.environ.get('LHR_OPENROUTER_PARALLEL', '0') == '1'

class LiveContext(NativeContextTest):
    async def provider(self, request, form_data, user):
        import httpx
        from fastapi.responses import StreamingResponse
        messages = copy.deepcopy(form_data['messages'])
        self.inputs.append({'execution_id':form_data['metadata']['chat_id'],
                            'synthesis': any(str(m.get('content','')).startswith('Confirmed parallel task results.') for m in messages),
                            'model': form_data['model'], 'child': form_data['metadata']['chat_id'] != self.parent_id,
                            'messages':messages,'tools':form_data.get('tools',[])})
        payload = {'model':MODEL, 'messages':messages, 'temperature':0, 'max_tokens':5000,
                   'provider':{'require_parameters':True}}
        if form_data.get('tools'):
            payload['tools'] = form_data['tools']
            # Some Tool-capable endpoints do not accept this optional parameter.
            # Actual multi-call behavior is always checked by validate_live_report.
            if PARALLEL_PARAMETER:
                payload['parallel_tool_calls'] = True
        key = Path(os.environ['LHR_OPENROUTER_KEY_FILE']).read_text().strip()
        async with httpx.AsyncClient(timeout=100) as client:
            response = await client.post('https://openrouter.ai/api/v1/chat/completions',json=payload,
                headers={'Authorization':'Bearer '+key})
            if response.status_code != 200:
                raise RuntimeError('OpenRouter status '+str(response.status_code)+': '+response.text[:500])
            data=response.json()
        message=data['choices'][0]['message']
        message = {k:v for k,v in message.items() if k in {'role','content','tool_calls'}}
        self.outputs.append({'model':form_data['model'],'message':message,'usage':data.get('usage')})
        print('LLM response', form_data['model'], 'calls', [c['function']['name'] for c in message.get('tool_calls',[])],flush=True)
        async def stream():
            calls=message.get('tool_calls',[])
            for i,c in enumerate(calls):c['index']=i
            yield 'data: '+json.dumps({'model':form_data['model'],'choices':[{'index':0,'delta':message,'finish_reason':None}]})+'\n\n'
            yield 'data: '+json.dumps({'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if calls else 'stop'}]})+'\n\n'
            yield 'data: [DONE]\n\n'
        return StreamingResponse(stream(),media_type='text/event-stream')

    async def scenario(self, mode, missing=False):
        from open_webui.models.models import Models, ModelForm
        from open_webui.utils import chat, middleware
        from open_webui.utils.misc import convert_output_to_messages
        await self.asyncSetUp()
        self.inputs=[]; self.outputs=[]
        router_prompt = ('You are Router. Read the routing skill first. For a request for two parallel calls, issue exactly two delegate_task calls '
            'in ONE generation to the requested agent. Each task must contain all relevant early parameters and data. '
            'Each worker performs ONE forecast; never ask workers to repeat the group. For a single request use lite_delegate. '
            'If the user asks for final synthesis, after successful workers use lite_delegate to the same expert for a final answer. '
            'If another group is rejected, report the actual completed results and missing work; do not hand off or invent another result. '
            'Do not invent missing observations. Always pass the user preference from memory to the final answer.')
        worker_prompt = ('You are a forecasting expert. If a Confirmed parallel task results packet is present, act only as a synthesizer: '
            'report the ACTUAL supplied results, count their unique task IDs, and never call worker_lookup again. '
            'Otherwise read your working skill, then call worker_lookup exactly ONCE for your own assignment. '
            'Return ONE forecast only for your assignment. Never repeat the work because the parent requested two calls. '
            'Use only supplied observations; for a constant series use its constant value. When observations are absent, explicitly say '
            '"Недостаточно данных: отсутствуют наблюдения ФТП" and do not invent a numeric forecast. Preserve exact ID and dates. '
            'For final synthesis do not compute extra forecasts or assume how many outcomes were supplied. If native user memory is present, '
            'include its report tag verbatim. Reply in Russian, concise.')
        for identifier,prompt in [(self.router,router_prompt),(self.agent,worker_prompt)]:
            row=await Models.get_model_by_id(identifier)
            values=row.params.model_dump() if hasattr(row.params,'model_dump') else dict(row.params)
            values['system']=prompt
            await Models.update_model_by_id(identifier,ModelForm(**{**row.model_dump(),'params':values}))
            self.app.state.MODELS[identifier]['info']['params']=values
        question=('Сделай 2 параллельных вызова в этот агент, покажи прогноз ФТП, затем передай агенту итоговую сводку.'
                  if mode=='same' else 'Передай агенту задачу: покажи прогноз ФТП с моей меткой отчёта из памяти.')
        body,metadata=await self.parent(question)
        self.parent_id=metadata['chat_id']
        early='ФТП FTP-42. Период прогноза 2026-11-01–2026-11-30. Формат: краткая таблица. '
        early+= 'Наблюдения не предоставлены.' if missing else 'Три последних наблюдения: 100, 100, 100. Используй прогноз постоянного уровня.'
        body['messages'][0]['content']=early
        # Preparation has captured its canonical history; prepare it again with the synthetic early turn.
        self.request.scope.pop('lite_handoff_router',None)
        await self.preparation.inlet(body,self.request,self.user.model_dump(),metadata)
        metadata['features']={'memory':True}
        memory='User report tag: SYNTHETIC-REPORT-ORANGE.'
        output=middleware.hidden_tool_result('recall_memories',memory)
        metadata['initial_output']=copy.deepcopy(output)
        body['messages']+=convert_output_to_messages(output,raw=True)
        with patch.object(chat,'generate_openai_chat_completion',self.provider):
            await asyncio.wait_for(self.run_loop(self.request,body,self.user,metadata),240)
        from open_webui.models.chats import Chats
        saved=await Chats.get_chat_by_id(self.parent_id)
        final=saved.chat['history']['messages'][metadata['message_id']]
        group=self.request.scope['lite_handoff_router']['parallel']
        report={'error':final.get('error'),'provider_model':MODEL,'parallel_parameter':PARALLEL_PARAMETER,'mode':mode,'missing':missing,'inputs':self.inputs,'outputs':self.outputs,
                'final':final.get('content'),'group':{'status':group['status'],'results':group['results'],'rejected_followup':group.get('rejected_followup',False)},
                'worker_tool_contexts':self.app.state.worker_contexts}
        Path(os.environ.get('LHR_REAL_REPORT_DIR','/tmp')).joinpath('real-'+mode+('-missing' if missing else '')+'.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        validate_live_report(report,mode=mode,missing=missing)
        print('PASS',mode,missing,flush=True)
        await self.doAsyncCleanups()
        self.doCleanups()

async def main():
    for mode,missing in [('same',False),('same',True),('handoff',False)]:
        case=LiveContext('test_current_memory_reaches_final_handoff')
        await case.scenario(mode,missing)

if __name__=='__main__':asyncio.run(main())

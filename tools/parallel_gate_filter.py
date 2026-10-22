# Appended to an unmodified exported Preparation only in the disposable probe.
_GatePreparationBase = Filter


class Filter(_GatePreparationBase):
    async def inlet(self, body: dict, __request__, __user__: dict, __metadata__: dict = None):
        if __request__.scope.get('lite_handoff_preparing'):
            return body
        if getattr(__request__.state, 'internal', False) is not True:
            return await super().inlet(body, __request__, __user__, __metadata__)
        from open_webui.models.chats import Chats
        context = getattr(__request__.state, 'chat_context', {}) or {}
        ticket = context.get('lhr_gate_ticket')
        entry = getattr(__request__.app.state, 'lhr_gate_runs', {}).get(ticket)
        if not entry or entry['user_id'] != __user__['id']:
            raise ValueError('Unknown gate execution ticket')
        if 'dispatch slow' in entry['task']:
            import asyncio
            await asyncio.sleep(30)
        metadata = __metadata__ if __metadata__ is not None else body['metadata']
        chat = await Chats.get_chat_by_id(metadata['chat_id'])
        if not chat or chat.user_id != entry['user_id'] or chat.meta.get('internal') is not True \
                or chat.meta.get('parent_chat_id') != entry['parent_chat_id'] \
                or chat.meta.get('parent_message_id') != entry['parent_message_id']:
            raise ValueError('Invalid native child identity')
        identity = (metadata['chat_id'], metadata['message_id'])
        if entry.get('child') not in (None, identity):
            raise ValueError('Ticket belongs to another child')
        entry['child'] = identity
        entry['scope'] = __request__.scope
        target = entry['agent_id']
        registry = {target: {'agent_id': target, 'agent_name': target,
                             'skill_id': 'route-' + target.removeprefix('agent-')}}
        state = begin(__request__, body, metadata, __user__['id'], registry, [], [], [])
        state.update(active_agent=target, phase='agent', gate_worker=True)
        await entry['bound'](entry)
        return body

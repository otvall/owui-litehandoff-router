"""Separate model-bound resource authority from the execution user."""

from .registry import ids
from .terminals import terminal_binding


async def routing_owner(adapter, request, user, router_id):
    return await adapter.owner(await adapter.model(request, router_id, user))


class ResourceScope:
    def __init__(self, adapter, request, user, model, owner, tool_ids, skill_ids, *, router=False, terminal_id=None,
                 chat_terminal=False, chat_skill_ids=()):
        self.adapter, self.request, self.user = adapter, request, user
        self.model_id, self.owner_id = model["id"], owner.id
        self.tool_ids, self.skill_ids = frozenset(tool_ids), frozenset(skill_ids)
        self.router = router
        self.terminal_id = terminal_id
        self.chat_terminal = chat_terminal and router
        self.chat_skill_ids = frozenset(chat_skill_ids) if router else frozenset()
        meta = model.get("info", {}).get("meta") or {}
        self.card_ids = {"skill": frozenset(ids(meta.get("skillIds") or [])),
                         "tool": frozenset(ids(meta.get("toolIds") or []))}

    async def authority(self, kind, identifier):
        allowed = (self.skill_ids if kind == "skill" else
                   {self.terminal_id} - {None} if kind == "terminal" else self.tool_ids)
        if identifier not in allowed:
            raise ValueError("Resource is outside the current model scope")
        model = await self.adapter.model(self.request, self.model_id, self.user)
        owner = await self.adapter.owner(model)
        if owner.id != self.owner_id:
            raise ValueError("Model resource owner changed during generation")
        if kind == "skill" and identifier in self.chat_skill_ids:
            return self.user
        if kind == "terminal" and self.chat_terminal:
            meta = model.get("info", {}).get("meta") or {}
            if (meta.get("capabilities") or {}).get("terminal") is False:
                raise ValueError("Terminal capability was disabled on the current model")
            return self.user
        if not self.router or kind == "terminal" or identifier in self.card_ids.get(kind, ()):
            meta = model.get("info", {}).get("meta") or {}
            configured = ({terminal_binding(model)} - {None} if kind == "terminal" else
                          ids(meta.get("skillIds" if kind == "skill" else "toolIds") or []))
            if identifier not in configured:
                raise ValueError("Resource was detached from the current model")
        return owner

    async def skill(self, identifier):
        owner = await self.authority("skill", identifier)
        await self.adapter.skill(identifier, owner)
        return owner

    async def tool(self, identifier):
        owner = await self.authority("tool", identifier)
        await self.adapter.tool_access(self.request, identifier, owner)

    async def terminal(self, identifier):
        owner = await self.authority("terminal", identifier)
        await self.adapter.terminal_access(self.request, identifier, owner)

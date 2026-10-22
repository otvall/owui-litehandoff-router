"""Delegation signals a pending selection, never starts another chat."""

from ..owui_adapter import OWUIAdapter
from ..protocol import delegate
from ..runtime import require


class Tools:
    async def lite_delegate(self, agent_id: str, __request__, __user__: dict, __metadata__: dict) -> str:
        """Hand off this user request to an exact advertised Workspace Model ID.

        :param agent_id: Workspace Model ID from the current routing manifest.
        """
        adapter = OWUIAdapter()
        user = await adapter.user(__user__["id"])
        state = require(__request__, __metadata__, user.id)
        return await delegate(adapter, __request__, user, state, agent_id)

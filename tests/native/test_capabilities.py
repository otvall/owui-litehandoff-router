import json
from uuid import uuid4
import unittest

from lite_handoff_router.capabilities import load_capabilities
from lite_handoff_router.owui_adapter import OWUIAdapter


class NativeCapabilitiesTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_uses_execution_actor_and_their_own_user_valves(self):
        from fastapi import FastAPI
        from starlette.requests import Request
        from open_webui.models.users import Users
        from open_webui.models.tools import Tools, ToolForm
        from open_webui.models.access_grants import AccessGrants
        from open_webui.utils.plugin import load_tool_module_by_id, set_tool_module_in_cache
        from open_webui.utils.tools import get_tool_specs

        suffix = uuid4().hex[:10]
        owner = await Users.insert_new_user("owner-" + suffix, "Owner", "owner-" + suffix + "@example.test", role="admin")
        actor = await Users.insert_new_user("actor-" + suffix, "Actor", "actor-" + suffix + "@example.test", role="user")
        request = Request({"type": "http", "app": FastAPI(), "headers": []})
        tool_id = "identity_" + suffix
        content = '''from pydantic import BaseModel
import json
class Tools:
    class UserValves(BaseModel):
        region: str = "default"
    async def who(self, __user__: dict) -> str:
        return json.dumps({"id": __user__["id"], "region": __user__["valves"].region})
'''
        module, _, source = await load_tool_module_by_id(tool_id, content)
        await Tools.insert_new_tool(owner.id, ToolForm(id=tool_id, name="Identity", content=content, meta={}), get_tool_specs(module), module=module)
        set_tool_module_in_cache(request, tool_id, content, module, source)
        await Tools.update_user_valves_by_id_and_user_id(tool_id, owner.id, {"region": "owner-only"})
        await Tools.update_user_valves_by_id_and_user_id(tool_id, actor.id, {"region": "actor-region"})
        await AccessGrants.grant_access("tool", tool_id, "user", actor.id, "read")
        adapter = OWUIAdapter()
        tools, _ = await load_capabilities(adapter, request, [tool_id], actor, {"__user__": actor.model_dump()})
        self.assertEqual(json.loads(await tools["who"]["callable"]()), {"id": actor.id, "region": "actor-region"})
        await AccessGrants.revoke_access("tool", tool_id, "user", actor.id, "read")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            await load_capabilities(adapter, request, [tool_id], actor, {"__user__": actor.model_dump()})
        from open_webui.models.config import Config

        previous = await Config.get("tool_server.connections", [])
        try:
            await Config.upsert({"tool_server.connections": [{
                "type": "mcp", "info": {"id": "isolated-mcp"}, "url": "http://127.0.0.1:8766/mcp",
                "config": {"enable": True, "access_grants": [{
                    "principal_type": "user", "principal_id": actor.id, "permission": "read",
                }]},
            }]})
            await adapter.mcp_access("isolated-mcp", actor)
            await Config.upsert({"tool_server.connections": []})
            with self.assertRaisesRegex(ValueError, "denied"):
                await adapter.mcp_access("isolated-mcp", actor)
            with self.assertRaisesRegex(ValueError, "denied"):
                await adapter.connect_mcp(request, "isolated-mcp", actor, {})
        finally:
            await Config.upsert({"tool_server.connections": previous})

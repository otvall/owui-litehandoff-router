import asyncio
from types import SimpleNamespace
import unittest

from lite_handoff_router.capabilities import load_capabilities


class McpAdapter:
    def __init__(self):
        self.actor, self.tasks, self.denied, self.closed = [], [], False, False

    async def tools(self, request, tool_ids, user, extra):
        return {}

    async def mcp_access(self, server_id, user):
        self.actor.append(user.id)
        if self.denied:
            raise ValueError("MCP access denied")

    async def connect_mcp(self, request, server_id, user, extra):
        self.actor.append(user.id)
        self.tasks.append(asyncio.current_task())
        return self, [{"name": "lookup", "parameters": {"type": "object", "properties": {}}}]

    async def call_tool(self, name, function_args):
        self.tasks.append(asyncio.current_task())
        return name, function_args

    async def disconnect(self):
        self.tasks.append(asyncio.current_task())
        self.closed = True


class CapabilitiesTest(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_identity_session_and_revocation(self):
        adapter, request, user = McpAdapter(), SimpleNamespace(scope={}), SimpleNamespace(id="execution-user")
        tools, _ = await load_capabilities(adapter, request, ["server:mcp:internal"], user, {"__user__": {"id": user.id}})
        result = await tools["internal_lookup"]["callable"](query="q", _name="ordinary-tool-argument")
        self.assertEqual(result, ("lookup", {"query": "q", "_name": "ordinary-tool-argument"}))
        # Repreparation preserves the same request session; permissions still rechecked.
        await load_capabilities(adapter, request, ["server:mcp:internal"], user, {"__user__": {"id": user.id}})
        self.assertEqual(len(request.scope["lite_handoff_mcp"]), 1)
        adapter.denied = True
        with self.assertRaisesRegex(ValueError, "denied"):
            await tools["internal_lookup"]["callable"](query="no")
        session = next(iter(request.scope["lite_handoff_mcp"].values()))
        await session.close()
        self.assertTrue(adapter.closed)
        self.assertEqual(set(adapter.actor), {user.id})
        self.assertEqual(len(set(adapter.tasks)), 1)

    async def test_missing_required_tool_fails_instead_of_partial_capabilities(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            await load_capabilities(McpAdapter(), SimpleNamespace(scope={}), ["private-tool"], SimpleNamespace(id="u"), {})

"""Cancellation during native client opening shares the request close deadline."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from lite_handoff_router.capabilities import load_capabilities
from lite_handoff_router.owui_adapter import OWUIAdapter


class NativeMcpCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_opening_failure_keeps_original_error_when_cleanup_times_out(self):
        from open_webui.utils import tools

        closed = asyncio.Event()

        class Client:
            def __init__(self, **kwargs):
                pass

            async def connect(self, **kwargs):
                raise ValueError("original connection failure")

            async def disconnect(self):
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        adapter = OWUIAdapter()
        with patch.object(adapter, "mcp_connection", return_value={"auth_type":"none"}), \
             patch.object(adapter, "user", return_value=SimpleNamespace(id="actor")), \
             patch.object(tools, "build_tool_server_headers", return_value=({}, {})), \
             patch.object(tools, "MCPClient", Client), \
             patch("lite_handoff_router.owui_adapter.MCP_CLOSE_TIMEOUT", .05), \
             self.assertLogs("lite_handoff_router.owui_adapter", "ERROR") as logs:
            with self.assertRaisesRegex(ValueError, "original connection failure"):
                await asyncio.wait_for(adapter.connect_mcp(None, "s", None, {"__user__":{"id":"actor"}}), .5)
        self.assertTrue(closed.is_set())
        self.assertIn("opening cleanup failed", logs.output[0])

    async def test_stop_during_connect_or_catalog_bounds_opening_cleanup(self):
        from open_webui.models.config import Config
        from open_webui.models.users import Users
        from open_webui.utils import tools

        uid = uuid4().hex
        owner = await Users.insert_new_user(uid, uid, uid + "@cleanup.test", role="user")
        previous = await Config.get("tool_server.connections", [])
        adapter = OWUIAdapter()
        connection = {"type": "mcp", "info": {"id": uid}, "auth_type": "none",
                      "config": {"enable": True, "access_grants": [{"principal_type": "user",
                      "principal_id": owner.id, "permission": "read"}]}}
        try:
            await Config.upsert({"tool_server.connections": [connection]})
            for stage in ("connect", "list_tool_specs"):
                with self.subTest(stage=stage):
                    opening, closed, release = (asyncio.Event() for _ in range(3))
                    request = SimpleNamespace(scope={})
                    clients = []

                    class Client:
                        def __init__(self, **kwargs):
                            self.disconnects = 0
                            clients.append(self)

                        async def connect(self, **kwargs):
                            if stage == "connect":
                                opening.set()
                                await asyncio.Event().wait()

                        async def list_tool_specs(self):
                            opening.set()
                            await asyncio.Event().wait()

                        async def disconnect(self):
                            self.disconnects += 1
                            try:
                                await release.wait()
                            finally:
                                closed.set()

                    async def run():
                        await load_capabilities(adapter, request, ["server:mcp:" + uid], owner,
                                                {"__user__": owner.model_dump()})

                    with patch.object(tools, "MCPClient", Client), \
                         patch("lite_handoff_router.capabilities.MCP_CLOSE_TIMEOUT", .05), \
                         self.assertLogs("lite_handoff_router.capabilities", "ERROR") as logs:
                        task = asyncio.create_task(run())
                        await asyncio.wait_for(opening.wait(), 1)
                        session = next(iter(request.scope["lite_handoff_mcp"].values()))
                        try:
                            task.cancel()
                            done, _ = await asyncio.wait([task, session.task], timeout=.25)
                            self.assertEqual(done, {task, session.task})
                            self.assertTrue(session.worker.done())
                            self.assertIn("close timed out", logs.output[0])
                            self.assertTrue(closed.is_set())
                            self.assertEqual(clients[0].disconnects, 1)
                            self.assertEqual(request.scope["lite_handoff_mcp"], {})
                            self.assertTrue(session.ready.cancelled())
                        finally:
                            release.set()
                            await asyncio.gather(task, session.close(), return_exceptions=True)
        finally:
            await Config.upsert({"tool_server.connections": previous})
            await Users.delete_user_by_id(owner.id)

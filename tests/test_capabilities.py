import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lite_handoff_router.capabilities import McpSession, close_mcp_sessions, load_capabilities


class McpAdapter:
    def __init__(self):
        self.actor, self.tasks, self.denied, self.closed = [], [], False, False

    async def tools(self, request, tool_ids, user, extra):
        return {}

    async def mcp_access(self, server_id, user):
        self.actor.append(user.id)
        if self.denied:
            raise ValueError("MCP access denied")

    async def mcp_specs(self, server_id, user, specs):
        await self.mcp_access(server_id, user)
        return specs

    async def connect_mcp(self, request, server_id, user, extra, *, register_client=None):
        self.actor.append(user.id)
        self.tasks.append(asyncio.current_task())
        if register_client is not None:
            register_client(self)
        return self, [{"name": "lookup", "parameters": {"type": "object", "properties": {}}}]

    async def call_tool(self, name, function_args):
        self.tasks.append(asyncio.current_task())
        return name, function_args

    async def disconnect(self):
        self.tasks.append(asyncio.current_task())
        self.closed = True


class CapabilitiesTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_policy_check_does_not_send_tool(self):
        checking, release, cleaning, finish = (asyncio.Event() for _ in range(4))
        adapter, sessions, sent = McpAdapter(), [], []

        async def check():
            checking.set()
            await release.wait()

        async def call(name, function_args):
            sent.append(name)

        adapter.call_tool = call

        async def request():
            session = McpSession(lambda register: adapter.connect_mcp(None, "s", SimpleNamespace(id="u"), {}))
            sessions.append(session)
            await session.ready
            try:
                await session.call("write", {}, check=check)
            finally:
                cleaning.set()
                await finish.wait()

        owner = asyncio.create_task(request())
        try:
            await asyncio.wait_for(checking.wait(), 1)
            owner.cancel()
            await cleaning.wait()
            release.set()
            # A marker queued behind the cancelled call proves the worker resumed.
            await sessions[0].call("marker", {})
            self.assertEqual(sent, ["marker"])
        finally:
            release.set()
            finish.set()
            await asyncio.gather(owner, return_exceptions=True)
            if sessions:
                await sessions[0].close()

    async def test_disconnect_does_not_wait_for_active_call_cancellation(self):
        started, cancelling, disconnected = (asyncio.Event() for _ in range(3))
        adapter = McpAdapter()

        async def call(name, function_args):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                # Model the SDK's courtesy cancel, which waits for transport I/O.
                await disconnected.wait()
                raise

        async def disconnect():
            disconnected.set()

        adapter.call_tool, adapter.disconnect = call, disconnect
        session = McpSession(lambda register: adapter.connect_mcp(None, "s", SimpleNamespace(id="u"), {}))
        await session.ready
        active = asyncio.create_task(session.call("write", {}))
        await started.wait()
        try:
            with patch("lite_handoff_router.capabilities.MCP_CLOSE_TIMEOUT", .05):
                closing = asyncio.create_task(session.close())
                await cancelling.wait()
                done, _ = await asyncio.wait([closing], timeout=.2)
                self.assertIn(closing, done)
                self.assertTrue(disconnected.is_set())
                self.assertTrue(session.task.done())
                self.assertTrue(active.done())
        finally:
            disconnected.set()
            await session.close()
            await asyncio.gather(active, return_exceptions=True)

    async def test_queued_call_rechecks_policy_before_transport(self):
        adapter=McpAdapter()
        request=SimpleNamespace(scope={})
        user=SimpleNamespace(id="u")
        started,release=asyncio.Event(),asyncio.Event()
        sent=[]

        async def call(name,function_args):
            sent.append(function_args["value"])
            started.set()
            await release.wait()
            return "done"

        adapter.call_tool=call
        tools,_=await load_capabilities(adapter,request,["server:mcp:s"],user,{"__user__":{"id":"u"}})
        first=asyncio.create_task(tools["s_lookup"]["callable"](value="first"))
        await started.wait()
        second=asyncio.create_task(tools["s_lookup"]["callable"](value="queued"))
        session=next(iter(request.scope["lite_handoff_mcp"].values()))
        while session.queue.empty(): await asyncio.sleep(0)
        adapter.denied=True
        release.set()
        self.assertEqual(await first,"done")
        with self.assertRaisesRegex(ValueError,"denied"):
            await second
        self.assertEqual(sent,["first"])
        await session.close()

    async def test_owner_exit_closes_idle_session_on_success_and_error(self):
        for failed in (False, True):
            adapter, sessions = McpAdapter(), []

            async def request():
                session = McpSession(lambda register: adapter.connect_mcp(None, "s", SimpleNamespace(id="u"), {}))
                sessions.append(session)
                await session.ready
                if failed:
                    raise ValueError("original failure")

            owner = asyncio.create_task(request())
            result = await asyncio.gather(owner, return_exceptions=True)
            await asyncio.wait_for(asyncio.shield(sessions[0].task), 1)
            self.assertTrue(adapter.closed)
            self.assertEqual(isinstance(result[0], ValueError), failed)

    async def test_stop_during_connect_and_before_first_session_turn(self):
        opening, interrupted = asyncio.Event(), asyncio.Event()
        sessions = []

        async def connect(register):
            opening.set()
            try:
                await asyncio.Event().wait()
            finally:
                interrupted.set()

        async def request():
            session = McpSession(connect)
            sessions.append(session)
            await session.ready

        owner = asyncio.create_task(request())
        await opening.wait()
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        await asyncio.wait_for(asyncio.shield(sessions[0].task), 1)
        self.assertTrue(interrupted.is_set())
        session = McpSession(connect)
        await session.close()
        self.assertTrue(session.ready.cancelled())
        self.assertTrue(session.task.done())

    async def test_concurrent_close_and_cancelled_waiter_keep_one_cleanup(self):
        entered, release = asyncio.Event(), asyncio.Event()
        disconnects = []
        adapter = McpAdapter()

        async def disconnect():
            disconnects.append(True)
            entered.set()
            await release.wait()

        adapter.disconnect = disconnect
        session = McpSession(lambda register: adapter.connect_mcp(None, "s", SimpleNamespace(id="u"), {}))
        await session.ready
        one, two = (asyncio.create_task(session.close()) for _ in range(2))
        await entered.wait()
        one.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await one
        self.assertFalse(session.task.done())
        release.set()
        await asyncio.wait_for(two, 1)
        await session.close()
        self.assertEqual(disconnects, [True])

    async def test_multiple_sessions_start_closing_before_waiting(self):
        release, all_entered = asyncio.Event(), asyncio.Event()
        entered, sessions = [], []

        async def connect(register):
            adapter = McpAdapter()

            async def disconnect():
                entered.append(True)
                if len(entered) == 3:
                    all_entered.set()
                await release.wait()

            adapter.disconnect = disconnect
            return adapter, []

        for _ in range(3):
            session = McpSession(connect)
            sessions.append(session)
            await session.ready
        closing = asyncio.create_task(close_mcp_sessions(sessions))
        try:
            await asyncio.wait_for(all_entered.wait(), 1)
        finally:
            release.set()
            await closing
        self.assertTrue(all(s.task.done() for s in sessions))

    async def test_disconnect_timeout_is_bounded_and_diagnosed(self):
        adapter = McpAdapter()

        async def disconnect():
            await asyncio.Event().wait()

        adapter.disconnect = disconnect
        session = McpSession(lambda register: adapter.connect_mcp(None, "s", SimpleNamespace(id="u"), {}))
        await session.ready
        with patch("lite_handoff_router.capabilities.MCP_CLOSE_TIMEOUT", .02), \
             self.assertLogs("lite_handoff_router.capabilities", "ERROR") as logs:
            await asyncio.wait_for(session.close(), 1)
        self.assertTrue(session.task.done())
        self.assertIn("close timed out", logs.output[0])

    async def test_owner_stop_closes_hanging_call_and_queued_waiters(self):
        started, disconnected = asyncio.Event(), asyncio.Event()
        sessions, calls = [], []

        class Client:
            async def call_tool(self, name, function_args):
                calls.append(name)
                started.set()
                await asyncio.Event().wait()

            async def disconnect(self):
                disconnected.set()

        async def connect(register):
            return Client(), []

        async def request():
            session = McpSession(connect)
            sessions.append(session)
            await session.ready
            await asyncio.Event().wait()

        owner = asyncio.create_task(request())
        while not sessions or not sessions[0].ready.done():
            await asyncio.sleep(0)
        session = sessions[0]
        active = asyncio.create_task(session.call("active", {}))
        await started.wait()
        queued = asyncio.create_task(session.call("queued", {}))
        await asyncio.sleep(0)
        try:
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
            await asyncio.wait_for(disconnected.wait(), 5)
            await asyncio.wait_for(asyncio.shield(session.task), 1)
            await asyncio.sleep(0)
            self.assertTrue(active.done())
            self.assertTrue(queued.done())
            self.assertEqual(calls, ["active"])
            with self.assertRaisesRegex(RuntimeError, "closed"):
                await session.call("late", {})
        finally:
            # Failure cleanup only; the assertions must observe automatic close.
            for task in (owner, session.task, active, queued):
                task.cancel()
            await asyncio.gather(owner, session.task, active, queued, return_exceptions=True)

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
        self.assertEqual(set(adapter.tasks[:-1]), {session.worker})
        self.assertNotEqual(adapter.tasks[-1], session.worker)

    async def test_missing_required_tool_fails_instead_of_partial_capabilities(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            await load_capabilities(McpAdapter(), SimpleNamespace(scope={}), ["private-tool"], SimpleNamespace(id="u"), {})

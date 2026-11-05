"""Native Tools and authenticated MCP sessions owned by one request."""

import asyncio
from copy import deepcopy
import hashlib
import logging

MCP_CLOSE_TIMEOUT = 5
mcp_log = logging.getLogger(__name__)


def bounded_tool_name(name):
    if len(name) <= 64:
        return name
    return name[:55] + "_" + hashlib.sha256(name.encode()).hexdigest()[:8]


class McpSession:
    """Serialize request-owned calls and interrupt them when the owner exits."""

    def __init__(self, connect):
        self.queue = asyncio.Queue()
        self.ready = asyncio.get_running_loop().create_future()
        self.waiters = set()
        self.stopping = False
        self.client = None
        self.deadline = None
        self.stopped = asyncio.get_running_loop().create_future()
        self.owner = asyncio.current_task()
        self.worker = asyncio.create_task(self.run(connect))
        self.task = asyncio.create_task(self.finish())
        self.owner.add_done_callback(self.stop)
        self.task.add_done_callback(self.finished)

    def stop(self, _owner=None):
        if not self.stopping:
            self.stopping = True
            self.deadline = asyncio.get_running_loop().time() + MCP_CLOSE_TIMEOUT
            self.cancel_waiters()
            self.stopped.set_result(None)

    def cancel_waiters(self):
        if not self.ready.done():
            self.ready.cancel()
        for future in tuple(self.waiters):
            if not future.done():
                future.cancel()

    def finished(self, task):
        # Also handles cancellation before run() gets its first event-loop turn.
        self.stopping = True
        self.cancel_waiters()
        self.owner.remove_done_callback(self.stop)
        while not self.queue.empty():
            self.queue.get_nowait()
        if not task.cancelled() and task.exception():
            mcp_log.error("MCP session failed", exc_info=task.exception())

    async def close(self):
        self.stop()
        try:
            await asyncio.shield(self.task)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise

    def attach_client(self, client):
        # Own the client before connect/list_tools can create or await resources.
        self.client = client

    async def finish(self):
        try:
            await asyncio.wait((self.worker, self.stopped), return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.stop()
            self.worker.cancel()
            try:
                async with asyncio.timeout_at(self.deadline):
                    # SDK cancellation can itself wait on transport I/O. Close the
                    # transport concurrently, under the same absolute deadline.
                    await asyncio.gather(self.worker, self.disconnect(), return_exceptions=True)
            except TimeoutError:
                mcp_log.exception("MCP session close timed out")

    async def disconnect(self):
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                mcp_log.exception("MCP session disconnect failed")

    async def run(self, connect):
        try:
            if self.stopping:
                return
            result = await connect(self.attach_client)
            if result is None:
                raise ValueError("Required MCP connection is unavailable or denied")
            client, specs = result
            self.attach_client(client)
            if self.stopping:
                return
            self.ready.set_result((specs, getattr(client, "instructions", "") or ""))
            while not self.stopping:
                name, arguments, future, check = await self.queue.get()
                if future.cancelled():
                    continue
                try:
                    if check is not None:
                        await check()
                    if future.cancelled() or self.stopping:
                        continue
                    result = await client.call_tool(name, function_args=arguments)
                except Exception as error:
                    if not future.done():
                        # Do not hand another task a traceback owning this live
                        # coroutine (consumers may clear its exception frames).
                        future.set_exception(error.with_traceback(None))
                else:
                    if not future.done():
                        future.set_result(result)
        except Exception as error:
            if not self.ready.done():
                self.ready.set_exception(error)
            else:
                mcp_log.exception("MCP session failed")

    async def call(self, name, arguments, check=None):
        if self.stopping or self.task.done():
            raise RuntimeError("MCP request session closed")
        future = asyncio.get_running_loop().create_future()
        self.waiters.add(future)
        self.queue.put_nowait((name, arguments, future, check))
        try:
            return await future
        finally:
            self.waiters.discard(future)


async def close_mcp_sessions(sessions):
    # Stop every connection before waiting, sharing one concurrent close budget.
    sessions = tuple(sessions)
    for session in sessions:
        session.stop()
    await asyncio.gather(*(session.close() for session in sessions))


def mcp_invoker(adapter, server_id, user, session, function_name):
    async def check():
        if not await adapter.mcp_specs(server_id, user, [{"name": function_name}]):
            raise ValueError("MCP function is no longer permitted: " + function_name)

    async def invoke(**kwargs):
        return await session.call(function_name, kwargs, check=check)
    return invoke


async def load_capabilities(adapter, request, tool_ids, user, extra):
    regular = [identifier for identifier in tool_ids if not identifier.startswith("server:mcp:")]
    tools = await adapter.tools(request, regular, user, extra)
    tools = {name: {**tool, "spec": deepcopy(tool["spec"])} for name, tool in tools.items()}
    missing = set(regular) - {tool.get("tool_id") for tool in tools.values()}
    if missing:
        raise ValueError("Required model Tools unavailable: " + ", ".join(sorted(missing)))
    instructions = []
    sessions = request.scope.setdefault("lite_handoff_mcp", {})
    created = []
    try:
        for identifier in tool_ids:
            if not identifier.startswith("server:mcp:"):
                continue
            server_id = identifier.removeprefix("server:mcp:")
            await adapter.mcp_access(server_id, user)
            key = (extra["__user__"]["id"], user.id, (extra.get("__model__") or {}).get("id"), server_id)
            session = sessions.get(key)
            if session is None or session.task.done():
                async def connect(register_client, server_id=server_id):
                    return await adapter.connect_mcp(request, server_id, user, extra,
                                                     register_client=register_client)
                session = McpSession(connect)
                sessions[key] = session
                created.append((key, session))
            specs, instruction = await session.ready
            specs = await adapter.mcp_specs(server_id, user, specs)
            if not specs:
                raise ValueError("Required MCP server provides no permitted Tools: " + server_id)
            if instruction:
                instructions.append("MCP server " + server_id + " instructions:\n" + instruction)
            for spec in specs:
                name = bounded_tool_name(server_id + "_" + spec["name"])
                if name in tools:
                    raise ValueError("Conflicting Tool name: " + name)

                tools[name] = {"spec": {**deepcopy(spec), "name": name},
                               "callable": mcp_invoker(adapter, server_id, user, session, spec["name"]),
                               "type": "mcp", "tool_id": identifier, "direct": False}
        return tools, instructions
    except BaseException:
        for key, session in created:
            sessions.pop(key, None)
        await close_mcp_sessions(session for _, session in created)
        raise

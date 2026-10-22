"""Native Tools and authenticated MCP sessions owned by one request."""

import asyncio
from copy import deepcopy
import hashlib


class McpSession:
    """Keep SDK enter/exit and calls in one task, including after continuation.

    MCP uses task-bound cancel scopes. A queue prevents a native Tool worker
    from closing a session opened by a different task. The request owner stops
    the session on completion/cancellation; no process-wide session cache exists.
    """

    def __init__(self, connect):
        self.queue = asyncio.Queue()
        self.ready = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self.run(connect))
        asyncio.current_task().add_done_callback(lambda _: self.stop())

    def stop(self):
        if not self.task.done():
            self.queue.put_nowait(None)

    async def close(self):
        self.stop()
        # A request can stop while the MCP owner is awaiting a remote Tool.
        # Cancel in that same owner task if graceful shutdown does not finish.
        await asyncio.wait_for(self.task, 5)

    async def run(self, connect):
        client = None
        try:
            result = await connect()
            if result is None:
                raise ValueError("Required MCP connection is unavailable or denied")
            client, specs = result
            self.ready.set_result((specs, getattr(client, "instructions", "") or ""))
            while True:
                command = await self.queue.get()
                if command is None:
                    break
                name, arguments, future = command
                if future.cancelled():
                    continue
                try:
                    result = await client.call_tool(name, function_args=arguments)
                except Exception as error:
                    if not future.done():
                        future.set_exception(error)
                else:
                    if not future.done():
                        future.set_result(result)
        except BaseException as error:
            if not self.ready.done():
                self.ready.set_exception(error)
        finally:
            try:
                if client is not None:
                    await client.disconnect()
            finally:
                while not self.queue.empty():
                    command = self.queue.get_nowait()
                    if command and not command[2].done():
                        command[2].set_exception(RuntimeError("MCP request session closed"))

    async def call(self, name, arguments):
        if self.task.done():
            raise RuntimeError("MCP request session closed")
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((name, arguments, future))
        return await future


def mcp_invoker(adapter, server_id, user, session, function_name):
    async def invoke(**kwargs):
        await adapter.mcp_access(server_id, user)
        return await session.call(function_name, kwargs)
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
                async def connect(server_id=server_id):
                    return await adapter.connect_mcp(request, server_id, user, extra)
                session = McpSession(connect)
                sessions[key] = session
                created.append((key, session))
            specs, instruction = await session.ready
            if not specs:
                raise ValueError("Required MCP server provides no permitted Tools: " + server_id)
            if instruction:
                instructions.append("MCP server " + server_id + " instructions:\n" + instruction)
            for spec in specs:
                name = server_id + "_" + spec["name"]
                if len(name) > 64:
                    name = name[:55] + "_" + hashlib.sha256(name.encode()).hexdigest()[:8]
                if name in tools:
                    raise ValueError("Conflicting Tool name: " + name)

                tools[name] = {"spec": {**deepcopy(spec), "name": name},
                               "callable": mcp_invoker(adapter, server_id, user, session, spec["name"]),
                               "type": "mcp", "tool_id": identifier, "direct": False}
        return tools, instructions
    except BaseException:
        for key, session in reversed(created):
            await session.close()
            sessions.pop(key, None)
        raise

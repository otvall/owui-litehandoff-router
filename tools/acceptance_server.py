"""Loopback-only HTTP provider and real MCP transport for disposable acceptance."""

from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import FastAPI
from mcp.server.fastmcp import Context, FastMCP

import acceptance_provider

mcp = FastMCP("Router acceptance MCP", streamable_http_path="/", stateless_http=True, json_response=True)
TRACE = Path(__file__).resolve().parent / "mcp-trace.jsonl"


@mcp.tool()
async def mcp_echo(value: str, ctx: Context) -> str:
    actor = ctx.request_context.request.headers.get("x-execution-user", "")
    record = {"value": value, "actor": actor}
    with TRACE.open("a") as trace:
        trace.write(json.dumps(record) + "\n")
    return json.dumps(record)


mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = FastAPI(lifespan=lifespan)
app.mount("/mcp", mcp_app)
app.mount("/", acceptance_provider.app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8766)

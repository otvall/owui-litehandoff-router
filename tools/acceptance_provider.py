"""Deterministic local provider for native plumbing tests; not an LLM eval."""

import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
TRACE = Path(__file__).resolve().parents[1] / ".cache/provider-trace.jsonl"


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "local"}
                                       for model in ("router-llm", "specialist-a", "specialist-b")]}


@app.post("/v1/chat/completions")
async def completion(request: Request):
    body = await request.json()
    TRACE.parent.mkdir(parents=True, exist_ok=True)
    with TRACE.open("a") as trace:
        trace.write(json.dumps(body, ensure_ascii=False) + "\n")
    messages = body.get("messages", [])
    def real_user(message):
        content = message.get("content")
        image_result = isinstance(content, list) and content and isinstance(content[0], dict) and content[0].get("text") == "Here are the images from the tool results above. Please analyze them."
        return message.get("role") == "user" and not image_result
    user_index = next((i for i in range(len(messages) - 1, -1, -1) if real_user(messages[i])), -1)
    question = str(messages[user_index].get("content", "")) if user_index >= 0 else ""
    current = messages[user_index + 1:]
    tools = {t["function"]["name"]: t["function"] for t in body.get("tools", [])}
    for message in current:
        if message.get("role") == "tool":
            content = message.get("content", "")
            try:
                value = json.loads(content) if isinstance(content, str) else content
            except (ValueError, TypeError):
                value = {}
            if isinstance(value, dict) and value.get("error") or isinstance(content, str) and content.startswith("Error:"):
                return JSONResponse({"error": {"message": "Tool execution failed: " + str(content)}}, status_code=503)
    if "provider error" in question.lower():
        return JSONResponse({"error": {"message": "Test provider failure"}}, status_code=503)
    if "overflow" in question.lower():
        return JSONResponse({"error": {"message": "Context length exceeded"}}, status_code=400)
    function, args, answer = None, {}, "General answer"
    if body["model"] == "router-llm" and "profile" in question.lower() and "lite_delegate" in tools:
        enum = tools["lite_delegate"]["parameters"]["properties"]["agent_id"]["enum"]
        target = next((i for i in enum if i.startswith("agent-b") and "profile b" in question.lower()), enum[0])
        has_read = any(c.get("function", {}).get("name") == "view_skill"
                       for m in current for c in m.get("tool_calls", []))
        if not has_read:
            function, args = "view_skill", {"id": "route-" + target.removeprefix("agent-")}
        else:
            if "revoke access" in question.lower():
                gate = Path(__file__).resolve().parent
                (gate / "handoff-ready").write_text(question)
                for _ in range(300):
                    if (gate / "handoff-continue").exists():
                        break
                    await asyncio.sleep(0.05)
                else:
                    return JSONResponse({"error": {"message": "Acceptance handoff gate timed out"}}, status_code=504)
            function, args = "lite_delegate", {"agent_id": target}
    elif body["model"].startswith("specialist"):
        working_skill = None
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, str) and content.startswith("Available skills"):
                for entry in json.loads(content.split("\n", 1)[1]):
                    if entry["skill_id"].startswith("work-"):
                        working_skill = entry["skill_id"]
        has_working_read = any(c.get("function", {}).get("name") == "view_skill"
                               for m in current for c in m.get("tool_calls", []))
        has_lookup = any(c.get("function", {}).get("name") == "profile_lookup"
                         for m in current for c in m.get("tool_calls", []))
        if has_lookup and "fail after lookup" in question.lower():
            return JSONResponse({"error": {"message": "Test provider failure after Tool"}}, status_code=503)
        mcp_name = next((name for name in tools if name.endswith("_mcp_echo")), None)
        has_mcp = mcp_name and any(c.get("function", {}).get("name") == mcp_name
                                   for m in current for c in m.get("tool_calls", []))
        if working_skill and not has_working_read:
            function, args = "view_skill", {"id": working_skill}
        elif not has_lookup:
            function, args = "profile_lookup", {"query": question}
        elif mcp_name and not has_mcp:
            function, args = mcp_name, {"value": "MCP_RESULT_42"}
        elif "profile_finish" in tools and not any(c.get("function", {}).get("name") == "profile_finish"
                                                   for m in current for c in m.get("tool_calls", [])):
            function, args = "profile_finish", {}
        else:
            answer = body["model"] + " final: TOOL_RESULT_42"
    call_id = "call_" + uuid4().hex[:10]
    message = {"role": "assistant", "content": None if function else answer}
    finish = "tool_calls" if function else "stop"
    if function:
        message["tool_calls"] = [{"id": call_id, "type": "function", "function": {"name": function, "arguments": json.dumps(args)}}]
    response_id = "chatcmpl-" + uuid4().hex
    base = {"id": response_id, "created": int(time.time()), "model": body["model"]}
    if not body.get("stream"):
        return {**base, "object": "chat.completion", "choices": [{"index": 0, "message": message, "finish_reason": finish}]}

    async def stream():
        delta = dict(message)
        if function:
            delta["tool_calls"][0]["index"] = 0
        yield "data: " + json.dumps({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n"
        yield "data: " + json.dumps({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8766)

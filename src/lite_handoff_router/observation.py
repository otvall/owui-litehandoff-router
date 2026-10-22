"""Observe provider Tool calls to preserve their generation and executor."""

from copy import deepcopy
import codecs
import json


def observe_response(response, state, executor):
    state["generation"] = state.get("generation", 0) + 1
    generation = state["generation"]
    calls = {}
    finished = False

    def event(data):
        if finished:
            return
        for choice in data.get("choices", []):
            for ordinal, call in enumerate((choice.get("delta") or choice.get("message") or {}).get("tool_calls") or []):
                key = ("chat", choice.get("index", 0), call.get("index", ordinal))
                entry = calls.setdefault(key, {"id": "", "name": "", "arguments": ""})
                if call.get("id"):
                    entry["id"] = call["id"]
                function = call.get("function") or {}
                entry["name"] += function.get("name") or ""
                arguments = function.get("arguments") or ""
                entry["arguments"] += json.dumps(arguments) if isinstance(arguments, dict) else arguments
        item = data.get("item") or {}
        if item.get("type") == "function_call":
            key = ("responses", data.get("output_index", item.get("id")))
            entry = calls.setdefault(key, {"id": "", "name": "", "arguments": ""})
            entry.update(id=item.get("call_id") or item.get("id") or entry["id"],
                         name=item.get("name") or entry["name"])
            if item.get("arguments"):
                entry["arguments"] = item["arguments"]
        elif data.get("type") == "response.function_call_arguments.delta":
            key = ("responses", data.get("output_index"))
            entry = calls.setdefault(key, {"id": "", "name": "", "arguments": ""})
            entry["arguments"] += data.get("delta", "")
        if any(choice.get("finish_reason") for choice in data.get("choices", [])) \
                or data.get("type") in {"response.completed", "response.failed", "response.incomplete"}:
            finish()

    def finish():
        nonlocal finished
        if finished:
            return
        finished = True
        for call in calls.values():
            try:
                arguments = json.loads(call["arguments"])
            except (TypeError, ValueError):
                continue
            if call["id"] and call["name"] and isinstance(arguments, dict):
                state.setdefault("audit", []).append({**call, "arguments": arguments,
                    "generation": generation, "executor": deepcopy(executor)})

    if isinstance(response, dict):
        event(response)
        for ordinal, item in enumerate(response.get("output") or []):
            event({"output_index": ordinal, "item": item})
        finish()
        return response
    if not hasattr(response, "body_iterator"):
        return response
    original = response.body_iterator

    async def stream():
        buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            async for chunk in original:
                buffer += decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        finish()
                    if line and line != "[DONE]":
                        try:
                            data = json.loads(line)
                            if isinstance(data, dict):
                                event(data)
                        except ValueError:
                            pass
                yield chunk
        finally:
            finish()
            if hasattr(original, "aclose"):
                await original.aclose()
    response.body_iterator = stream()
    return response

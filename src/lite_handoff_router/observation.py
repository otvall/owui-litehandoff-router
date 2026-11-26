"""Inspect model streams and preserve tool and usage evidence as they pass through."""

from copy import deepcopy
import json

from .provider_errors import ProviderContextOverflow, provider_error, provider_event_error
from .stream_lines import StreamLines

INITIAL_STREAM_LIMIT = 65536


def initial_completion_event(line):
    if not line.startswith(b"data:"):
        return False
    try:
        event = json.loads(line[5:])
    except ValueError:
        return True
    if not isinstance(event, dict):
        return True
    if error := provider_event_error(event):
        failure = provider_error(error)
        if isinstance(failure, ProviderContextOverflow):
            raise failure
        return True
    kind = event.get("type")
    if kind and kind not in {"response.created", "response.in_progress"}:
        return True
    return any(
        choice.get("finish_reason") or
        any(value for key, value in (choice.get("delta") or {}).items() if key != "role")
        for choice in event.get("choices") or ()
    )


async def check_initial_stream(response):
    """Catch context overflow before exposing any model output to Open WebUI."""
    if not hasattr(response, "body_iterator"):
        return response
    stream = aiter(response.body_iterator)
    prefix = []
    framer = StreamLines()
    try:
        async for chunk in stream:
            prefix.append(chunk)
            chunk_bytes = chunk.encode() if isinstance(chunk, str) else chunk
            if framer.received + len(chunk_bytes) > INITIAL_STREAM_LIMIT:
                break
            if any(initial_completion_event(line) for line in framer.feed(chunk_bytes)):
                break
        else:
            for line in framer.finish():
                initial_completion_event(line)
    except BaseException:
        if hasattr(stream, "aclose"):
            await stream.aclose()
        raise

    async def replay():
        try:
            for chunk in prefix:
                yield chunk
            async for chunk in stream:
                yield chunk
        finally:
            if hasattr(stream, "aclose"):
                await stream.aclose()

    response.body_iterator = replay()
    return response


def routing_completion_status(event):
    if provider_event_error(event) or event.get("type") in {"response.failed", "response.incomplete"}:
        return "failed"
    if event.get("type") == "response.completed":
        output = (event.get("response") or {}).get("output") or []
        return "tools_pending" if any(item.get("type") == "function_call" for item in output) else "completed"
    reasons = {choice.get("finish_reason") for choice in event.get("choices") or ()}
    if reasons & {"length", "content_filter"}:
        return "failed"
    if reasons & {"tool_calls", "function_call"}:
        return "tools_pending"
    if "stop" in reasons:
        return "completed"
    return None


def observe_response(response, state, executor, *, on_usage=None, on_finish=None):
    state["generation"] = state.get("generation", 0) + 1
    generation = state["generation"]
    calls = {}
    finished = False
    usage = None
    routing_status = None

    def event(data):
        nonlocal usage, routing_status
        if on_finish:
            routing_status = routing_completion_status(data) or routing_status
        if on_usage:
            reported = data.get("usage")
            if not reported and data.get("type") == "response.completed":
                reported = (data.get("response") or {}).get("usage")
            if isinstance(reported, dict) and reported:
                usage = reported
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
                arguments = None
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

    def consume(line):
        line = line.strip()
        if line.startswith(b"data:"):
            line = line[5:].strip()
        if line == b"[DONE]":
            finish()
        elif line:
            try:
                data = json.loads(line)
            except ValueError:
                return
            if isinstance(data, dict):
                event(data)

    async def stream():
        framer = StreamLines()
        try:
            async for chunk in original:
                for line in framer.feed(chunk):
                    consume(line)
                yield chunk
            for line in framer.finish():
                consume(line)
        finally:
            finish()
            if hasattr(original, "aclose"):
                await original.aclose()
            if on_usage:
                await on_usage(usage)
            if on_finish:
                await on_finish(routing_status or "incomplete")
    response.body_iterator = stream()
    return response

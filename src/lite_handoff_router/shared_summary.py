"""One summary of the shared dialogue, reused by Router and its agents."""

from asyncio import CancelledError
from copy import deepcopy
import hashlib
import json
import logging
import re

from .history import agent_dialogue, analyze
from .protocol import tool_image

SUMMARY_LIMIT = 16000
SUMMARY_INPUT_LIMIT = 60000
OVERFLOW_MESSAGES = re.compile(
    r"context.length.exceeded|(?:context|input|prompt) (?:window|length)(?: limit)? "
    r"(?:is |was )?(?:exceeded|too (?:long|large))|maximum context length|"
    r"(?:exceeds|exceeded) (?:the )?(?:maximum |max(?:imum)? supported )?(?:model )?context length|"
    r"(?:input|prompt) (?:is )?too long|input length \(\d+\) is longer than|"
    r"(?:input|prompt)(?: length)?\s+\([^)]*tokens?[^)]*\) "
    r"(?:is longer than|exceeds the maximum allowed length)", re.IGNORECASE,
)


class ProviderContextOverflow(RuntimeError):
    pass


def propagate_provider_error(detail):
    text = str(detail)
    if OVERFLOW_MESSAGES.search(text):
        raise ProviderContextOverflow(text)
    raise RuntimeError(text)


def actual_user(message):
    return message.get("role") == "user" and not tool_image(message)


def shared_turns(state):
    messages = state["source"]
    boundary = state["user_boundary"]
    starts = [index for index, message in enumerate(messages[:boundary]) if actual_user(message)]
    exchanges = analyze(messages, current_boundary=boundary, audit=state["audit"])
    # Keep visible dialogue while removing confirmed Router handoff chatter.
    content, packets = agent_dialogue(state, None, exchanges, boundary)
    digest = hashlib.sha256(b"lite-shared-summary-v1")
    turns = []
    for start, end in zip(starts, starts[1:] + [boundary]):
        original = messages[start:end]
        visible = [m for m in original if m.get("role") in ("user", "assistant") and not tool_image(m)]
        if any(m.get("files") or m.get("embeds") or
               not isinstance(m.get("content") or "", str) or "@@@VIZ-START" in (m.get("content") or "")
               for m in visible):
            break
        if any(m.get("role") == "tool" and (m.get("files") or m.get("embeds")) or tool_image(m)
               for m in original):
            break
        dialogue = []
        for i in range(start, end):
            message = messages[i]
            if actual_user(message):
                dialogue.append({"role": "user", "content": message.get("content") or ""})
            for item in packets.get(i, ()):
                if item["role"] == "tool":
                    dialogue.append({"role": "assistant", "content": item["content"]})
            if message["role"] == "assistant" and (text := content.get(i, message.get("content"))):
                dialogue.append({"role": "assistant", "content": text})
        digest.update(json.dumps([messages[start].get("id"), dialogue], ensure_ascii=False).encode())
        turns.append({"key": digest.hexdigest(), "messages": dialogue, "next_user": messages[end]})
    return turns


class SharedSummary:
    def __init__(self, adapter, request, user, state, *, emitter=None):
        self.adapter, self.request, self.user, self.state = adapter, request, user, state
        self.emitter = emitter
        self.model_id = state["context_policy"]["shared_summary_model_id"].strip()
        self.turns = shared_turns(state) if self.model_id and not state.get("worker") else []
        self.checkpoint = None

    async def status(self, description, *, done, error=False):
        if self.emitter is not None:
            try:
                await self.emitter({"type": "status", "data": {
                    "action": "lite_shared_summary", "description": description, "done": done, "error": error,
                }})
            except Exception as failure:
                logging.getLogger(__name__).debug("Shared summary status unavailable (%s)", type(failure).__name__)

    def eligible(self, draft, packet):
        users = [m for m in draft["messages"] if actual_user(m) and m != packet]
        # A destination filter may rewrite user messages. Match the whole candidate
        # prefix before dropping any messages from that projection.
        eligible = set()
        for count, turn in enumerate(self.turns, 1):
            if len(users) <= count or (users[count - 1].get("content") or "") != turn["messages"][0]["content"]:
                break
            if users[count].get("content") == turn["next_user"].get("content"):
                eligible.add(turn["key"])
        return eligible

    async def load(self):
        if not self.turns:
            return
        cache = self.state.get("shared_summary_cache")
        if cache is None:
            cache = await self.adapter.shared_summary_cache(self.state, self.user, self.model_id)
            self.state["shared_summary_cache"] = cache
        self.checkpoint = next((turn for turn in reversed(self.turns) if turn["key"] in cache), None)

    def apply(self, draft, packet):
        if not self.checkpoint or self.checkpoint["key"] not in self.eligible(draft, packet):
            return False
        messages = draft["messages"]
        count = self.turns.index(self.checkpoint) + 1
        users = [i for i, m in enumerate(messages) if actual_user(m) and m != packet]
        end = users[count]
        start = users[0]
        kept = [message for message in messages[start:end] if message == packet]
        summary = self.state["shared_summary_cache"][self.checkpoint["key"]]
        messages[start:end] = [{"role": "user", "content": "[Shared conversation summary]\n" + summary}, *kept]
        return True

    async def extend(self, draft, packet, full_messages):
        candidate = {**draft, "messages": full_messages}
        eligible = self.eligible(candidate, packet)
        start = self.turns.index(self.checkpoint) + 1 if self.checkpoint else 0
        added, length = [], 0
        for turn in self.turns[start:]:
            length += sum(len(message["content"]) for message in turn["messages"])
            if length > SUMMARY_INPUT_LIMIT:
                break
            added.append(turn)
        while added and added[-1]["key"] not in eligible:
            added.pop()
        if not added:
            return False
        previous = self.state["shared_summary_cache"].get(self.checkpoint["key"]) if self.checkpoint else None
        description = "Не удалось сократить контекст"
        failed = True
        try:
            await self.status("Сжимаю контекст…", done=False)
            try:
                summary = await self.adapter.generate_shared_summary(
                    self.request, self.user, self.model_id,
                    [message for turn in added for message in turn["messages"]], previous)
            except Exception as error:
                logging.getLogger(__name__).warning("Shared summary unavailable (%s)", type(error).__name__)
                return False
            if not 0 < len(summary.strip()) <= SUMMARY_LIMIT:
                return False
            self.checkpoint = added[-1]
            key = self.checkpoint["key"]
            self.state["shared_summary_cache"][key] = summary
            await self.adapter.save_shared_summary(self.state, self.user, self.model_id, key, summary)
            draft["messages"][:] = deepcopy(full_messages)
            applied = self.apply(draft, packet)
            if applied:
                description = "Контекст сокращён"
                failed = False
            return applied
        except CancelledError:
            description = "Сжатие контекста отменено"
            failed = False
            raise
        finally:
            await self.status(description, done=True, error=failed)


def prompt_token_count(usage):
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "input_tokens", "prompt_eval_count"):
            value = usage.get(key)
            if type(value) is int and value > 0:
                return value
    return None


def last_router_prompt_tokens(state, model_id):
    current = prompt_token_count((state.get("router_usage") or {}).get(model_id))
    return current or prompt_token_count((state.get("historical_router_usage") or {}).get(model_id))


async def check_initial_stream(response):
    if not hasattr(response, "body_iterator"):
        return response
    stream = aiter(response.body_iterator)
    prefix, pending, size = [], b"", 0
    try:
        async for chunk in stream:
            prefix.append(chunk)
            data = chunk if isinstance(chunk, bytes) else chunk.encode()
            pending += data
            size += len(data)
            if size > 65536:
                break
            complete = False
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if not line.startswith(b"data:"):
                    continue
                try:
                    event = json.loads(line[5:])
                except ValueError:
                    complete = True
                    break
                if not isinstance(event, dict):
                    complete = True
                    break
                error = event.get("error")
                if not error and event.get("type") == "response.failed":
                    error = (event.get("response") or {}).get("error")
                if error and OVERFLOW_MESSAGES.search(str(error)):
                    raise ProviderContextOverflow(str(error))
                choices = event.get("choices") or ()
                kind = event.get("type")
                if error or kind and kind not in {"response.created", "response.in_progress"} or any(choice.get("finish_reason") or
                        any(value for key, value in (choice.get("delta") or {}).items() if key != "role")
                        for choice in choices):
                    complete = True
                    break
            if complete:
                break
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

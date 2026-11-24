"""One summary of the shared dialogue, reused by Router and its agents."""

from asyncio import CancelledError
from copy import deepcopy
import hashlib
import json
import logging
import re
import traceback
from pathlib import PurePath

from .history import agent_dialogue, analyze, describe_visualizations
from .protocol import tool_image

SUMMARY_INPUT_LIMIT = 60000
SUMMARY_TEXT_PARTS = {"text", "input_text", "output_text"}
SUMMARY_INLINE_MEDIA = re.compile(r"data:(?:image|audio|video)/[\w.+-]+;base64,[A-Za-z0-9+/=_-]+", re.IGNORECASE)
OVERFLOW_MESSAGES = re.compile(
    r"context.length.exceeded|(?:context|input|prompt) (?:window|length)(?: limit)? "
    r"(?:is |was )?(?:exceeded|too (?:long|large))|maximum context length|"
    r"(?:exceeds|exceeded) (?:the )?(?:maximum |max(?:imum)? supported )?(?:model )?context length|"
    r"(?:input|prompt) (?:is )?too long|input length \(\d+\) is longer than|"
    r"(?:input|prompt)(?: length)?\s+\([^)]*tokens?[^)]*\) "
    r"(?:is longer than|exceeds the maximum allowed length)", re.IGNORECASE,
)

EXACT_REFERENCE = re.compile(
    r"(?<![\w-])(?:[A-ZА-ЯЁ]{2,12}-\d{2,16}|\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})(?![\w-])"
)


class ProviderContextOverflow(RuntimeError):
    pass


class SummaryGenerationError(RuntimeError):
    """A diagnostic that is safe to display in the chat."""


def summary_error_details(error):
    details = []
    for failure in (error, error.__cause__):
        if failure is None:
            continue
        message = str(failure).strip()
        if message.startswith(("{", "[")):
            try:
                payload = json.loads(message)
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                payload = payload.get("error", payload)
                message = payload.get("message", "") if isinstance(payload, dict) else ""
            else:
                message = ""
        if isinstance(message, str):
            message = re.split(r"(?i)\b(?:headers|request body|request payload|messages|prompt)\s*[:=]", message)[0]
            message = re.sub(r"(?i)https?://[^\s<>\"']+", "[URL скрыт]", message)
            message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [скрыто]", message)
            message = re.sub(
                r"""(?i)(\b(?:api[-_ ]?key|authorization|access[-_ ]?token|token|password|secret|cookie)\b["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)""",
                r"\1[скрыто]", message,
            )
            message = re.sub(r"\b(?:sk-[\w-]+|hf_[\w-]+|gh[pousr]_[\w-]+)\b", "[скрыто]", message)
            message = " ".join(message.split())[:600]
        else:
            message = ""
        frames = traceback.extract_tb(failure.__traceback__)
        location = ""
        if frames:
            frame = frames[-1]
            location = f" ({PurePath(frame.filename).name}:{frame.lineno}, {frame.name})"
        status_code = getattr(failure, "status_code", None)
        http_status = f" HTTP {status_code}" if type(status_code) is int else ""
        if message or location or http_status:
            details.append(f"{type(failure).__name__}{http_status}{location}" + (f" — {message}" if message else ""))
    return " | ".join(details)


def propagate_provider_error(detail):
    text = str(detail)
    if OVERFLOW_MESSAGES.search(text):
        raise ProviderContextOverflow(text)
    raise RuntimeError(text)


def actual_user(message):
    return message.get("role") == "user" and not tool_image(message)


def summary_projection_turns(messages, packet):
    """Completed turns as received before/after destination inlets."""
    starts = [i for i, message in enumerate(messages) if actual_user(message) and message != packet]
    sequence = [messages[start:end] for start, end in zip(starts, starts[1:])]
    return [[message for message in turn if message.get("role") != "system" and message != packet]
            for turn in sequence]


def recap_dialogue_text(content):
    if isinstance(content, list):
        content = "\n".join(part["text"] for part in content
                            if isinstance(part, dict) and part.get("type") in SUMMARY_TEXT_PARTS
                            and isinstance(part.get("text"), str))
    return SUMMARY_INLINE_MEDIA.sub("[Медиа было в исходном ходе.]", content) if isinstance(content, str) else ""


def recap_agent_activity(content):
    packet = json.loads(content)
    if packet.get("reply"):
        packet["reply"] = describe_visualizations(recap_dialogue_text(packet["reply"]), [])
    for event in packet["events"]:
        if event["type"] == "assistant":
            event["content"] = describe_visualizations(recap_dialogue_text(event["content"]), [])
    return json.dumps(packet, ensure_ascii=False)


def shared_turns(state):
    messages = state["source"]
    boundary = state["user_boundary"]
    starts = [index for index, message in enumerate(messages[:boundary]) if actual_user(message)]
    exchanges = analyze(messages, current_boundary=boundary, audit=state["audit"])
    # Keep visible dialogue while removing confirmed Router handoff chatter.
    content, packets = agent_dialogue(state, None, exchanges, boundary)
    # Older checkpoints may contain fallback excerpts or truncated generations.
    digest = hashlib.sha256(b"lite-shared-summary-v2")
    turns = []
    for start, end in zip(starts, starts[1:] + [boundary]):
        source_user = messages[start]
        dialogue, visible_dialogue = [], []
        for i in range(start, end):
            message = messages[i]
            if i == start:
                text = recap_dialogue_text(source_user.get("content"))
                line = {"role": "user", "content": text}
                dialogue.append(line)
                visible_dialogue.append(line)
            for item in packets.get(i, ()):
                if item["role"] == "tool":
                    dialogue.append({"role": "assistant", "content": recap_agent_activity(item["content"])})
            if message["role"] == "assistant":
                text = recap_dialogue_text(content.get(i, message.get("content")))
                if text:
                    line = {"role": "assistant", "content": describe_visualizations(text, [])}
                    dialogue.append(line)
                    visible_dialogue.append(line)
        if any(m.get("files") or m.get("embeds") or
               isinstance(m.get("content"), list) and any(
                   isinstance(part, dict) and part.get("type") not in SUMMARY_TEXT_PARTS
                   for part in m["content"])
               for m in messages[start:end] if m.get("role") in {"user", "assistant", "tool"}):
            dialogue.append({"role": "assistant", "content": "[В исходном ходе были медиа или вложения.]"})
        digest.update(json.dumps([source_user.get("id"), dialogue], ensure_ascii=False).encode())
        turns.append({"key": digest.hexdigest(), "messages": dialogue, "visible_dialogue": visible_dialogue,
                      "source_user": source_user, "next_user": messages[end]})
    return turns


def shared_summary_reference(text, covered):
    """Keep exact values from visible text beside the model-written recap."""
    groups = []
    for number, turn in enumerate(covered, 1):
        references = dict.fromkeys(
            match.group() for message in turn["visible_dialogue"]
            for match in EXACT_REFERENCE.finditer(message["content"])
        )
        if references:
            groups.append(f"Turn {number}: " + ", ".join(list(references)[:12]))
    if not groups:
        return text
    return "[Exact references from the original dialogue]\n" + "\n".join(groups[-12:]) + "\n\n" + text


class SharedSummary:
    def __init__(self, adapter, request, user, state, *, emitter=None):
        self.adapter, self.request, self.user, self.state = adapter, request, user, state
        self.emitter = emitter
        policy = state["context_policy"]
        self.model_id = policy["shared_summary_model_id"].strip()
        self.context_percent = policy["shared_summary_context_percent"]
        self.turns = shared_turns(state) if self.model_id and not state.get("worker") else []
        self.checkpoint = None
        self.applied_turns = 0

    async def status(self, description, *, done, error=False, action="lite_shared_summary"):
        if self.emitter is not None:
            try:
                await self.emitter({"type": "status", "data": {
                    "action": action, "description": description, "done": done, "error": error,
                }})
            except Exception as failure:
                logging.getLogger(__name__).debug("Shared summary status unavailable (%s)", type(failure).__name__)

    def eligible(self, draft, packet):
        messages = draft["messages"]
        users = [i for i, m in enumerate(messages) if actual_user(m) and m != packet]
        guard = self.state.get("shared_summary_unchanged_turns")
        protected = self.state.get("shared_summary_protected", ())
        # A destination filter may rewrite user messages. Match the whole candidate
        # prefix before dropping any messages from that projection.
        eligible = set()
        for count, turn in enumerate(self.turns, 1):
            if (len(users) <= count or (guard is not None and count > guard)
                    or messages[users[count - 1]].get("content") != turn["source_user"].get("content")):
                break
            if any(message in protected for message in messages[users[count - 1]:users[count]]):
                break
            if messages[users[count]].get("content") == turn["next_user"].get("content"):
                eligible.add(turn["key"])
        return eligible

    async def load(self, draft=None, packet=None):
        if not self.turns:
            return
        cache = self.state.get("shared_summary_cache")
        if cache is None:
            cache = await self.adapter.shared_summary_cache(self.state, self.user, self.model_id)
            self.state["shared_summary_cache"] = cache
        eligible = self.eligible(draft, packet) if draft is not None else {turn["key"] for turn in self.turns}
        self.checkpoint = next((turn for turn in reversed(self.turns)
                                if turn["key"] in eligible and cache.get(turn["key"])), None)

    def applied_summary_key(self):
        if not self.applied_turns:
            return None
        key = self.turns[self.applied_turns - 1]["key"]
        text = self.state["shared_summary_cache"][key]
        return key + ":" + hashlib.sha256(text.encode()).hexdigest()

    def apply(self, draft, packet):
        if not self.checkpoint or self.checkpoint["key"] not in self.eligible(draft, packet):
            return False
        messages = draft["messages"]
        count = self.turns.index(self.checkpoint) + 1
        users = [i for i, m in enumerate(messages) if actual_user(m) and m != packet]
        end = users[count]
        start = users[0]
        kept = [message for message in messages[start:end] if message == packet]
        summary = shared_summary_reference(self.state["shared_summary_cache"][self.checkpoint["key"]],
                                           self.turns[:count])
        messages[start:end] = [{"role": "user", "content": "[Shared conversation summary]\n" + summary}, *kept]
        self.applied_turns = count
        return True

    async def extend(self, draft, packet, full_messages, *, trigger):
        candidate = {**draft, "messages": full_messages}
        eligible = self.eligible(candidate, packet)
        start = self.turns.index(self.checkpoint) + 1 if self.checkpoint else 0
        candidates = self.turns[start:]
        while candidates and candidates[-1]["key"] not in eligible:
            candidates.pop()
        if not candidates:
            if start < len(self.turns):
                reason = "История в запросе к модели отличается от исходной. Безопасная граница сжатия не найдена."
            elif start:
                reason = "Все доступные старые ходы уже входят в сводку."
            else:
                reason = "В истории ещё нет старых ходов. Текущий ход защищён."
            await self.status(f"Сжатие пропущено ({trigger}). {reason}", done=True)
            return False
        # Open WebUI's estimate for text messages, applied to the summary projection.
        turn_tokens = [sum(4 + (max(1, len(text) // 4) if text else 0)
                           for text in (message["content"] for message in turn["messages"]))
                       for turn in candidates]
        total_tokens = sum(turn_tokens)
        target_tokens = total_tokens * self.context_percent // 100
        added, selected_tokens, length = [], 0, 0
        for turn, tokens in zip(candidates, turn_tokens):
            size = sum(len(message["content"]) for message in turn["messages"])
            # The oldest whole turn stays eligible even for a small percentage.
            if length + size > SUMMARY_INPUT_LIMIT or (added and selected_tokens + tokens > target_tokens):
                break
            added.append(turn)
            selected_tokens += tokens
            length += size
        if not added:
            await self.status(f"Сжатие пропущено ({trigger}). Первый непересказанный ход содержит "
                              f"{size} символов при лимите {SUMMARY_INPUT_LIMIT}.",
                              done=True)
            return False
        cache = self.state["shared_summary_cache"]
        previous = cache[self.checkpoint["key"]] if self.checkpoint else None
        covered_turns = start + len(added)
        description = "Не удалось сократить контекст"
        failed = True
        try:
            await self.status(f"Сжимаю контекст… {trigger}. Модель {self.model_id}. "
                              f"Ходов {len(added)}, символов {length}. "
                              f"Токенов по оценке Open WebUI {selected_tokens} из {total_tokens}. "
                              f"Целевой объём {target_tokens}.", done=False)
            try:
                save_options = {}
                if hasattr(self.adapter, "shared_summary_source_version"):
                    source_version = await self.adapter.shared_summary_source_version(self.state, self.user, covered_turns)
                    save_options = {"covered_turns": covered_turns, "source_version": source_version}
                summary = await self.adapter.generate_shared_summary(
                    self.request, self.user, self.model_id,
                    [message for turn in added for message in turn["messages"]], previous)
            except Exception as error:
                logging.getLogger(__name__).warning("Shared summary unavailable (%s)", type(error).__name__)
                if isinstance(error, SummaryGenerationError):
                    reason = str(error)
                elif isinstance(error, TimeoutError):
                    reason = "Модель суммаризации не ответила за 45 секунд."
                elif isinstance(error, ProviderContextOverflow):
                    reason = "Переполнено окно модели суммаризации."
                elif isinstance(error, AttributeError) and isinstance(error.name, str) and error.name.isidentifier():
                    reason = f"Ошибка Open WebUI (AttributeError: {type(error.obj).__name__}.{error.name})."
                else:
                    reason = f"Ошибка запроса к модели суммаризации ({type(error).__name__})."
                description = f"{description}. {reason}"
                if details := summary_error_details(error):
                    description += f" Подробности — {details}"
                return False
            if not summary:
                description += ". Модель вернула пустую сводку."
                return False
            checkpoint = added[-1]
            key = checkpoint["key"]
            saved = await self.adapter.save_shared_summary(
                self.state, self.user, self.model_id, key, summary, **save_options)
            if saved is False:
                description += ". Исходная история изменилась во время суммаризации."
                return False
            self.checkpoint = checkpoint
            cache[key] = saved if isinstance(saved, str) else summary
            draft["messages"][:] = deepcopy(full_messages)
            applied = self.apply(draft, packet)
            if applied:
                description = f"Контекст сокращён. Ходов в общей сводке {self.applied_turns}."
                failed = False
            else:
                description += ". Граница сводки не совпала с историей запроса."
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


def last_prompt_tokens(state, model_id, *, agent_id, summary_key):
    if agent_id:
        key = agent_id, model_id
        current, historical = "agent_usage", "historical_agent_usage"
    else:
        key = model_id
        current, historical = "router_usage", "historical_router_usage"
    sample = state.get(current, {}).get(key, state.get(historical, {}).get(key))
    if sample and "summary_key" in sample and sample["summary_key"] == summary_key:
        return prompt_token_count(sample)
    return None


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

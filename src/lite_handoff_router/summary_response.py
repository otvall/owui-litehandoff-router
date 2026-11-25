"""Normalize provider summary responses across Chat Completions and Responses."""

from dataclasses import dataclass
import json
import re

from .provider_errors import provider_error, provider_event_error
from .stream_lines import StreamLines

SUMMARY_STREAM_LIMIT = 4 * 1024 * 1024


class SummaryGenerationError(RuntimeError):
    """A diagnostic that is safe to display in the chat."""


@dataclass
class SummaryStreamStats:
    frames: int = 0
    line_bytes: int = 0
    json_events: int = 0
    text_fragments: int = 0
    done: bool = False
    body_format: str = ""

    def missing_finish(self):
        reason = ("Поток сводки прислал [DONE] без finish_reason или response.completed." if self.done
                  else "Поток сводки закрылся без завершающего события.")
        body_format = f" Формат тела: {self.body_format}." if self.body_format else ""
        return SummaryGenerationError(
            f"{reason} SSE-кадров: {self.frames}, фрагментов текста: {self.text_fragments}, "
            f"байт в строках: {self.line_bytes}, JSON-событий без SSE: {self.json_events}." + body_format
        )


async def summary_stream_lines(stream):
    if isinstance(stream, (str, bytes)):
        async def buffered_chunks():
            yield stream

        chunks = buffered_chunks()
    else:
        chunks = stream
    framer = StreamLines()
    async for chunk in chunks:
        chunk = chunk.encode() if isinstance(chunk, str) else chunk
        if framer.received + len(chunk) > SUMMARY_STREAM_LIMIT:
            raise SummaryGenerationError("Поток сводки превысил предел в 4 МиБ.")
        for line in framer.feed(chunk):
            yield line
    for line in framer.finish():
        yield line


def summary_unframed_events(body):
    if not body.strip():
        return [], ""
    if body.lstrip().startswith(b"<"):
        status = re.search(rb"(?i)<title>\s*([45][0-9]{2})\b", body[:512])
        return [], f"HTML/XML, код {status[1].decode()}" if status else "HTML/XML"
    try:
        decoded = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], "некорректный UTF-8"
    try:
        payload = json.loads(decoded)
        events = payload if isinstance(payload, list) else [payload]
    except ValueError:
        try:
            events = [json.loads(line) for line in decoded.split("\n") if line.strip()]
        except ValueError:
            return [], "нераспознанное тело"
    if events and all(isinstance(event, dict) for event in events):
        return events, ""
    return [], "неожиданная структура JSON"


async def summary_stream_events(stream, stats):
    if isinstance(stream, list):
        if not all(isinstance(event, dict) for event in stream):
            raise SummaryGenerationError("Некорректный список событий в ответе сводки.")
        stats.json_events = len(stream)
        for event in stream:
            yield event
        return

    unframed = bytearray()
    async for line in summary_stream_lines(stream):
        stats.line_bytes += len(line)
        if not line.startswith(b"data:"):
            if not stats.frames:
                unframed.extend(line + b"\n")
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            stats.done = True
            return
        if not data:
            continue
        try:
            event = json.loads(data)
        except ValueError as error:
            raise SummaryGenerationError("Некорректное событие в потоке сводки.") from error
        if not isinstance(event, dict):
            raise SummaryGenerationError("Некорректное событие в потоке сводки.")
        stats.frames += 1
        unframed.clear()
        yield event
    if not stats.frames:
        events, stats.body_format = summary_unframed_events(unframed)
        stats.json_events = len(events)
        for event in events:
            yield event


def summary_response_document(event, convert_responses):
    if error := provider_event_error(event):
        raise provider_error(error)
    kind = event.get("type")
    status = event.get("status") if event.get("object") == "response" else None
    if kind == "response.incomplete" or status in ("incomplete", "failed"):
        raise SummaryGenerationError("Провайдер не завершил генерацию сводки.")
    if kind == "response.completed" or status == "completed":
        document = event.get("response") if kind == "response.completed" else event
        if not isinstance(document, dict):
            raise SummaryGenerationError("Некорректное событие в потоке сводки.")
        return convert_responses(document)
    return None


def summary_choices(document):
    choices = document.get("choices") or []
    if not isinstance(choices, list):
        raise SummaryGenerationError("Некорректное поле choices в ответе сводки.")
    return choices


def summary_message(value):
    message = value or {}
    if not isinstance(message, dict):
        raise SummaryGenerationError("Некорректное сообщение в ответе сводки.")
    return message


def summary_tool_call(message):
    return bool(message.get("tool_calls") or message.get("function_call"))


def completed_summary(content, finish_reason, tool_called):
    if finish_reason == "length":
        raise SummaryGenerationError("Генерация сводки достигла лимита модели (finish_reason=length).")
    if tool_called:
        raise SummaryGenerationError("Модель суммаризации вернула вызов инструмента.")
    if finish_reason != "stop":
        raise SummaryGenerationError("Провайдер прервал генерацию сводки.")
    return content.strip() if isinstance(content, str) else ""


def summary_document_text(document):
    choices = summary_choices(document)
    choice = summary_message(choices[0] if choices else None)
    message = summary_message(choice.get("message"))
    return completed_summary(message.get("content"), choice.get("finish_reason"), summary_tool_call(message))


async def collect_summary_stream(stream, convert_responses):
    stats = SummaryStreamStats()
    parts = []
    snapshot = finish_reason = None
    tool_called = False
    try:
        async for event in summary_stream_events(stream, stats):
            document = summary_response_document(event, convert_responses)
            if document is not None:
                return summary_document_text(document)
            for choice in summary_choices(event):
                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                    continue
                delta = summary_message(choice.get("delta"))
                full = summary_message(choice.get("message"))
                if isinstance(delta.get("content"), str):
                    parts.append(delta["content"])
                    stats.text_fragments += 1
                if isinstance(full.get("content"), str):
                    snapshot = full["content"]
                    stats.text_fragments += 1
                tool_called = tool_called or summary_tool_call(delta) or summary_tool_call(full)
                finish_reason = choice.get("finish_reason") or finish_reason
    finally:
        if hasattr(stream, "aclose"):
            await stream.aclose()
    if not finish_reason:
        raise stats.missing_finish()
    content = snapshot if snapshot is not None else "".join(parts)
    return completed_summary(content, finish_reason, tool_called)


async def summary_response_text(response, *, convert_responses):
    if isinstance(response, dict):
        document = summary_response_document(response, convert_responses)
        return summary_document_text(response if document is None else document)
    stream = getattr(response, "body_iterator", response)
    if not isinstance(stream, (str, bytes, list)) and not hasattr(stream, "__aiter__"):
        raise SummaryGenerationError("Провайдер вернул ответ сводки в неожиданном формате.")
    return await collect_summary_stream(stream, convert_responses)

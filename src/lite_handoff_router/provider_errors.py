"""Classify errors shared by ordinary model calls and summary generation."""

import re

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


def provider_error(detail):
    text = str(detail)
    return ProviderContextOverflow(text) if OVERFLOW_MESSAGES.search(text) else RuntimeError(text)


def provider_event_error(event):
    error = event.get("error")
    if error:
        return error
    if event.get("type") in {"error", "response.failed"}:
        response = event.get("response")
        if isinstance(response, dict) and response.get("error"):
            return response["error"]
        return event
    return None

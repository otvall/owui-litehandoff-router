"""Measure the model-facing request without contacting the provider."""

from dataclasses import dataclass
import json
import logging

from .protocol import AGENT_HISTORY_HEADING, SHARED_SUMMARY_PREFIX, object_value

MEDIA_PARTS = {"image_url", "input_image", "video_url", "audio_url", "input_audio", "file", "input_file"}
BUDGET_LOG = logging.getLogger(__name__)


class ContextBudgetExceeded(RuntimeError):
    """The prepared model request exceeds its configured input budget."""


@dataclass(frozen=True)
class ContextBudgetEstimate:
    breakdown: dict[str, int]
    mode: str
    exact_total: int | None = None

    @property
    def total(self):
        return self.exact_total if self.exact_total is not None else sum(self.breakdown.values())


def input_budget(policy, recipient, draft=None):
    configured = policy.get("context_model_input_budgets", {}).get(recipient, policy.get("context_input_budget_tokens"))
    window = policy.get("context_window_tokens")
    limits = [configured] if configured is not None else []
    if window is not None:
        draft = draft or {}
        options = draft.get("options")
        ollama_limit = options.get("num_predict") if isinstance(options, dict) else None
        output_limits = [draft.get(key) for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")]
        output_limits.extend((ollama_limit, policy["context_response_reserve_tokens"]))
        reserve = max(value for value in output_limits if type(value) is int and value > 0)
        limits.append(window - reserve)
    return max(0, min(limits) - policy["context_safety_margin_tokens"]) if limits else None


def estimate_context_budget(draft, *, count_serialized_request=None):
    messages = draft["messages"]
    schemas = draft.get("tools") or []
    breakdown = dict.fromkeys(("system", "dialogue", "tool exchanges", "tool schemas", "summary", "media", "overhead"), 0)

    def count(value):
        if not value:
            return 0
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return (len(text.encode("utf-8", errors="replace")) + 2) // 3

    breakdown["tool schemas"] = count(schemas)
    breakdown["overhead"] = 8 + 6 * len(messages)
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        category = "system" if role == "system" else "tool exchanges" if role == "tool" else "dialogue"
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in MEDIA_PARTS:
                    image = part.get("image_url")
                    detail = image.get("detail") if isinstance(image, dict) else None
                    breakdown["media"] += 256 if detail == "low" else 2048
                else:
                    breakdown[category] += count(part)
        elif isinstance(content, str) and content.startswith(SHARED_SUMMARY_PREFIX):
            breakdown["summary"] += count(content)
        elif isinstance(content, str) and content.startswith(AGENT_HISTORY_HEADING + "\n"):
            _, _, data = content.partition("\n")
            history = object_value(data)
            summary = count(history.get("summary"))
            breakdown["summary"] += summary
            breakdown["dialogue"] += count(content) - summary
        else:
            breakdown[category] += count(content)
        for field in ("tool_calls", "tool_call_id", "name", "reasoning_content", "thinking",
                      "reasoning_details", "reasoning_items", "output"):
            breakdown["tool exchanges"] += count(message.get(field))
    if count_serialized_request is not None and not breakdown["media"]:
        # A model-specific local counter must include the actual chat template
        # and schema serialization. Keep the component estimates diagnostic.
        exact = count_serialized_request(messages, schemas)
        if type(exact) is int and exact >= 0:
            return ContextBudgetEstimate(breakdown, "exact", exact)
    return ContextBudgetEstimate(breakdown, "estimated")


def check_context_budget(estimate, available, *, recipient, before, hidden_results):
    BUDGET_LOG.info("Context budget recipient=%s mode=%s input_before=%d input_after=%d budget=%d hidden_results=%d breakdown=%s",
                    recipient, estimate.mode, before, estimate.total, available, hidden_results, estimate.breakdown)
    if estimate.total > available:
        components = ", ".join(f"{key} {value}" for key, value in estimate.breakdown.items() if value)
        mode = "оценка" if estimate.mode == "estimated" else "точный подсчёт"
        raise ContextBudgetExceeded(
            f"Входной контекст превышает бюджет. {mode}: {estimate.total} токенов при лимите {available}. "
            f"До сжатия {before}. Скрыто результатов: {hidden_results}. {components}.")

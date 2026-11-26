"""Administrative context policy; no client metadata participates in resolution."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

CONTEXT_MIN_RESPONSE_TOKENS = 1024
CONTEXT_SAFETY_MARGIN_TOKENS = 1024


def summary_model_id(state):
    if state.get("worker"):
        return ""
    return ((state.get("context_policy") or {}).get("shared_summary_model_id") or "").strip()


class ContextPolicy(BaseModel):
    model_config = ConfigDict(validate_assignment=True, strict=True)

    tool_result_history_limit: int | None = Field(default=None, ge=0)
    shared_summary_model_id: str = ""
    context_window_tokens: int | None = Field(
        default=None, gt=CONTEXT_MIN_RESPONSE_TOKENS + CONTEXT_SAFETY_MARGIN_TOKENS,
        description="Total context window shared by Router and agents, including output",
    )
    shared_summary_trigger_percent: int = Field(
        default=90, ge=1, le=100,
        description="Percentage of the shared context window that triggers early summarization",
    )

    # Preserve the saved amount of history selected per summary.
    shared_summary_context_percent: SkipJsonSchema[int] = Field(default=60, ge=1, le=100)

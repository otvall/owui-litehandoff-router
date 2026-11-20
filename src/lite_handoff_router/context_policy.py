"""Administrative context policy; no client metadata participates in resolution."""

from pydantic import BaseModel, ConfigDict, Field


class ContextPolicy(BaseModel):
    model_config = ConfigDict(validate_assignment=True, strict=True)

    tool_result_history_limit: int | None = Field(default=None, ge=0)
    shared_summary_model_id: str = ""
    shared_summary_context_percent: int = Field(
        default=60, ge=1, le=100,
        description="Percentage of estimated tokens in eligible unsummarized history to summarize in one update",
    )
    router_summary_trigger_tokens: int = Field(default=40000, gt=0)
    agent_summary_trigger_tokens: int = Field(default=40000, gt=0)

"""Administrative context policy; no client metadata participates in resolution."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


def summary_model_id(state):
    if state.get("worker"):
        return ""
    return ((state.get("context_policy") or {}).get("shared_summary_model_id") or "").strip()


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

    context_input_budget_tokens: int | None = Field(default=None, gt=0)
    context_model_input_budgets: dict[str, Annotated[int, Field(gt=0)]] = Field(default_factory=dict)
    context_window_tokens: int | None = Field(default=None, gt=0, description="Total model context window, including output")
    context_response_reserve_tokens: int = Field(default=1024, gt=0, description="Minimum output reserve for the total window")
    context_safety_margin_tokens: int = Field(default=1024, ge=0)

    @model_validator(mode="after")
    def validate_input_budgets(self):
        limits = list(self.context_model_input_budgets.values())
        if self.context_input_budget_tokens is not None:
            limits.append(self.context_input_budget_tokens)
        if any(limit <= self.context_safety_margin_tokens for limit in limits):
            raise ValueError("Context input budgets must be greater than the safety margin")
        if (self.context_window_tokens is not None
                and self.context_window_tokens <= self.context_response_reserve_tokens + self.context_safety_margin_tokens):
            raise ValueError("Context window must be greater than the response reserve and safety margin")
        return self

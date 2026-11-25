"""Administrative context policy; no client metadata participates in resolution."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema


def summary_model_id(state):
    if state.get("worker"):
        return ""
    return ((state.get("context_policy") or {}).get("shared_summary_model_id") or "").strip()


class ContextPolicy(BaseModel):
    model_config = ConfigDict(validate_assignment=True, strict=True)

    tool_result_history_limit: int | None = Field(default=None, ge=0)
    shared_summary_model_id: str = ""
    context_window_tokens: int | None = Field(default=None, gt=0, description="Total model context window, including output")
    context_model_window_tokens: dict[str, Annotated[int, Field(gt=0)]] = Field(
        default_factory=dict, description="Total context window overrides by Workspace Model ID")

    # Preserve saved policies through Open WebUI's Valves load and save cycle.
    shared_summary_context_percent: SkipJsonSchema[int] = Field(default=60, ge=1, le=100)
    router_summary_trigger_tokens: SkipJsonSchema[int] = Field(default=40000, gt=0)
    agent_summary_trigger_tokens: SkipJsonSchema[int] = Field(default=40000, gt=0)
    context_input_budget_tokens: SkipJsonSchema[int | None] = Field(default=None, gt=0)
    context_model_input_budgets: SkipJsonSchema[dict[str, Annotated[int, Field(gt=0)]]] = Field(default_factory=dict)
    context_response_reserve_tokens: SkipJsonSchema[int] = Field(default=1024, gt=0)
    context_safety_margin_tokens: SkipJsonSchema[int] = Field(default=1024, ge=0)

    @model_validator(mode="after")
    def validate_input_budgets(self):
        limits = list(self.context_model_input_budgets.values())
        if self.context_input_budget_tokens is not None:
            limits.append(self.context_input_budget_tokens)
        if any(limit <= self.context_safety_margin_tokens for limit in limits):
            raise ValueError("Context input budgets must be greater than the safety margin")
        windows = list(self.context_model_window_tokens.values())
        if self.context_window_tokens is not None:
            windows.append(self.context_window_tokens)
        if any(window <= self.context_response_reserve_tokens + self.context_safety_margin_tokens for window in windows):
            raise ValueError("Context window must be greater than the response reserve and safety margin")
        return self

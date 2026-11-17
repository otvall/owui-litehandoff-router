"""Administrative context policy; no client metadata participates in resolution."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveTokens = Annotated[int, Field(gt=0, strict=True)]


class ContextPolicy(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    tool_result_history_limit: int | None = Field(default=None, ge=0, strict=True)
    context_input_budget_tokens: PositiveTokens | None = None
    context_model_input_budgets: dict[str, PositiveTokens] = Field(default_factory=dict)
    context_safety_margin_tokens: int = Field(default=1024, ge=0, strict=True)
    context_summary_enabled: bool = Field(default=False, strict=True)
    context_summary_model_id: str = ""
    context_summary_trigger_ratio: float = Field(default=0.9, gt=0, lt=1, strict=True)
    context_summary_target_ratio: float = Field(default=0.7, gt=0, lt=1, strict=True)
    context_summary_input_budget_tokens: PositiveTokens = 8192
    context_summary_max_tokens: PositiveTokens = 1024
    context_summary_timeout_seconds: PositiveTokens = 60
    context_summary_max_chunks: PositiveTokens = 8

    @model_validator(mode="after")
    def validate_policy(self):
        if self.context_summary_target_ratio >= self.context_summary_trigger_ratio:
            raise ValueError("Summary target must be below trigger")
        budgets = list(self.context_model_input_budgets.values())
        if self.context_input_budget_tokens is not None:
            budgets.append(self.context_input_budget_tokens)
        if any(budget <= self.context_safety_margin_tokens for budget in budgets):
            raise ValueError("Input budget must exceed context safety margin")
        if self.context_summary_input_budget_tokens <= self.context_safety_margin_tokens:
            raise ValueError("Summary input budget must exceed context safety margin")
        if self.context_summary_enabled and (not self.context_summary_model_id.strip() or not budgets):
            raise ValueError("Summary requires an explicit model and input budgets")
        return self

    def usable_budget(self, model_id):
        budget = self.context_model_input_budgets.get(model_id, self.context_input_budget_tokens)
        if budget is None:
            if self.context_summary_enabled:
                raise ValueError("Missing context input budget for model: " + model_id)
            return None
        return budget - self.context_safety_margin_tokens

    def snapshot(self):
        """Revalidate mutable dictionaries and return policy fields only."""
        return ContextPolicy.model_validate({
            name: getattr(self, name) for name in ContextPolicy.model_fields
        }).model_dump()

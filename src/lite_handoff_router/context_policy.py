"""Administrative context policy; no client metadata participates in resolution."""

from pydantic import BaseModel, ConfigDict, Field


class ContextPolicy(BaseModel):
    model_config = ConfigDict(validate_assignment=True, strict=True)

    tool_result_history_limit: int | None = Field(default=None, ge=0)
    shared_summary_model_id: str = ""
    router_summary_trigger_tokens: int = Field(default=40000, gt=0)
    agent_token_count_enabled: bool = Field(default=False, description="Measure agent requests with the provider's tokenize endpoint")

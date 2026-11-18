"""Administrative context policy; no client metadata participates in resolution."""

from pydantic import BaseModel, ConfigDict, Field


class ContextPolicy(BaseModel):
    model_config = ConfigDict(validate_assignment=True, strict=True)

    tool_result_history_limit: int | None = Field(default=None, ge=0)

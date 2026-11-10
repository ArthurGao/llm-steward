from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from llm_steward.services.namespace_service import TOKEN_MAX_TTL, TOKEN_TTL


class UserTokenIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)
    ttl_seconds: int = Field(
        default=int(TOKEN_TTL.total_seconds()),
        ge=60,
        le=int(TOKEN_MAX_TTL.total_seconds()),
        description="Lifetime in seconds: 15 minutes by default, 1 hour at most.",
    )


class UserTokenOut(BaseModel):
    token: str
    token_type: Literal["Bearer"] = "Bearer"
    user_id: str
    expires_at: datetime

"""Python client for llm-steward: governed memory and knowledge for AI apps.

    from llm_steward_client import Steward

    st = Steward(base_url="http://127.0.0.1:8600", api_key="stw_...")
    st.episodes.add(messages=[{"role": "user", "content": "I moved to Shanghai"}], user_id="u1")
    print(st.retrieve("where does the user live", user_id="u1", format="context").context)

`AsyncSteward` has the same resources, awaitable. `Steward.embedded("sqlite+aiosqlite:///x.db")`
runs llm-steward in this process (extra `embedded`). Response objects are the API's own models
(`llm_steward_client.models`), generated from its OpenAPI spec.
"""

from llm_steward_client import _models as models
from llm_steward_client._client import AsyncSteward, Steward
from llm_steward_client._errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    StewardConnectionError,
    StewardError,
    UnprocessableError,
)

__all__ = [
    "AsyncSteward",
    "AuthenticationError",
    "ConflictError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServerError",
    "Steward",
    "StewardConnectionError",
    "StewardError",
    "UnprocessableError",
    "models",
]

"""Input limits shared by every entry point (REST request models and MCP tool arguments), so an
agent cannot send what an HTTP client may not (security review of 71c1cb7)."""

from typing import Annotated

from pydantic import Field

Fact = Annotated[str, Field(min_length=1, max_length=20_000)]  # a memory's text; a suggestion
Query = Annotated[str, Field(min_length=1, max_length=4_000)]
TopK = Annotated[int, Field(ge=1, le=50)]
UserId = Annotated[str, Field(max_length=200)]
MemoryType = Annotated[str, Field(max_length=64)]
Weight = Annotated[float, Field(ge=0, le=1)]  # importance, confidence
CollectionName = Annotated[str, Field(min_length=1, max_length=200)]

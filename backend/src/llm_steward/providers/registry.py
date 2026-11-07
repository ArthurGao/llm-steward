"""The one place that maps configured provider names to instances.

Everything else talks to providers through their Protocol, never by name (backend/AGENTS.md).
"""

import httpx2

from llm_steward.config import Settings
from llm_steward.providers.base import BuiltinProvider, HealthProbe, NotImplementedProvider
from llm_steward.providers.weknora import WeKnoraProvider


def build_providers(settings: Settings, http: httpx2.AsyncClient) -> dict[str, HealthProbe]:
    """Return the configured providers keyed by role: memory_provider, kb_provider."""
    memory: HealthProbe
    match settings.memory_provider:
        case "native":
            memory = BuiltinProvider()
        case "mem0":
            memory = NotImplementedProvider("mem0", "P2")

    kb: HealthProbe
    match settings.kb_provider:
        case "native":
            kb = BuiltinProvider()
        case "weknora":
            kb = WeKnoraProvider(http, settings.weknora_url, settings.provider_timeout_ms / 1000)
        case "ragflow":
            kb = NotImplementedProvider("ragflow", "P7")

    return {"memory_provider": memory, "kb_provider": kb}

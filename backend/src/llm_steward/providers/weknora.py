"""WeKnora KnowledgeProvider. P0 implements health only; the rest of design §8 arrives in P3."""

import httpx2

from llm_steward.providers.base import ComponentHealth


class WeKnoraProvider:
    name = "weknora"

    def __init__(self, http: httpx2.AsyncClient, base_url: str, timeout_s: float) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    async def health(self) -> ComponentHealth:
        try:
            response = await self._http.get(f"{self._base_url}/health", timeout=self._timeout_s)
        except httpx2.TimeoutException:
            return ComponentHealth(self.name, "error", f"timeout after {self._timeout_s:g}s")
        except httpx2.HTTPError as exc:
            return ComponentHealth.from_exception(self.name, exc)
        if response.status_code != 200:
            return ComponentHealth(self.name, "error", f"HTTP {response.status_code}")
        return ComponentHealth(self.name, "ok")

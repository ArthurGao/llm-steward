"""Provider contracts (design §8).

P0 defines only the health part; P1 extends `HealthProbe` into the full `MemoryProvider` /
`KnowledgeProvider` Protocols with capabilities.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

ComponentStatus = Literal["ok", "error", "not_implemented"]


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    status: ComponentStatus
    detail: str | None = None

    @classmethod
    def from_exception(cls, name: str, exc: BaseException) -> "ComponentHealth":
        return cls(name, "error", f"{type(exc).__name__}: {exc}")


class HealthProbe(Protocol):
    @property
    def name(self) -> str: ...

    async def health(self) -> ComponentHealth: ...


@dataclass(frozen=True)
class BuiltinProvider:
    """The native provider runs in-process, so there is nothing remote to probe."""

    name: str = "native"

    async def health(self) -> ComponentHealth:
        return ComponentHealth(self.name, "ok", "builtin")


@dataclass(frozen=True)
class NotImplementedProvider:
    """A configured provider whose adapter belongs to a later stage."""

    name: str
    stage: str

    async def health(self) -> ComponentHealth:
        return ComponentHealth(self.name, "not_implemented", f"adapter arrives in {self.stage}")

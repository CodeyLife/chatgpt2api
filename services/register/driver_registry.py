from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class RegistrationDriver(Protocol):
    def register(self, index: int) -> dict:
        ...

    def close(self, index: int | None = None) -> None:
        ...


DriverFactory = Callable[[dict], RegistrationDriver]


@dataclass(frozen=True)
class RegistrationDriverInfo:
    name: str
    label: str
    supports_agent_identity: bool = False
    supports_codex_oauth: bool = False
    description: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "supports_agent_identity": self.supports_agent_identity,
            "supports_codex_oauth": self.supports_codex_oauth,
            "description": self.description,
        }


_factories: dict[str, DriverFactory] = {}
_metadata: dict[str, RegistrationDriverInfo] = {}


def register_driver(
    name: str,
    factory: DriverFactory,
    *,
    label: str,
    supports_agent_identity: bool = False,
    supports_codex_oauth: bool = False,
    description: str = "",
) -> None:
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("registration driver name is required")
    _factories[key] = factory
    _metadata[key] = RegistrationDriverInfo(
        name=key,
        label=label,
        supports_agent_identity=supports_agent_identity,
        supports_codex_oauth=supports_codex_oauth,
        description=description,
    )


def list_drivers() -> list[dict]:
    return [_metadata[name].as_dict() for name in sorted(_metadata)]


def get_driver_info(name: str) -> RegistrationDriverInfo | None:
    return _metadata.get(str(name or "").strip().lower())


def create_driver(name: str, runtime_config: dict) -> RegistrationDriver:
    key = str(name or "").strip().lower()
    factory = _factories.get(key)
    if factory is None:
        known = ", ".join(sorted(_factories)) or "none"
        raise ValueError(f"unknown registration driver: {name or '<empty>'}; known={known}")
    return factory(runtime_config)

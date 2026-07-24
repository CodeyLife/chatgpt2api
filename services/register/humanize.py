from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable


DEFAULT_DELAYS: dict[str, tuple[float, float]] = {
    "api": (0.45, 1.35),
    "navigate": (1.2, 3.2),
    "challenge": (0.8, 2.4),
    "otp_input": (2.5, 8.0),
    "form": (1.8, 5.0),
    "post_auth": (1.5, 4.0),
    "job_stagger": (0.4, 1.8),
}


@dataclass(frozen=True)
class HumanizeConfig:
    enabled: bool = True
    factor: float = 1.0
    delays: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(DEFAULT_DELAYS))


def _bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def _delay_range(value: Any, fallback: tuple[float, float]) -> tuple[float, float]:
    raw = value
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace("~", ",").replace("-", ",").split(",") if part.strip()]
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        try:
            lo = max(0.0, float(raw[0]))
            hi = max(lo, float(raw[1]))
            return lo, hi
        except (TypeError, ValueError):
            return fallback
    return fallback


def from_runtime_config(runtime_config: dict | None) -> HumanizeConfig:
    source = runtime_config if isinstance(runtime_config, dict) else {}
    cfg = source.get("humanize") if isinstance(source.get("humanize"), dict) else {}
    delays_cfg = cfg.get("delays") if isinstance(cfg.get("delays"), dict) else {}
    delays = {
        key: _delay_range(delays_cfg.get(key), value)
        for key, value in DEFAULT_DELAYS.items()
    }
    try:
        factor = max(0.0, float(cfg.get("factor") if cfg.get("factor") is not None else 1.0))
    except (TypeError, ValueError):
        factor = 1.0
    return HumanizeConfig(
        enabled=_bool(cfg.get("enabled"), True),
        factor=factor,
        delays=delays,
    )


class Humanizer:
    def __init__(
        self,
        config: HumanizeConfig | dict | None = None,
        *,
        sleep_func: Callable[[float], None] = time.sleep,
        random_func: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.config = from_runtime_config({"humanize": config}) if isinstance(config, dict) else config or HumanizeConfig()
        self._sleep = sleep_func
        self._random = random_func

    def delay(self, kind: str = "api", *, minimum: float | None = None, maximum: float | None = None) -> float:
        if not self.config.enabled:
            return 0.0
        fallback = self.config.delays.get(kind, DEFAULT_DELAYS["api"])
        lo = fallback[0] if minimum is None else float(minimum)
        hi = fallback[1] if maximum is None else float(maximum)
        lo = max(0.0, lo * self.config.factor)
        hi = max(lo, hi * self.config.factor)
        seconds = self._random(lo, hi)
        self._sleep(seconds)
        return seconds

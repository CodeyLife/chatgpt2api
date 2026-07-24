from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Callable


def _shift_year_safe(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, month=2, day=28)


def generate_random_birthday(
    min_age: int = 18,
    max_age: int = 65,
    *,
    today: date | None = None,
    randint: Callable[[int, int], int] = random.randint,
) -> str:
    if min_age < 0 or max_age < min_age:
        raise ValueError(f"invalid age range: min_age={min_age}, max_age={max_age}")
    base_day = today or date.today()
    oldest = _shift_year_safe(base_day, -max_age)
    youngest = _shift_year_safe(base_day, -min_age)
    span_days = max(0, (youngest - oldest).days)
    birthday = oldest + timedelta(days=randint(0, span_days))
    return birthday.isoformat()


def birthday_from_config(config: dict | None = None) -> str:
    cfg = config if isinstance(config, dict) else {}
    profile = cfg.get("profile") if isinstance(cfg.get("profile"), dict) else {}
    try:
        min_age = max(13, int(profile.get("min_age") if profile.get("min_age") is not None else 18))
    except (TypeError, ValueError):
        min_age = 18
    try:
        max_age = max(min_age, int(profile.get("max_age") if profile.get("max_age") is not None else 65))
    except (TypeError, ValueError):
        max_age = 65
    return generate_random_birthday(min_age=min_age, max_age=max_age)

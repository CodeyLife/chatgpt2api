from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def beijing_now_string() -> str:
    return beijing_now().strftime(DATETIME_FORMAT)


def beijing_from_timestamp_string(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=BEIJING_TZ).strftime(DATETIME_FORMAT)

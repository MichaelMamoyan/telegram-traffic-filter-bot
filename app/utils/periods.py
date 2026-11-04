from __future__ import annotations

import datetime as dt

PERIODS = ("today", "7d", "30d", "all")


def period_since(period: str, tz: dt.tzinfo | None = None) -> dt.datetime | None:
    now = dt.datetime.now(tz or dt.UTC)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "7d":
        return now - dt.timedelta(days=7)
    if period == "30d":
        return now - dt.timedelta(days=30)
    return None

from __future__ import annotations

import logging
import random
from collections.abc import Sequence

from app.db.models import Channel
from app.enums import Segment

log = logging.getLogger(__name__)


def eligible_channels(channels: Sequence[Channel], segment: Segment) -> list[Channel]:
    """Каналы, подходящие сегменту напрямую.

    Жёсткое правило ТЗ: если канал не допускает аудиторию <18 (allow_minors=False),
    он никогда не выдаётся детским сегментам — даже если его по ошибке привязали
    к male_minor/female_minor в админке.
    """
    result = []
    for channel in channels:
        if not channel.is_active:
            continue
        if segment.is_minor and not channel.allow_minors:
            continue
        if segment.value in (channel.segments or []):
            result.append(channel)
    return result


def _same_age_channels(channels: Sequence[Channel], segment: Segment) -> list[Channel]:
    """Каналы той же возрастной группы, но другого пола — первый уровень фолбэка."""
    group = (
        {Segment.MALE_MINOR.value, Segment.FEMALE_MINOR.value}
        if segment.is_minor
        else {Segment.MALE_ADULT.value, Segment.FEMALE_ADULT.value}
    )
    return [
        channel
        for channel in channels
        if channel.is_active
        and not (segment.is_minor and not channel.allow_minors)
        and group & set(channel.segments or [])
    ]


def _age_safe_channels(channels: Sequence[Channel], segment: Segment) -> list[Channel]:
    """Последний уровень фолбэка: любой активный канал, разрешённый этому возрасту."""
    return [
        channel
        for channel in channels
        if channel.is_active and not (segment.is_minor and not channel.allow_minors)
    ]


def weighted_choice(channels: Sequence[Channel], rng: random.Random | None = None) -> Channel:
    """Берём группу с наивысшим приоритетом (наименьшим priority) и крутим по весам."""
    rng = rng or random
    best_priority = min(channel.priority for channel in channels)
    pool = [channel for channel in channels if channel.priority == best_priority]
    weights = [max(channel.weight, 0.0) for channel in pool]
    if sum(weights) <= 0:
        return rng.choice(pool)
    return rng.choices(pool, weights=weights, k=1)[0]


def pick_channel(
    channels: Sequence[Channel], segment: Segment, rng: random.Random | None = None
) -> Channel | None:
    """Маршрутизация сегмент -> канал с тремя уровнями фолбэка."""
    for tier, finder in (
        ("exact", eligible_channels),
        ("same_age", _same_age_channels),
        ("age_safe", _age_safe_channels),
    ):
        candidates = finder(channels, segment)
        if candidates:
            channel = weighted_choice(candidates, rng)
            if tier != "exact":
                log.info(
                    "Сегмент %s: нет точного канала, фолбэк %s -> %s",
                    segment.value,
                    tier,
                    channel.title,
                )
            return channel
    log.warning("Сегмент %s: подходящих каналов нет", segment.value)
    return None

from __future__ import annotations

import random

from app.db.models import Channel
from app.enums import Segment
from app.services.routing import eligible_channels, pick_channel, weighted_choice


def make_channel(
    chat_id: int,
    segments: list[str],
    allow_minors: bool = False,
    is_active: bool = True,
    weight: float = 1.0,
    priority: int = 100,
) -> Channel:
    return Channel(
        id=chat_id,
        chat_id=chat_id,
        title=f"ch{chat_id}",
        segments=segments,
        allow_minors=allow_minors,
        is_active=is_active,
        weight=weight,
        priority=priority,
    )


def test_four_segments_route_to_their_channels() -> None:
    channels = [
        make_channel(1, ["male_adult"]),
        make_channel(2, ["female_adult"]),
        make_channel(3, ["male_minor", "female_minor"], allow_minors=True),
    ]
    assert pick_channel(channels, Segment.MALE_ADULT).chat_id == 1
    assert pick_channel(channels, Segment.FEMALE_ADULT).chat_id == 2
    assert pick_channel(channels, Segment.MALE_MINOR).chat_id == 3
    assert pick_channel(channels, Segment.FEMALE_MINOR).chat_id == 3


def test_minors_never_get_adult_only_channel() -> None:
    """Главное требование ТЗ: аудиторию <18 нельзя пускать в канал 18+."""
    channels = [make_channel(1, ["male_adult", "male_minor"], allow_minors=False)]
    assert eligible_channels(channels, Segment.MALE_MINOR) == []
    assert pick_channel(channels, Segment.MALE_MINOR) is None


def test_minor_falls_back_to_another_allowed_channel() -> None:
    """«Отсеивать и отдавать на другой канал»."""
    channels = [
        make_channel(1, ["male_adult"], allow_minors=False),
        make_channel(2, ["female_minor"], allow_minors=True, priority=50),
    ]
    picked = pick_channel(channels, Segment.MALE_MINOR)
    assert picked is not None and picked.chat_id == 2


def test_inactive_channel_is_skipped() -> None:
    channels = [
        make_channel(1, ["male_adult"], is_active=False),
        make_channel(2, ["male_adult"]),
    ]
    assert pick_channel(channels, Segment.MALE_ADULT).chat_id == 2


def test_no_channel_at_all() -> None:
    assert pick_channel([], Segment.FEMALE_ADULT) is None


def test_priority_wins_over_weight() -> None:
    channels = [
        make_channel(1, ["male_adult"], weight=100, priority=50),
        make_channel(2, ["male_adult"], weight=1, priority=10),
    ]
    assert pick_channel(channels, Segment.MALE_ADULT).chat_id == 2


def test_weighted_rotation_respects_weights() -> None:
    channels = [
        make_channel(1, ["male_adult"], weight=9),
        make_channel(2, ["male_adult"], weight=1),
    ]
    rng = random.Random(42)
    picks = [weighted_choice(channels, rng).chat_id for _ in range(1000)]
    assert 850 < picks.count(1) < 950


def test_zero_weights_do_not_crash() -> None:
    channels = [make_channel(1, ["male_adult"], weight=0)]
    assert weighted_choice(channels, random.Random(1)).chat_id == 1

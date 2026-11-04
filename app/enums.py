from __future__ import annotations

from enum import StrEnum


class AgeStatus(StrEnum):
    ADULT = "18+"
    MINOR = "<18"


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"


class Segment(StrEnum):
    """Четыре целевых сегмента из ТЗ (возраст x пол)."""

    MALE_ADULT = "male_adult"
    FEMALE_ADULT = "female_adult"
    MALE_MINOR = "male_minor"
    FEMALE_MINOR = "female_minor"

    @property
    def is_minor(self) -> bool:
        return self in (Segment.MALE_MINOR, Segment.FEMALE_MINOR)

    @property
    def gender(self) -> Gender:
        return Gender.MALE if self in (Segment.MALE_ADULT, Segment.MALE_MINOR) else Gender.FEMALE

    @property
    def age_status(self) -> AgeStatus:
        return AgeStatus.MINOR if self.is_minor else AgeStatus.ADULT


SEGMENT_TITLES: dict[Segment, str] = {
    Segment.MALE_ADULT: "Парни 18+",
    Segment.FEMALE_ADULT: "Девушки 18+",
    Segment.MALE_MINOR: "Парни до 18",
    Segment.FEMALE_MINOR: "Девушки до 18",
}


def resolve_segment(age: AgeStatus, gender: Gender) -> Segment:
    if age is AgeStatus.ADULT:
        return Segment.MALE_ADULT if gender is Gender.MALE else Segment.FEMALE_ADULT
    return Segment.MALE_MINOR if gender is Gender.MALE else Segment.FEMALE_MINOR


class OfferStatus(StrEnum):
    ISSUED = "issued"
    JOINED = "joined"
    LEFT = "left"


class EventType(StrEnum):
    START = "start"
    AGE_ANSWER = "age_answer"
    GENDER_ANSWER = "gender_answer"
    FILTER_PASSED = "filter_passed"
    FILTER_RESET = "filter_reset"
    OFFER_ISSUED = "offer_issued"
    NO_OFFER = "no_offer"
    JOIN_REQUEST = "join_request"
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"
    BLOCKED_BOT = "blocked_bot"


class BroadcastStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import texts
from app.keyboards.callbacks import AgeCB, GenderCB


def age_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_AGE_ADULT, callback_data=AgeCB(adult=True))
    builder.button(text=texts.BTN_AGE_MINOR, callback_data=AgeCB(adult=False))
    builder.adjust(2)
    return builder.as_markup()


def gender_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_GENDER_MALE, callback_data=GenderCB(male=True))
    builder.button(text=texts.BTN_GENDER_FEMALE, callback_data=GenderCB(male=False))
    builder.adjust(2)
    return builder.as_markup()


def offer_kb(url: str, button_text: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button_text or texts.OFFER_BUTTON_DEFAULT, url=url)]
        ]
    )

"""Строгая проверка HTML-разметки — как её делает Telegram Bot API."""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Набор тегов, который разрешает Telegram Bot API.
ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "span", "tg-spoiler", "tg-emoji", "a", "code", "pre", "blockquote",
}

HOSTILE = 'Канал <18 & "кавычки" <b>жирный</b>'


class _Validator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"недопустимый тег <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            self.problems.append(f"закрывающий </{tag}> без открывающего")
        elif self.stack[-1] != tag:
            self.problems.append(f"</{tag}> закрывает <{self.stack[-1]}>")
            self.stack.pop()
        else:
            self.stack.pop()


# Документация Bot API: «все символы <, > и &, не являющиеся частью тега или
# HTML-сущности, должны быть заменены на соответствующие сущности». Python-парсер
# к этому снисходителен (не считает «<18» тегом), Telegram — нет.
_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>")
_ENTITY = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]*|#[0-9]+|#[xX][0-9a-fA-F]+);")


def assert_telegram_html(text: str) -> None:
    problems: list[str] = []

    for match in re.finditer("<", text):
        if not _TAG.match(text, match.start()):
            problems.append(f"голый «<» на позиции {match.start()}")
    for match in re.finditer("&", text):
        if not _ENTITY.match(text, match.start()):
            problems.append(f"неэкранированный «&» на позиции {match.start()}")

    validator = _Validator()
    validator.feed(text)
    validator.close()
    problems += validator.problems

    assert not problems, f"{problems} в тексте:\n{text}"
    assert not validator.stack, f"незакрытые теги {validator.stack} в тексте:\n{text}"

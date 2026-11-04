from __future__ import annotations

import re

BUTTON_RE = re.compile(r"^\s*(?P<text>.+?)\s*[-—|]{1,2}\s*(?P<url>https?://\S+|t\.me/\S+)\s*$")


def parse_buttons(raw: str) -> list[dict[str, str]]:
    """Парсит кнопки рассылки: «Текст - https://url» по одной в строке."""
    buttons: list[dict[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        match = BUTTON_RE.match(line)
        if not match:
            raise ValueError(f"Не разобрал строку: {line.strip()!r}")
        url = match.group("url")
        if url.startswith("t.me/"):
            url = f"https://{url}"
        buttons.append({"text": match.group("text")[:64], "url": url})
    if not buttons:
        raise ValueError("Не нашёл ни одной кнопки")
    return buttons


def parse_start_payload(payload: str | None) -> str | None:
    """Нормализует start-payload.

    Поддерживаются и короткий код (`vasya_tiktok_01`), и формат из ТЗ
    (`ref_<buyer>_<link>`) — префикс `ref_` отбрасывается.
    """
    if not payload:
        return None
    cleaned = payload.strip()[:64]
    if not cleaned:
        return None
    if cleaned.lower().startswith("ref_") and len(cleaned) > 4:
        cleaned = cleaned[4:]
    return cleaned or None

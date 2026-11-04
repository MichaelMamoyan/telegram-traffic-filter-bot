from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**env: str) -> Settings:
    return Settings(bot_token="123:TEST", _env_file=None, **env)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1, [1]),
        ("1", [1]),
        ("1,2", [1, 2]),
        ("1, 2 ,3", [1, 2, 3]),
        ("1;2", [1, 2]),
        ([1, 2], [1, 2]),
        ("", []),
        (None, []),
    ],
)
def test_admin_ids_accepts_every_sane_format(raw, expected) -> None:
    assert _settings(admin_ids=raw).admin_ids == expected


def test_is_admin() -> None:
    settings = _settings(admin_ids="100,200")
    assert settings.is_admin(100) and settings.is_admin(200)
    assert not settings.is_admin(300)


def test_sheets_disabled_without_spreadsheet_id() -> None:
    assert _settings().sheets_enabled is False


def test_bot_token_is_required(monkeypatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_seed_file_is_not_a_path(raw) -> None:
    """Пустая переменная в .env не должна превращаться в Path('.')."""
    assert _settings(seed_channels_file=raw).seed_channels_file is None


def test_blank_credentials_path_falls_back_to_default() -> None:
    settings = _settings(google_credentials_file="")
    assert settings.google_credentials_file == Path("secrets/google.json")
    assert settings.sheets_enabled is False


def test_sheets_disabled_when_credentials_path_is_a_directory() -> None:
    settings = _settings(google_spreadsheet_id="abc123", google_credentials_file=".")
    assert settings.sheets_enabled is False


async def test_seed_channels_skips_directory(tmp_path) -> None:
    from app.db.init import seed_channels

    assert await seed_channels(None, Path(".")) == 0
    assert await seed_channels(None, tmp_path / "нет-такого.json") == 0

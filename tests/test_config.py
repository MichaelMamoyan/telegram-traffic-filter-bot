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


# --------------------------------------------------------------- чтение из окружения
# Предыдущая версия этих проверок передавала значение прямо в конструктор и
# миновала источники pydantic-settings. Из-за этого не заметили, что
# ADMIN_IDS=1,2 роняет запуск: список сначала пробуют разобрать как JSON,
# и SettingsError летит ДО валидаторов. С одним ID работало — это валидный JSON.


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("6336322208", [6336322208]),
        ("6336322208,8589144095", [6336322208, 8589144095]),
        ("1, 2 , 3", [1, 2, 3]),
        ("1;2", [1, 2]),
        ("[1, 2]", [1, 2]),
        ("", []),
    ],
)
def test_admin_ids_from_environment(monkeypatch, raw, expected) -> None:
    """Так конфиг приходит в Docker — через переменные окружения."""
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("ADMIN_IDS", raw)
    assert Settings(_env_file=None).admin_ids == expected  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("6336322208", [6336322208]),
        ("6336322208,8589144095", [6336322208, 8589144095]),
        ("1;2;3", [1, 2, 3]),
    ],
)
def test_admin_ids_from_dotenv_file(monkeypatch, tmp_path, raw, expected) -> None:
    """А так — при локальном запуске, из файла .env."""
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"BOT_TOKEN=123:TEST\nADMIN_IDS={raw}\n", encoding="utf-8")
    assert Settings(_env_file=env_file).admin_ids == expected  # type: ignore[call-arg]


def test_several_admins_all_pass_the_check(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("ADMIN_IDS", "6336322208,8589144095")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.is_admin(6336322208)
    assert settings.is_admin(8589144095)
    assert not settings.is_admin(999)


def test_broken_admin_ids_fail_loudly(monkeypatch) -> None:
    """Опечатка в .env должна падать на старте, а не тихо обнулять админов."""
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("ADMIN_IDS", "123,не-число")
    with pytest.raises((ValidationError, ValueError)):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_empty_redis_dsn_is_allowed() -> None:
    """Redis необязателен: он держит только состояние форм в админке."""
    settings = _settings(redis_dsn="")
    assert settings.redis_dsn == ""


def test_connection_pool_defaults_fit_small_plans() -> None:
    """На тарифе с 512 МБ десятки соединений съели бы всю память базы."""
    settings = _settings()
    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 10
    assert settings.db_pool_size + settings.db_max_overflow <= 20


def test_pool_size_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("DB_POOL_SIZE", "20")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "40")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (settings.db_pool_size, settings.db_max_overflow) == (20, 40)


def test_redis_is_not_required(monkeypatch) -> None:
    """Отсутствие REDIS_DSN — штатная конфигурация, а не ошибка.

    Некоторые PaaS не дают сохранить переменную с пустым значением, поэтому
    её просто не задают: умолчание обязано означать «Redis не нужен».
    """
    monkeypatch.setenv("BOT_TOKEN", "123:TEST")
    monkeypatch.delenv("REDIS_DSN", raising=False)
    assert Settings(_env_file=None).redis_dsn == ""  # type: ignore[call-arg]

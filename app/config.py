from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


def parse_id_list(value: object) -> list[int]:
    """Принимает `123`, `123,456`, `123;456`, `[123, 456]`."""
    if value is None or value == "":
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):  # допускаем и JSON-массив: ADMIN_IDS=[1, 2]
            raw = raw[1:-1]
        return [int(chunk.strip()) for chunk in raw.replace(";", ",").split(",") if chunk.strip()]
    return [int(item) for item in value]  # type: ignore[union-attr]


class _CsvMixin:
    """Читает списки как «1,2,3», не как JSON.

    pydantic-settings для полей-списков сначала пробует json.loads и падает с
    SettingsError ДО валидаторов. Поэтому `ADMIN_IDS=1` работал (валидный JSON),
    а `ADMIN_IDS=1,2` ронял запуск.
    """

    CSV_FIELDS: ClassVar[set[str]] = {"admin_ids"}

    def prepare_field_value(self, field_name, field, value, value_is_complex):  # type: ignore[no-untyped-def]
        if field_name in self.CSV_FIELDS and isinstance(value, str):
            return parse_id_list(value)
        return super().prepare_field_value(field_name, field, value, value_is_complex)  # type: ignore[misc]


class _CsvEnvSource(_CsvMixin, EnvSettingsSource):
    """Переменные окружения (так приходит конфиг в Docker)."""


class _CsvDotEnvSource(_CsvMixin, DotEnvSettingsSource):
    """Файл .env (так приходит конфиг при локальном запуске)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Telegram ---
    bot_token: str
    admin_ids: list[int] = Field(default_factory=list)

    # --- Инфраструктура ---
    db_dsn: str = "postgresql+asyncpg://bot:bot@localhost:5432/filterbot"
    redis_dsn: str = "redis://localhost:6379/0"

    # --- Логика воронки ---
    # Выдавать каждому юзеру персональную инвайт-ссылку (точнее атрибуция, но +1 API-запрос
    # на юзера). При false на канал создаётся одна join-request ссылка и кэшируется в БД:
    # атрибуция всё равно точная, т.к. chat_join_request приносит from_user.
    per_user_invite_links: bool = False
    # Автоматически одобрять заявки на вступление (п.3 ТЗ).
    auto_approve_join_requests: bool = True
    # Регистрировать неизвестный start-payload как новую реф-ссылку.
    # По умолчанию выключено: иначе любой переход по выдуманному коду плодит
    # строку в отчёте, а опечатка в креативе размазывает трафик байера по двум
    # «ссылкам». При выключенном флаге код всё равно сохраняется в users.ref_payload
    # и виден в выгрузке, а сам юзер попадает в строку «Без ссылки (органика)».
    auto_create_unknown_links: bool = False
    # Задержка перед одобрением заявки, сек (0 = сразу). Иногда нужна,
    # чтобы юзер успел увидеть экран «заявка отправлена».
    approve_delay: float = 0.0

    # --- Рассылки ---
    broadcast_rate: float = 20.0  # сообщений в секунду (лимит Telegram ~30)
    broadcast_batch: int = 500  # размер страницы выборки получателей

    # --- Google Sheets ---
    google_spreadsheet_id: str = ""
    google_credentials_file: Path = Path("secrets/google.json")
    sheets_sync_interval: int = 30
    sheets_sync_batch: int = 500

    # --- Прочее ---
    seed_channels_file: Path | None = None
    log_level: str = "INFO"
    tz_name: str = "Europe/Moscow"
    support_url: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Параметры копируем с исходных источников, иначе переданный в
        # конструктор _env_file игнорируется и настройки всегда читаются
        # из реального .env — что ломает изоляцию в тестах.
        return (
            init_settings,
            _CsvEnvSource(
                settings_cls,
                case_sensitive=env_settings.case_sensitive,  # type: ignore[attr-defined]
                env_prefix=env_settings.env_prefix,  # type: ignore[attr-defined]
                env_nested_delimiter=env_settings.env_nested_delimiter,  # type: ignore[attr-defined]
            ),
            _CsvDotEnvSource(
                settings_cls,
                env_file=dotenv_settings.env_file,  # type: ignore[attr-defined]
                env_file_encoding=dotenv_settings.env_file_encoding,  # type: ignore[attr-defined]
                case_sensitive=dotenv_settings.case_sensitive,  # type: ignore[attr-defined]
                env_prefix=dotenv_settings.env_prefix,  # type: ignore[attr-defined]
                env_nested_delimiter=dotenv_settings.env_nested_delimiter,  # type: ignore[attr-defined]
            ),
            file_secret_settings,
        )

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        return parse_id_list(value)

    @field_validator("seed_channels_file", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """`SEED_CHANNELS_FILE=` без значения — это «не задано», а не Path('.')."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("google_credentials_file", mode="before")
    @classmethod
    def _blank_to_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return Path("secrets/google.json")
        return value

    @property
    def sheets_enabled(self) -> bool:
        # is_file(), а не exists(): пустая переменная превращается в каталог.
        return bool(self.google_spreadsheet_id) and self.google_credentials_file.is_file()

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

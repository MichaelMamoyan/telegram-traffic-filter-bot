from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        """Принимаем и `123`, и `123,456`, и `[123, 456]`.

        pydantic-settings сначала пробует json.loads, поэтому единственный ID
        из .env приходит сюда уже целым числом, а не строкой.
        """
        if value is None or value == "":
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [
                int(chunk.strip())
                for chunk in value.replace(";", ",").split(",")
                if chunk.strip()
            ]
        return value

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

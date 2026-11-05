"""Синхронизация с Google Sheets: раскладка по листам без обращения к сети."""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.services.rows import (
    SUMMARY_HEADERS,
    TAB_FEMALE_ADULT,
    TAB_MALE_ADULT,
    TAB_MINORS,
    TAB_NO_FILTER,
    TAB_SUMMARY,
    USER_HEADERS,
    USER_TABS,
)
from app.services.sheets import SheetsSyncer


class FakeWorksheet:
    def __init__(self, title: str) -> None:
        self.title = title
        self.updates: list[list] = []
        self.frozen_rows = 0
        self.row_count = 500

    def batch_update(self, data, **kwargs):  # type: ignore[no-untyped-def]
        self.updates.append(data)

    def freeze(self, rows: int = 0, cols: int = 0) -> None:
        self.frozen_rows = rows


class FakeSpreadsheet:
    def __init__(self, titles: list[str]) -> None:
        self.sheets = [FakeWorksheet(title) for title in titles]
        self.added: list[str] = []

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self.sheets)

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        worksheet = FakeWorksheet(title)
        self.sheets.append(worksheet)
        self.added.append(title)
        return worksheet


def _syncer(spreadsheet: FakeSpreadsheet) -> SheetsSyncer:
    settings = Settings(bot_token="42:TEST", _env_file=None)
    syncer = SheetsSyncer(settings, sessionmaker=None)  # type: ignore[arg-type]
    syncer._spreadsheet = spreadsheet
    return syncer


def test_all_tabs_are_created_on_first_sync() -> None:
    """Пустая таблица должна сразу показать всю обещанную структуру."""
    spreadsheet = FakeSpreadsheet(["Sheet1"])
    _syncer(spreadsheet)._ensure_all_tabs_sync()

    assert spreadsheet.added == [
        TAB_SUMMARY, TAB_MALE_ADULT, TAB_FEMALE_ADULT, TAB_MINORS, TAB_NO_FILTER
    ]
    titles = [w.title for w in spreadsheet.worksheets()]
    assert titles[0] == "Sheet1"  # чужие листы не трогаем
    assert set(USER_TABS) | {TAB_SUMMARY} <= set(titles)


def test_headers_match_the_tab_kind() -> None:
    spreadsheet = FakeSpreadsheet([])
    _syncer(spreadsheet)._ensure_all_tabs_sync()
    by_title = {w.title: w for w in spreadsheet.worksheets()}

    assert by_title[TAB_SUMMARY].updates[0][0]["values"] == [SUMMARY_HEADERS]
    for tab in USER_TABS:
        assert by_title[tab].updates[0][0]["values"] == [USER_HEADERS]
        assert by_title[tab].frozen_rows == 1, "шапка должна быть закреплена"


def test_existing_tabs_are_not_recreated() -> None:
    spreadsheet = FakeSpreadsheet([TAB_SUMMARY, TAB_MALE_ADULT])
    _syncer(spreadsheet)._ensure_all_tabs_sync()
    assert spreadsheet.added == [TAB_FEMALE_ADULT, TAB_MINORS, TAB_NO_FILTER]


def test_tabs_are_ensured_only_once_per_run() -> None:
    spreadsheet = FakeSpreadsheet([])
    syncer = _syncer(spreadsheet)

    asyncio.run(syncer._ensure_tabs())
    created = list(spreadsheet.added)
    asyncio.run(syncer._ensure_tabs())

    assert spreadsheet.added == created, "повторные вызовы не должны ходить в API"


def test_disabled_syncer_does_nothing() -> None:
    settings = Settings(bot_token="42:TEST", _env_file=None)
    syncer = SheetsSyncer(settings, sessionmaker=None)  # type: ignore[arg-type]
    assert syncer.enabled is False
    assert asyncio.run(syncer.sync_once()) == 0
    assert asyncio.run(syncer.full_resync()) == 0


# --------------------------------------------------------------- ключ Google


def test_credentials_from_env_var(monkeypatch, tmp_path) -> None:
    """На PaaS файла с ключом нет — он приходит переменной окружения."""
    captured = {}

    class FakeCreds:
        @staticmethod
        def from_service_account_info(info, scopes):  # type: ignore[no-untyped-def]
            captured["info"] = info
            captured["scopes"] = scopes
            return "creds-from-env"

        @staticmethod
        def from_service_account_file(path, scopes):  # type: ignore[no-untyped-def]
            raise AssertionError("не должен читать файл, когда задана переменная")

    import google.oauth2.service_account as sa

    monkeypatch.setattr(sa, "Credentials", FakeCreds)

    settings = Settings(
        bot_token="42:TEST",
        google_spreadsheet_id="abc",
        google_credentials_json='{"type": "service_account", "project_id": "p"}',
        _env_file=None,
    )
    syncer = SheetsSyncer(settings, sessionmaker=None)  # type: ignore[arg-type]
    assert syncer._credentials() == "creds-from-env"
    assert captured["info"]["project_id"] == "p"
    assert captured["scopes"] == ["https://www.googleapis.com/auth/spreadsheets"]


def test_credentials_fall_back_to_file(monkeypatch, tmp_path) -> None:
    key_file = tmp_path / "google.json"
    key_file.write_text('{"type": "service_account"}', encoding="utf-8")

    class FakeCreds:
        @staticmethod
        def from_service_account_file(path, scopes):  # type: ignore[no-untyped-def]
            return f"creds-from-{path}"

    import google.oauth2.service_account as sa

    monkeypatch.setattr(sa, "Credentials", FakeCreds)

    settings = Settings(
        bot_token="42:TEST",
        google_spreadsheet_id="abc",
        google_credentials_file=key_file,
        _env_file=None,
    )
    syncer = SheetsSyncer(settings, sessionmaker=None)  # type: ignore[arg-type]
    assert syncer._credentials() == f"creds-from-{key_file}"


def test_sheets_enabled_with_env_var_only(tmp_path) -> None:
    """Файла нет, но ключ в переменной — выгрузка должна работать."""
    settings = Settings(
        bot_token="42:TEST",
        google_spreadsheet_id="abc",
        google_credentials_json='{"type": "service_account"}',
        google_credentials_file=tmp_path / "нет-файла.json",
        _env_file=None,
    )
    assert settings.has_google_credentials is True
    assert settings.sheets_enabled is True


def test_sheets_disabled_without_any_credentials(tmp_path) -> None:
    settings = Settings(
        bot_token="42:TEST",
        google_spreadsheet_id="abc",
        google_credentials_file=tmp_path / "нет-файла.json",
        _env_file=None,
    )
    assert settings.sheets_enabled is False


def test_blank_env_var_is_not_credentials(tmp_path) -> None:
    settings = Settings(
        bot_token="42:TEST",
        google_spreadsheet_id="abc",
        google_credentials_json="   ",
        google_credentials_file=tmp_path / "нет-файла.json",
        _env_file=None,
    )
    assert settings.sheets_enabled is False

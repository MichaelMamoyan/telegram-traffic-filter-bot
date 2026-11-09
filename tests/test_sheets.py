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
    TAB_SHORT,
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
        self.cleared = False

    def batch_update(self, data, **kwargs):  # type: ignore[no-untyped-def]
        self.updates.append(data)

    def freeze(self, rows: int = 0, cols: int = 0) -> None:
        self.frozen_rows = rows

    def clear(self) -> None:
        self.cleared = True


class FakeSpreadsheet:
    def __init__(self, titles: list[str]) -> None:
        self.sheets = [FakeWorksheet(title) for title in titles]
        self.added: list[str] = []

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self.sheets)

    def worksheet(self, title: str) -> FakeWorksheet:
        import gspread

        for sheet in self.sheets:
            if sheet.title == title:
                return sheet
        raise gspread.WorksheetNotFound(title)

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
        TAB_SUMMARY, TAB_SHORT, TAB_MALE_ADULT, TAB_FEMALE_ADULT, TAB_MINORS, TAB_NO_FILTER
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
    assert spreadsheet.added == [TAB_SHORT, TAB_FEMALE_ADULT, TAB_MINORS, TAB_NO_FILTER]


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


# --------------------------------------------------------------- узкий лист


def _funnel(*triples):  # type: ignore[no-untyped-def]
    """Каждый элемент: (название, зашло, подписалось). Код берётся из названия."""
    from app.db.repo import FunnelRow, slugify_code

    return [
        FunnelRow(link_id=i, code=slugify_code(title), title=title, buyer_name=None,
                  starts=starts, passed=starts, converted=conv)
        for i, (title, starts, conv) in enumerate(triples, 1)
    ]


def test_short_rows_include_links_without_traffic() -> None:
    """Пак раздаётся целиком, поэтому в листе должны быть все его ссылки."""
    from app.services.rows import short_rows

    rows = short_rows(_funnel(("Kiril 1", 50, 20), ("Kiril 2", 0, 0), ("Kiril 3", 7, 1)))
    assert rows == [["Kiril 1", "20"], ["Kiril 2", "0"], ["Kiril 3", "1"]]


def test_short_rows_fall_back_to_code_without_title() -> None:
    from app.db.repo import FunnelRow
    from app.services.rows import short_rows

    row = FunnelRow(link_id=1, code="kiril_1", title="", buyer_name=None,
                    starts=3, passed=3, converted=1)
    assert short_rows([row]) == [["kiril_1", "1"]]


def test_short_tab_is_created_with_its_own_headers() -> None:
    from app.services.rows import SHORT_HEADERS, TAB_SHORT

    spreadsheet = FakeSpreadsheet([])
    _syncer(spreadsheet)._ensure_all_tabs_sync()

    assert TAB_SHORT in spreadsheet.added
    by_title = {w.title: w for w in spreadsheet.worksheets()}
    assert by_title[TAB_SHORT].updates[0][0]["values"] == [SHORT_HEADERS]
    assert by_title[TAB_SHORT].frozen_rows == 1


def test_short_sheet_is_rewritten_whole() -> None:
    """Лист узкий, поэтому переписывается целиком — без возни с номерами строк."""
    from app.services.rows import SHORT_HEADERS, TAB_SHORT

    spreadsheet = FakeSpreadsheet([])
    syncer = _syncer(spreadsheet)
    syncer._ensure_all_tabs_sync()
    syncer._write_sync({}, [], [["Kiril 1", "20"]])

    sheet = next(w for w in spreadsheet.worksheets() if w.title == TAB_SHORT)
    assert sheet.updates[-1][0]["values"] == [SHORT_HEADERS, ["Kiril 1", "20"]]


def test_short_rows_keep_links_with_traffic_but_no_subscriptions() -> None:
    """Связка льёт, а подписок нет — это и есть повод её отключить."""
    from app.services.rows import short_rows

    assert short_rows(_funnel(("Слитая", 300, 0))) == [["Слитая", "0"]]


def test_large_pack_is_shown_whole() -> None:
    """Пак из 150 ссылок: раньше в лист попадали только отработавшие."""
    from app.services.rows import short_rows

    funnel = _funnel(*[(f"ММ {i}", 1 if i == 150 else 0, 0) for i in range(1, 151)])
    rows = short_rows(funnel)
    assert len(rows) == 150
    assert [r[0] for r in rows[:3]] == ["ММ 1", "ММ 2", "ММ 3"]
    assert rows[-1] == ["ММ 150", "0"]


# --------------------------------------------------------------- группировка


def test_pack_key_splits_code() -> None:
    from app.services.rows import pack_key

    assert pack_key("kiril_7") == ("kiril", 7)
    assert pack_key("mm_12") == ("mm", 12)
    assert pack_key("bogdan") == ("bogdan", 0)
    assert pack_key("vasya_tiktok_01") == ("vasya_tiktok", 1)


def test_links_of_one_pack_go_together_and_in_order() -> None:
    """Раньше ссылки пака были разбросаны по таблице: сортировка шла по трафику."""
    from app.services.rows import short_rows

    rows = short_rows(_funnel(
        ("ММ 3", 10, 1), ("Петя 1", 80, 40), ("ММ 1", 50, 30),
        ("Петя 2", 5, 2), ("ММ 2", 7, 4),
    ))
    assert [r[0] for r in rows] == ["Петя 1", "Петя 2", "ММ 1", "ММ 2", "ММ 3"]


def test_packs_ordered_by_total_subscriptions() -> None:
    from app.services.rows import short_rows

    rows = short_rows(_funnel(
        ("Слабый 1", 100, 1), ("Сильный 1", 10, 5), ("Сильный 2", 10, 6),
    ))
    assert [r[0] for r in rows] == ["Сильный 1", "Сильный 2", "Слабый 1"]


def test_single_links_mix_with_packs_by_the_same_rule() -> None:
    from app.services.rows import short_rows

    rows = short_rows(_funnel(("bogdan", 20, 9), ("ММ 1", 5, 2), ("ММ 2", 5, 3)))
    assert [r[0] for r in rows] == ["bogdan", "ММ 1", "ММ 2"]


def test_grouping_keeps_every_link_with_traffic() -> None:
    from app.services.rows import short_rows

    funnel = _funnel(*[(f"ММ {i}", i, i % 3) for i in range(1, 31)])
    rows = short_rows(funnel)
    assert len(rows) == 30
    assert [r[0] for r in rows] == [f"ММ {i}" for i in range(1, 31)]

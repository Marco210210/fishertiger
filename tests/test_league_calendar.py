import json
from io import BytesIO

import pandas as pd
import pytest
from openpyxl import Workbook

from advisor import league_calendar


def legacy_frame() -> pd.DataFrame:
    rows = [
        ["1ª Giornata lega", None, "3ª Giornata serie a", None, None, None, "2ª Giornata lega", None, "4ª Giornata serie a", None],
        ["Alpha", 0, 0, "Beta", None, None, "Alpha", 0, 0, "Gamma", None],
        ["Gamma", 0, 0, "Delta", None, None, "Beta", 0, 0, "Delta", None],
        ["3ª Giornata lega", None, "5ª Giornata serie a", None, None, None, None, None, None, None],
        ["Alpha", 0, 0, "Delta", None, None, None, None, None, None, None],
        ["Beta", 0, 0, "Gamma", None, None, None, None, None, None, None],
    ]
    return pd.DataFrame(rows)


def test_parser_infers_participants_and_fixture_counts_from_legacy_blocks():
    calendar = league_calendar.parse_legacy_two_block_frame(legacy_frame(), "friends")

    assert calendar["participants_count"] == 4
    assert calendar["teams"] == ["Alpha", "Beta", "Delta", "Gamma"]
    assert [day["number"] for day in calendar["matchdays"]] == [1, 2, 3]
    assert [len(day["fixtures"]) for day in calendar["matchdays"]] == [2, 2, 2]
    assert calendar["matchdays"][1]["fixtures"] == [{"home": "Alpha", "away": "Gamma"}, {"home": "Beta", "away": "Delta"}]


def test_parser_rejects_team_playing_twice_in_a_matchday():
    frame = legacy_frame()
    frame.iat[2, 0] = "Alpha"

    with pytest.raises(ValueError, match="more than once"):
        league_calendar.parse_legacy_two_block_frame(frame, "friends")


def test_cli_uses_reader_and_writes_canonical_json(monkeypatch, tmp_path):
    calls = []

    def fake_read_excel(source, sheet_name, header):
        calls.append((source, sheet_name, header))
        return legacy_frame()

    monkeypatch.setattr(league_calendar.pd, "read_excel", fake_read_excel)
    destination = tmp_path / "calendar.json"

    assert league_calendar.main(["calendar.xlsx", str(destination), "--league-id", "friends"]) == 0
    assert calls == [(league_calendar.Path("calendar.xlsx"), "Calendario", None)]
    assert json.loads(destination.read_text(encoding="utf-8"))["participants_count"] == 4


def test_generated_template_is_accepted_by_the_legacy_parser():
    frame = pd.read_excel(BytesIO(league_calendar.build_legacy_calendar_template()), sheet_name="Calendario", header=None)

    calendar = league_calendar.parse_legacy_two_block_frame(frame, "example")

    assert calendar["teams"] == [f"Squadra {number}" for number in range(1, 9)]
    assert [day["number"] for day in calendar["matchdays"]] == list(range(1, 37))
    assert all(len(day["fixtures"]) == 4 for day in calendar["matchdays"])


def test_reader_reports_missing_calendario_sheet_clearly(tmp_path):
    workbook = Workbook()
    workbook.active.title = "Altro"
    source = tmp_path / "calendar.xlsx"
    workbook.save(source)

    with pytest.raises(ValueError, match="worksheet 'Calendario' is required"):
        league_calendar.preprocess_legacy_calendar(source, "example")

import io
import json
from pathlib import Path

import pandas as pd
import pytest

import advisor.player_list_updates as player_list_updates
from advisor.league_profile import LeagueProfile
from advisor.pipeline import LISTONE_COLUMNS
from advisor.player_list_updates import (
    PlayerListUpdateError,
    candidate_status,
    official_download_url,
    parse_public_players,
    provider_season_id,
    public_check,
    reconcile_departed_starters,
    season_slug,
    store_candidate,
)


def workbook(players, ceduti=(), season="2026 27"):
    defaults = {column: 0 for column in LISTONE_COLUMNS}
    rows = []
    for player in players:
        row = {**defaults, "RM": "Por", "Nome": "Player", "Squadra": "AAA", **player}
        rows.append(row)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([[f"Quotazioni Fantacalcio Stagione {season}"]]).to_excel(writer, sheet_name="Tutti", index=False, header=False)
        pd.DataFrame(rows, columns=sorted(LISTONE_COLUMNS)).to_excel(writer, sheet_name="Tutti", index=False, startrow=1)
        pd.DataFrame([[f"Quotazioni Fantacalcio Stagione {season}"]]).to_excel(writer, sheet_name="Ceduti", index=False, header=False)
        departed_rows = [item if isinstance(item, dict) else {"Id": item} for item in ceduti]
        pd.DataFrame(departed_rows, columns=sorted(LISTONE_COLUMNS)).to_excel(writer, sheet_name="Ceduti", index=False, startrow=1)
    return output.getvalue()


def profile_for(path: Path):
    value = json.loads((Path(__file__).parents[1] / "config/default_profile.json").read_text(encoding="utf-8"))
    value["profile_id"] = "updates"
    next(source for source in value["current_sources"] if source["name"] == "player_list")["path"] = str(path)
    starters = path.with_name("titolari.csv")
    if not starters.exists():
        pd.DataFrame([{
            "squadra": "AAA", "nome": "Player", "id_fantacalcio": 1,
            "status": "TITOLARE", "note": "",
        }]).to_csv(starters, index=False)
    next(source for source in value["current_sources"] if source["name"] == "starters")["path"] = str(starters)
    return LeagueProfile.from_dict(value)


def public_html(players):
    rows = "".join(
        f'<tr class="player-row"><td class="role">{item["R"]}</td><td class="name"><a href="/calciatori/{item["Id"]}/player">{item["Nome"]}</a></td><td class="team">{item["Squadra"]}</td><td class="quotation">{item["Qt.A"]}</td><td class="fvm">{item["FVM"]}</td></tr>'
        for item in players
    )
    return f"<html><h1>Quotazioni Fantacalcio 2026/27</h1><table>{rows}</table></html>"


def test_season_mapping_and_urls():
    assert provider_season_id("2026/27") == 21
    assert provider_season_id("2026/2027") == 21
    assert season_slug("2026/2027") == "2026-27"
    assert official_download_url("2026/27") == "https://www.fantacalcio.it/api/v1/Excel/prices/21/1"
    with pytest.raises(PlayerListUpdateError, match="consecutive"):
        provider_season_id("2026/28")


def test_html_parsing_and_public_id_diff(tmp_path):
    active = tmp_path / "active.xlsx"
    active.write_bytes(workbook([
        {"Id": 1, "R": "P", "Nome": "Old Name", "Squadra": "AAA", "Qt.A": 10, "FVM": 20},
        {"Id": 2, "R": "D", "Nome": "Departed", "Squadra": "BBB", "Qt.A": 5, "FVM": 8},
    ], ceduti=[2]))
    html = public_html([
        {"Id": 1, "R": "P", "Nome": "New Name", "Squadra": "AAA", "Qt.A": 11, "FVM": 20},
        {"Id": 2, "R": "D", "Nome": "Departed *", "Squadra": "BBB", "Qt.A": 5, "FVM": 8},
        {"Id": 3, "R": "A", "Nome": "Added", "Squadra": "CCC", "Qt.A": 4, "FVM": 9},
    ])
    parsed = parse_public_players(html, "2026/27")
    assert [item["Id"] for item in parsed] == [1, 3]
    result = public_check(profile_for(active), lambda url: html)
    assert result["state"] == "changed"
    assert result["summary"] == {"public_players": 2, "active_players": 1, "added": 1, "removed": 0, "changed": 1}
    assert result["download_url"].endswith("/21/1")
    assert len(result["content_hash"]) == 64

    mismatched = html.replace("<h1>Quotazioni Fantacalcio 2026/27</h1>", '<select id="season"><option>2026/27</option><option selected>2025/26</option></select>')
    with pytest.raises(PlayerListUpdateError, match="requested season"):
        parse_public_players(mismatched, "2026/27")


def test_invalid_upload_preserves_prior_candidate_and_status_diff(tmp_path):
    active = tmp_path / "active.xlsx"
    active.write_bytes(workbook([{"Id": 1, "R": "P", "Nome": "One", "Squadra": "AAA", "Qt.A": 10, "FVM": 20}]))
    valid = workbook([
        {"Id": 1, "R": "P", "Nome": "One", "Squadra": "AAA", "Qt.A": 12, "FVM": 21},
        {"Id": 2, "R": "A", "Nome": "Two", "Squadra": "BBB", "Qt.A": 8, "FVM": 15},
    ], ceduti=[1])
    first = store_candidate(tmp_path / "updates", "updates", "2026/27", valid, "list.xlsx")
    with pytest.raises(PlayerListUpdateError):
        store_candidate(tmp_path / "updates", "updates", "2026/27", b"not xlsx", "bad.xlsx")
    status = candidate_status(tmp_path / "updates", profile_for(active))
    assert status["candidate_hash"] == first["candidate_hash"]
    assert status["state"] == "candidate_ready"
    assert status["summary"]["added"] == 1
    assert status["summary"]["quotation"] == 1
    assert status["summary"]["fvm"] == 1
    assert status["summary"]["ceduti_added"] == 1
    assert status["profile_hash"] == profile_for(active).configuration_hash
    assert len(status["active_hash"]) == 64


def test_wrong_season_candidate_is_rejected(tmp_path):
    payload = workbook([{"Id": 1, "R": "P"}], season="2025 26")
    with pytest.raises(PlayerListUpdateError, match="season 2026-27"):
        store_candidate(tmp_path / "updates", "updates", "2026/27", payload, "wrong.xlsx")


def test_departed_starters_are_reconciled_only_by_id_or_unique_exact_identity(tmp_path):
    starters = tmp_path / "titolari.csv"
    pd.DataFrame([
        {"squadra": "Old Team", "nome": "By ID", "id_fantacalcio": "9001", "status": "RISERVA", "note": ""},
        {"squadra": "Bologna", "nome": "By Name", "id_fantacalcio": "", "status": "RISERVA", "note": ""},
        {"squadra": "Roma", "nome": "Unverified", "id_fantacalcio": "", "status": "RISERVA", "note": ""},
    ]).to_csv(starters, index=False)
    candidate = tmp_path / "candidate.xlsx"
    candidate.write_bytes(workbook(
        [{"Id": 1, "R": "P"}],
        ceduti=[
            {"Id": 9001, "Nome": "Different Name", "Squadra": "Different Team"},
            {"Id": 9002, "Nome": "By Name", "Squadra": "Bologna"},
        ],
    ))

    cleaned, removed = reconcile_departed_starters(starters, candidate)

    assert cleaned["nome"].tolist() == ["Unverified"]
    assert [(item["id"], item["match_method"]) for item in removed] == [
        (9001, "authoritative_id"), (9002, "exact_identity"),
    ]


def test_failed_metadata_swap_preserves_previous_candidate(tmp_path, monkeypatch):
    active = tmp_path / "active.xlsx"
    active.write_bytes(workbook([{"Id": 1, "R": "P", "Nome": "One"}]))
    root = tmp_path / "updates"
    first = store_candidate(root, "updates", "2026/27", workbook([{"Id": 1, "R": "P", "Nome": "First"}]), "first.xlsx")

    def fail_write(path, value):
        raise OSError("metadata write failed")

    monkeypatch.setattr(player_list_updates, "_write_json", fail_write)
    with pytest.raises(OSError, match="metadata write failed"):
        store_candidate(root, "updates", "2026/27", workbook([{"Id": 2, "R": "A", "Nome": "Second"}]), "second.xlsx")
    status = candidate_status(root, profile_for(active))
    assert status["candidate_hash"] == first["candidate_hash"]

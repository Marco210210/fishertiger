import json

import pandas as pd
import pytest

from advisor.league_profile import SourceDeclaration
from advisor.pipeline import _resolve_source, active_auction_guide, load_identity_overrides, match_manual, weighted_history


def test_source_lookup_supports_raw_relative_and_project_relative_paths(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    source_file = raw / "source.csv"
    source_file.write_text("x\n", encoding="utf-8")

    assert _resolve_source(SourceDeclaration("teams", "source.csv", "csv"), raw) == source_file
    with pytest.raises(FileNotFoundError, match="Missing required source 'teams'"):
        _resolve_source(SourceDeclaration("teams", "missing.csv", "csv"), raw)


def test_identity_overrides_are_applied_before_matching_and_validated(tmp_path):
    archive = tmp_path / "overrides.json"
    archive.write_text(json.dumps({"overrides": [{"source": "titolari", "name": "Different", "team": "Club", "id_fantacalcio": 7, "confirmed": True}]}), encoding="utf-8")
    listone = pd.DataFrame([{"Id": 7, "Nome": "Canonical", "Squadra": "Club"}])
    manual = pd.DataFrame([{"nome": "Different", "squadra": "Club"}])

    result = match_manual(manual, listone, "titolari", load_identity_overrides(archive))

    assert result.iloc[0].id_matched == 7
    assert result.iloc[0].metodo == "override"
    archive.write_text(json.dumps({"overrides": [{"source": "titolari", "name": "Different", "team": "Club", "id_fantacalcio": 7}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="confirmed=true"):
        match_manual(manual, listone, "titolari", load_identity_overrides(archive))


def test_unresolved_matches_are_explicitly_reported():
    result = match_manual(pd.DataFrame([{"nome": "Nobody", "squadra": "Club"}]), pd.DataFrame([{"Id": 1, "Nome": "Player", "Squadra": "Other"}]), "piazzati")

    assert result.iloc[0].metodo == "nessuno"
    assert result.iloc[0].diagnostic == "no confident candidate"


def test_ambiguous_matches_are_explicitly_reported():
    listone = pd.DataFrame([
        {"Id": 1, "Nome": "Rossi", "Squadra": "Club"},
        {"Id": 2, "Nome": "Rossi", "Squadra": "Club"},
    ])

    result = match_manual(pd.DataFrame([{"nome": "Rossi", "squadra": "Club"}]), listone, "piazzati")

    assert result.iloc[0].metodo == "ambiguo"
    assert result.iloc[0].diagnostic == "multiple equally scored candidates"


def test_all_history_seasons_are_used_in_chronological_effective_order():
    histories = [pd.DataFrame([{"Id": 1, "Mv": value, "Pv": 1}]) for value in (5.0, 6.0, 7.0, 8.0)]

    assert weighted_history(1, histories, "Mv", weights=(0.6, 0.3, 0.1)) == pytest.approx(8 / 1.1)


def test_stale_auction_guide_rows_do_not_block_a_new_official_listone():
    guide = pd.DataFrame([
        {"id_fantacalcio": 1, "fascia": "TOP"},
        {"id_fantacalcio": 218, "fascia": "JOLLY"},
    ])
    current = pd.DataFrame([{"Id": 1}])

    assert active_auction_guide(guide, current).id_fantacalcio.tolist() == [1]
    with pytest.raises(ValueError, match="unique"):
        active_auction_guide(pd.concat([guide.iloc[[0]], guide.iloc[[0]]]), current)


def test_injury_warnings_are_not_treated_as_auction_tiers():
    guide = pd.DataFrame([
        {"id_fantacalcio": 1, "fascia": "TOP"},
        {"id_fantacalcio": 2, "fascia": "INFORTUNATO"},
    ])
    current = pd.DataFrame([{"Id": 1}, {"Id": 2}])

    assert active_auction_guide(guide, current).id_fantacalcio.tolist() == [1]

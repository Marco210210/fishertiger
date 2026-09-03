import json
from types import SimpleNamespace

from advisor.config import LeagueConfig
from advisor import simulate


def payload():
    return {
        "meta": {"profile": {}},
        "calendario_lega": {
            "teams": ["Alpha", "Beta"],
            "matchdays": [{"number": 1, "serie_a_matchday": 1, "fixtures": [{"home": "Alpha", "away": "Beta"}]}],
        },
        "players": [{"id": 1, "ruolo": "P"}, {"id": 2, "ruolo": "D"}, {"id": 3, "ruolo": "P"}, {"id": 4, "ruolo": "D"}],
    }


def test_run_simulation_uses_supplied_rosters_and_records_auction_provenance(tmp_path, monkeypatch):
    (tmp_path / "auction_data.json").write_text(json.dumps(payload()), encoding="utf-8")
    rosters = {"Beta": [4, 3], "Alpha": [2, 1]}
    league = LeagueConfig(participants=2, slots=(("P", 1), ("D", 1)))
    monkeypatch.setattr(simulate, "make_sample_rosters", lambda *_: (_ for _ in ()).throw(AssertionError("sample rosters must not be used")))
    monkeypatch.setattr(simulate, "simulate_season", lambda *_args, **_kwargs: SimpleNamespace(iterations=5, teams={}, scenarios={}, diagnostics={"seed": 7}))

    output = simulate.run_simulation(tmp_path, iterations=5, seed=7, rosters=rosters, league=league)

    assert output["rosters"] == {"Alpha": [1, 2], "Beta": [3, 4]}
    assert output["meta"]["roster_mode"] == "auction"
    assert output["meta"]["roster_input_hash"]
    assert json.loads((tmp_path / "season_simulation.json").read_text(encoding="utf-8"))["meta"]["roster_mode"] == "auction"

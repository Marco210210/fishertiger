import pytest

from advisor.config import LeagueConfig
from advisor.simulation import make_sample_rosters


def _player(player_id: int, probability: float, *, inactive: bool = False) -> dict:
    return {
        "id": player_id,
        "ruolo": "P",
        "fvm_scaled": 100 - player_id,
        "disponibilita": {"confirmed_inactive": inactive},
        "p_gioca_per_giornata": [probability, probability],
        "voto_puro_mean_per_giornata": [6, 6],
        "bonus_atteso_per_giornata": [0, 0],
    }


def test_sample_rosters_use_reliability_and_exclude_confirmed_inactive() -> None:
    payload = {
        "players": [
            _player(1, 0.1),
            _player(2, 1),
            _player(3, 1),
            _player(4, 1, inactive=True),
        ],
        "calendario_lega": {
            "teams": ["A", "B"],
            "matchdays": [
                {
                    "number": 1,
                    "serie_a_matchday": 1,
                    "fixtures": [{"home": "A", "away": "B"}],
                }
            ],
        },
    }
    league = LeagueConfig(participants=2, slots=(("P", 1),))

    rosters = make_sample_rosters(payload, league)

    selected = {player_id for roster in rosters.values() for player_id in roster}
    assert selected == {2, 3}


@pytest.mark.parametrize("participants", [6, 8, 10, 12])
def test_sample_rosters_scale_deterministically_with_league_depth(participants: int) -> None:
    names = [f"T{index}" for index in range(participants)]
    payload = {
        "players": [_player(index + 1, 0.9) for index in range(participants + 3)],
        "calendario_lega": {
            "teams": names,
            "matchdays": [{
                "number": 1,
                "serie_a_matchday": 1,
                "fixtures": [],
            }],
        },
    }
    league = LeagueConfig(participants=participants, slots=(("P", 1),))

    first = make_sample_rosters(payload, league)
    second = make_sample_rosters(payload, league)

    assert first == second
    assert all(len(roster) == 1 for roster in first.values())
    assert len({player for roster in first.values() for player in roster}) == participants


def test_sample_rosters_pair_explicit_goalkeeper_deputies() -> None:
    players = [
        {**_player(1, 0.8), "squadra": "Roma", "gerarchia_portiere": "PRIMO"},
        {**_player(2, 0.2), "squadra": "Roma", "gerarchia_portiere": "SECONDO"},
        {**_player(3, 0.8), "squadra": "Milan", "gerarchia_portiere": "PRIMO"},
        {**_player(4, 0.2), "squadra": "Milan", "gerarchia_portiere": "SECONDO"},
    ]
    payload = {
        "players": players,
        "calendario_lega": {
            "teams": ["A", "B"],
            "matchdays": [{
                "number": 1,
                "serie_a_matchday": 1,
                "fixtures": [{"home": "A", "away": "B"}],
            }],
        },
    }
    league = LeagueConfig(participants=2, slots=(("P", 2),))

    rosters = make_sample_rosters(payload, league)
    clubs = {player["id"]: player["squadra"] for player in players}

    assert all(len({clubs[player_id] for player_id in roster}) == 1 for roster in rosters.values())


def test_sample_rosters_reject_duplicate_ids() -> None:
    payload = {
        "players": [_player(1, 1), _player(1, 1)],
        "calendario_lega": {
            "teams": ["A", "B"],
            "matchdays": [{"number": 1, "serie_a_matchday": 1, "fixtures": []}],
        },
    }

    with pytest.raises(ValueError, match="IDs must be unique"):
        make_sample_rosters(payload, LeagueConfig(participants=2, slots=(("P", 1),)))

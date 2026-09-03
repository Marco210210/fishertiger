import pytest

from advisor.config import LeagueConfig
from advisor.simulation import RosterValidationError, normalize_rosters


def payload():
    return {
        "calendario_lega": {
            "teams": ["Alpha", "Beta"],
            "matchdays": [{"number": 1, "serie_a_matchday": 1, "fixtures": [{"home": "Alpha", "away": "Beta"}]}],
        },
        "players": [
            {"id": 1, "ruolo": "P"}, {"id": 2, "ruolo": "D"},
            {"id": 3, "ruolo": "P"}, {"id": 4, "ruolo": "D"},
        ],
    }


def league():
    return LeagueConfig(participants=2, slots=(("P", 1), ("D", 1)))


def test_normalize_rosters_uses_calendar_order_and_sorted_player_ids():
    rosters = {"Beta": [4, 3], "Alpha": [2, 1]}

    assert normalize_rosters(payload(), rosters, league()) == {"Alpha": [1, 2], "Beta": [3, 4]}


@pytest.mark.parametrize(
    ("rosters", "message"),
    [
        ({"Alpha": [1, 2]}, "exactly the calendar teams"),
        ({"Alpha": [1, 2], "Beta": [3, 99]}, "unknown player ID 99"),
        ({"Alpha": [1, 2], "Beta": [3, True]}, "player IDs must be integers"),
        ({"Alpha": [1, 2], "Beta": [2, 3]}, "duplicate player"),
        ({"Alpha": [1, 2], "Beta": [3]}, "expected 2 players"),
        ({"Alpha": [1, 3], "Beta": [2, 4]}, "expected 1 P players"),
    ],
)
def test_normalize_rosters_rejects_invalid_real_auction_input(rosters, message):
    with pytest.raises(RosterValidationError, match=message):
        normalize_rosters(payload(), rosters, league())

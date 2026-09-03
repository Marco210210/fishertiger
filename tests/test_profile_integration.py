import json

from advisor import simulation
from advisor.config import LeagueConfig
from advisor.league_profile import LeagueProfile
from advisor.pipeline import league_rules_payload, load_canonical_league_calendar


def _players():
    roles = {1: "P", 2: "D", 3: "D", 4: "D", 5: "D", 6: "C", 7: "C", 8: "C", 9: "C", 10: "A", 11: "A", 12: "A"}
    return {player_id: {"id": player_id, "ruolo": role, "squadra": "Club", "p_gioca_per_giornata": [1.0], "voto_puro_mean_per_giornata": [10.0], "voto_puro_std_per_giornata": [0.0], "bonus_atteso_per_giornata": [0.0]} for player_id, role in roles.items()}


def test_profile_adapter_is_the_complete_payload_rule_source():
    source = json.loads((__import__("pathlib").Path(__file__).parents[1] / "config/default_profile.json").read_text())
    source["participants"]["team_names"] = ["A", "B"]
    source["participants"]["user_team"] = "A"
    source["payouts"]["prizes"] = [{"rank": 1, "amount_eur": 50}]
    source["roster_slots"] = {"P": 1, "D": 3, "C": 3, "A": 5}
    source["bench_switch"] = {"bench_roles": ["P"], "mode": "Basic", "max_substitutions": 1}
    source["scoring"].update(goal=4, penalty_missed=-3, penalty_saved=3, clean_sheet=1)
    source["virtual_goals"] = {"threshold": 70, "step": 4}
    source["incomplete_lineup"] = {"policy": "allow_partial", "score": 0}
    profile = LeagueProfile.from_dict(source)

    rules = league_rules_payload(LeagueConfig.from_profile(profile))

    assert rules["team_names"] == ("A", "B")
    assert rules["roster_slots"] == {"P": 1, "D": 3, "C": 3, "A": 5}
    assert rules["scoring_goal"] == 4
    assert rules["scoring_penalty_missed"] == -3
    assert rules["scoring_penalty_saved"] == 3
    assert rules["scoring_clean_sheet"] == 1
    assert rules["score_threshold"] == 70
    assert rules["incomplete_lineup_policy"] == "allow_partial"


def test_incomplete_lineup_policy_does_not_always_zero_scores(monkeypatch):
    def draw(player, day_index, rng, team_factor):
        if player["id"] in {5, 12}:
            return {"id": player["id"], "ruolo": player["ruolo"], "selection_value": 0.0, "plays": False}
        return {"id": player["id"], "ruolo": player["ruolo"], "selection_value": 10.0, "plays": True, "pure": 10.0, "fantavote": 10.0}

    monkeypatch.setattr(simulation, "_draw_outcome", draw)
    partial = LeagueConfig(defense_modifier_enabled=False, incomplete_lineup_policy="allow_partial")
    zero = LeagueConfig(defense_modifier_enabled=False, incomplete_lineup_policy="zero_score")

    assert simulation._team_score(list(range(1, 13)), _players(), 0, {}, None, partial)[0] == 100.0
    assert simulation._team_score(list(range(1, 13)), _players(), 0, {}, None, zero)[0] == 0


def test_custom_penalty_and_clean_sheet_scoring_is_applied(monkeypatch):
    def draw(player, day_index, rng, team_factor):
        is_goalkeeper = player["ruolo"] == "P"
        events = (0, 0, 0, 0, 0, 0, 1, int(is_goalkeeper), is_goalkeeper)
        return {
            "id": player["id"],
            "ruolo": player["ruolo"],
            "selection_value": 10.0,
            "plays": True,
            "pure": 10.0,
            "events": events,
            "fantavote": 10.0,
        }

    monkeypatch.setattr(simulation, "_draw_outcome", draw)
    league = LeagueConfig(
        defense_modifier_enabled=False,
        scoring_penalty_missed=-3,
        scoring_penalty_saved=3,
        scoring_clean_sheet=1,
    )

    score, _ = simulation._team_score(list(range(1, 13)), _players(), 0, {}, None, league)

    # Every player misses a penalty (-33), while only the goalkeeper saves one
    # and earns the clean-sheet bonus (+4): 110 - 33 + 4.
    assert score == 81.0


def test_canonical_calendar_json_is_loaded_without_legacy_workbook(tmp_path):
    calendar = {"schema_version": "1.0", "league_id": "two", "teams": ["A", "B"], "participants_count": 2, "matchdays": [{"number": 1, "serie_a_matchday": 3, "fixtures": [{"home": "A", "away": "B"}]}]}
    (tmp_path / "calendario_lega.json").write_text(json.dumps(calendar), encoding="utf-8")

    assert load_canonical_league_calendar(tmp_path) == calendar


def test_optional_profile_calendar_can_be_absent(tmp_path):
    source = json.loads((__import__("pathlib").Path(__file__).parents[1] / "config/default_profile.json").read_text())
    calendar = next(item for item in source["current_sources"] if item["name"] == "league_calendar")
    calendar["path"] = "not-yet-available.xlsx"

    assert load_canonical_league_calendar(tmp_path, LeagueProfile.from_dict(source)) is None


def test_profile_calendar_maps_consecutive_league_days_to_selected_serie_a_range(tmp_path):
    calendar = {"schema_version": "1.0", "league_id": "old", "teams": ["A", "B"], "participants_count": 2, "matchdays": [
        {"number": 1, "serie_a_matchday": 1, "fixtures": [{"home": "A", "away": "B"}]},
        {"number": 2, "serie_a_matchday": 2, "fixtures": [{"home": "A", "away": "B"}]},
    ]}
    (tmp_path / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
    source = json.loads((__import__("pathlib").Path(__file__).parents[1] / "config/default_profile.json").read_text())
    source["participants"]["team_names"] = ["A", "B"]
    source["participants"]["user_team"] = "A"
    source["payouts"]["prizes"] = [{"rank": 1, "amount_eur": 0}]
    source["current_sources"] = [{"name": "league_calendar", "path": "calendar.json", "format": "json"}]
    source["season"].update(fantasy_matchdays=2, fantasy_start_matchday=5, fantasy_end_matchday=6)

    loaded = load_canonical_league_calendar(tmp_path, LeagueProfile.from_dict(source))

    assert [day["serie_a_matchday"] for day in loaded["matchdays"]] == [5, 6]

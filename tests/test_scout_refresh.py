import json
from datetime import datetime, timezone

from advisor.scout_refresh import INJURIES_URL, refresh_official_scout


def injury_page(name: str, description: str) -> str:
    return f"""
    <div class="team-card">
      <span class="team-name">Atalanta</span>
      <div class="col">
        <span class="aa-infirmary-label">Infortunati</span>
        <ul><li><span class="item-name">{name}</span><span class="item-description">{description}</span></li></ul>
      </div>
    </div>
    """


def test_refresh_adds_monitor_and_short_lived_positive_recovery(tmp_path):
    raw = tmp_path / "raw"
    updates = tmp_path / "updates"
    raw.mkdir()
    (raw / "scout_ai_2026_27.json").write_text(
        json.dumps(
            {
                "season": "2026/27",
                "players": {
                    "1": {
                        "player_id": 1,
                        "name": "Recuperato",
                        "team": "Atalanta",
                        "status": "out",
                        "sources": [INJURIES_URL],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    players = [
        {"id": 1, "nome": "Recuperato", "squadra": "Atalanta"},
        {"id": 2, "nome": "In Ripresa", "squadra": "Atalanta"},
    ]

    snapshot = refresh_official_scout(
        players,
        "2026-27",
        raw_dir=raw,
        updates_dir=updates,
        fetcher=lambda _: injury_page(
            "In Ripresa", "Ha ripreso ad allenarsi e sara valutato nei prossimi giorni."
        ),
        now=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )

    assert snapshot["players"]["1"]["status"] == "positive"
    assert snapshot["players"]["1"]["expires_at"].startswith("2026-09-11")
    assert snapshot["players"]["2"]["status"] == "monitor"
    assert snapshot["counts"] == {"monitor": 1, "positive": 1}
    stored = json.loads((updates / "scout" / "2026-27.json").read_text(encoding="utf-8"))
    assert stored["players"]["1"]["status"] == "positive"

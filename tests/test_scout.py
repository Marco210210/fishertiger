from pathlib import Path

from advisor.scout import load_scout_snapshot, normalize_scout_snapshot


def test_scout_snapshot_is_clamped_and_sanitized() -> None:
    value = normalize_scout_snapshot(
        {
            "players": {
                "42": {
                    "name": "Bomber",
                    "status": "OUT",
                    "impact_percent": -90,
                    "confidence": 2,
                    "sources": ["https://club.example/news", "file:///secret"],
                },
                "bad": {"status": "positive"},
            }
        },
        "2026-27",
    )

    assert list(value["players"]) == ["42"]
    assert value["players"]["42"]["status"] == "out"
    assert value["players"]["42"]["impact_percent"] == -40
    assert value["players"]["42"]["multiplier"] == 0.6
    assert value["players"]["42"]["confidence"] == 1
    assert value["players"]["42"]["sources"] == ["https://club.example/news"]


def test_private_scout_update_wins_over_raw_snapshot(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    updates = tmp_path / "updates"
    raw.mkdir()
    (updates / "scout").mkdir(parents=True)
    (raw / "scout_ai_2026_27.json").write_text('{"provider":"raw"}', encoding="utf-8")
    (updates / "scout" / "2026-27.json").write_text('{"provider":"private"}', encoding="utf-8")

    value = load_scout_snapshot("2026-27", raw_dir=raw, updates_dir=updates)

    assert value["provider"] == "private"


def test_scout_snapshot_rejects_a_scalar_sources_value() -> None:
    value = normalize_scout_snapshot(
        {"players": [{"player_id": 42, "sources": "https://example.com/news"}]},
        "2026-27",
    )

    assert value["players"]["42"]["sources"] == []

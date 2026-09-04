from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from advisor.fantalab_live import FantaLabError, live_snapshot, parse_database, parse_room_id


ROOM = "86b90c3d-5206-4a0c-adf2-7513485baaf8"


def test_room_url_and_database_validation() -> None:
    assert parse_room_id(f"https://app.fantalab.it/asta?asta={ROOM}") == ROOM
    assert parse_room_id(ROOM.upper()) == ROOM
    assert parse_database("auto").automatic is True
    assert parse_database("default").value is None
    assert parse_database("9").value == 9
    with pytest.raises(FantaLabError):
        parse_database(21)


def test_invitation_link_explains_what_to_paste() -> None:
    with pytest.raises(FantaLabError, match="invito"):
        parse_room_id(f"https://app.fantalab.it/join-asta?invitation_id={ROOM}")


def test_live_snapshot_normalizes_lot_ledger_and_teams(tmp_path: Path) -> None:
    def requester(method: str, url: str, body: Any, headers: Any) -> Any:
        if url.endswith("/fantaleague/fetch"):
            assert headers["Authorization"] == "Bearer secret"
            return {
                "fantaleague_id": ROOM,
                "fantaleague_name": "Asta del sabato",
                "db": "9",
                "num_teams": 12,
                "num_credits": 500,
                "asta_type": "classic",
                "is_live": True,
                "fantateams": [{"fantateam_id": "team-a", "team_name": "Tigri", "position": 1, "max_credits": 500}],
            }
        if url.endswith("/v2/listone"):
            return {"players": [{"player_id": "fl-player", "fantacalcio_id": 42, "name": "Bomber", "team_name": "MIL", "role": "A"}]}
        if f"/auction/{ROOM}.json" in url:
            return {"player_id": "fl-player", "price": 37, "fantateam_id": "team-a", "user_id": "user-a", "last_update": 20, "last_bid_time": 10, "timeToPass": 8, "update_type": "raise"}
        if f"/assign/{ROOM}.json" in url:
            return None
        if f"/purchases/{ROOM}.json" in url:
            return {"sale-1": {"player_id": "fl-player", "price": 35, "fantateam_id": "team-a", "user_id": "user-a", "created_at": 1}}
        raise AssertionError(url)

    result = live_snapshot(
        {"room_url": f"https://app.fantalab.it/asta?asta={ROOM}", "db": "auto"},
        cache_path=tmp_path / "listone.json",
        token="secret",
        requester=requester,
    )

    assert result["db"] == 9
    assert result["connection"] == "token"
    assert result["read_only"] is True
    assert result["room"]["participants"] == 12
    assert result["room"]["name"] == "Asta del sabato"
    assert result["teams"][0]["name"] == "Tigri"
    assert result["lot"]["player_id"] == 42
    assert result["lot"]["price"] == 37
    assert result["lot"]["leader_team_id"] == "team-a"
    assert result["lot"]["leader_user_id"] == "user-a"
    assert result["purchases"][0]["purchase_id"] == "sale-1"
    assert result["purchases"][0]["player_id"] == 42
    assert result["purchases"][0]["buyer_user_id"] == "user-a"


def test_explicit_shard_needs_no_token(tmp_path: Path) -> None:
    def requester(method: str, url: str, body: Any, headers: Any) -> Any:
        if url.endswith("/v2/listone"):
            return {"players": []}
        if f"fantalab-4.europe-west1.firebasedatabase.app" in url:
            return None
        raise AssertionError(url)

    result = live_snapshot(
        {"room_id": ROOM, "db": 4},
        cache_path=tmp_path / "listone.json",
        requester=requester,
    )
    assert result["db"] == 4
    assert result["connection"] == "manuale"
    assert result["lot"] is None
    assert result["purchases"] == []


def test_public_room_reveals_the_current_leader_before_a_purchase(tmp_path: Path) -> None:
    def requester(method: str, url: str, body: Any, headers: Any) -> Any:
        if url.endswith("/v2/listone"):
            return {"players": []}
        if f"/auction/{ROOM}.json" in url:
            return {
                "player_id": "unknown-player",
                "price": 7,
                "fantateam_id": "anonymous-team",
                "last_update": 2,
            }
        if f"/assign/{ROOM}.json" in url or f"/purchases/{ROOM}.json" in url:
            return None
        raise AssertionError(url)

    result = live_snapshot(
        {"room_id": ROOM, "db": 4},
        cache_path=tmp_path / "listone.json",
        requester=requester,
    )
    assert result["teams"] == [
        {"id": "anonymous-team", "name": None, "position": None, "starting_credits": None}
    ]

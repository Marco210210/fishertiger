from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from advisor.fantalab_live import (
    FantaLabError,
    live_snapshot,
    parse_database,
    parse_room_id,
    resolve_fantalab_id_token,
)


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


def test_resolve_fantalab_id_token_returns_none_without_a_bootstrap_token(tmp_path: Path) -> None:
    def requester(url: str, params: Any) -> Any:
        raise AssertionError("Google must not be called when no refresh token is configured")

    assert resolve_fantalab_id_token(
        bootstrap_refresh_token=None,
        cache_path=tmp_path / "credentials.json",
        requester=requester,
    ) is None


def test_resolve_fantalab_id_token_renews_and_persists_the_rotated_refresh_token(tmp_path: Path) -> None:
    calls: list[str] = []

    def requester(url: str, params: Any) -> Any:
        assert params["grant_type"] == "refresh_token"
        calls.append(params["refresh_token"])
        return {"id_token": "id-1", "refresh_token": "rotated-1", "expires_in": "3600"}

    cache_path = tmp_path / "credentials.json"
    token = resolve_fantalab_id_token(
        bootstrap_refresh_token="seed-token",
        cache_path=cache_path,
        requester=requester,
        now=1_000.0,
    )
    assert token == "id-1"
    assert calls == ["seed-token"]
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached == {"refresh_token": "rotated-1", "id_token": "id-1", "expires_at": 4_600.0}


def test_resolve_fantalab_id_token_reuses_a_still_valid_cached_token(tmp_path: Path) -> None:
    cache_path = tmp_path / "credentials.json"
    cache_path.write_text(
        json.dumps({"refresh_token": "rotated-1", "id_token": "cached-id", "expires_at": 10_000.0}),
        encoding="utf-8",
    )

    def requester(url: str, params: Any) -> Any:
        raise AssertionError("a still-valid cached token must not trigger a renewal")

    token = resolve_fantalab_id_token(
        bootstrap_refresh_token="seed-token",
        cache_path=cache_path,
        requester=requester,
        now=9_000.0,
    )
    assert token == "cached-id"


def test_resolve_fantalab_id_token_renews_with_the_cached_rotated_token_first(tmp_path: Path) -> None:
    cache_path = tmp_path / "credentials.json"
    cache_path.write_text(
        json.dumps({"refresh_token": "rotated-1", "id_token": "stale-id", "expires_at": 1_000.0}),
        encoding="utf-8",
    )
    calls: list[str] = []

    def requester(url: str, params: Any) -> Any:
        calls.append(params["refresh_token"])
        return {"id_token": "id-2", "refresh_token": "rotated-2", "expires_in": "3600"}

    token = resolve_fantalab_id_token(
        bootstrap_refresh_token="seed-token",
        cache_path=cache_path,
        requester=requester,
        now=1_500.0,
    )
    assert token == "id-2"
    assert calls == ["rotated-1"]


def test_resolve_fantalab_id_token_falls_back_to_the_bootstrap_token_when_the_cached_one_is_stale(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "credentials.json"
    cache_path.write_text(
        json.dumps({"refresh_token": "revoked", "id_token": "stale-id", "expires_at": 1_000.0}),
        encoding="utf-8",
    )
    calls: list[str] = []

    def requester(url: str, params: Any) -> Any:
        calls.append(params["refresh_token"])
        if params["refresh_token"] == "revoked":
            raise FantaLabError("invalid")
        return {"id_token": "id-3", "refresh_token": "rotated-3", "expires_in": "3600"}

    token = resolve_fantalab_id_token(
        bootstrap_refresh_token="seed-token",
        cache_path=cache_path,
        requester=requester,
        now=1_500.0,
    )
    assert token == "id-3"
    assert calls == ["revoked", "seed-token"]


def test_resolve_fantalab_id_token_returns_none_when_every_credential_is_rejected(tmp_path: Path) -> None:
    def requester(url: str, params: Any) -> Any:
        raise FantaLabError("invalid")

    token = resolve_fantalab_id_token(
        bootstrap_refresh_token="seed-token",
        cache_path=tmp_path / "credentials.json",
        requester=requester,
    )
    assert token is None

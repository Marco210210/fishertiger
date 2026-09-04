"""Read-only FantaLab live-room integration.

The realtime database contains the current lot and the authoritative purchase
ledger.  Reads are intentionally isolated here so the rest of the advisor only
sees Fantacalcio player ids and normalized sales.  No bid/write operation is
implemented.

The wire format was documented and tested by Silvio Baratto's MIT-licensed
``fantabot`` project.  See ``THIRD_PARTY_NOTICES.md``.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


LISTONE_URL = "https://api.fantalab.it/v2/listone"
FANTALAB_API = "https://api.fantalab.it"
DEFAULT_DATABASE = "https://fantalab-79eaa-default-rtdb.europe-west1.firebasedatabase.app"
REGIONAL_DATABASE = "https://fantalab-{shard}.europe-west1.firebasedatabase.app"
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
LISTONE_MAX_AGE_SECONDS = 12 * 60 * 60
LISTONE_RETRY_AGE_SECONDS = 60
JsonRequester = Callable[[str, str, Mapping[str, Any] | None, Mapping[str, str] | None], Any]

# FantaLab's own web app authenticates against a Firebase project identified
# by a web API key. That key is not a secret by Firebase's own design (it
# ships inside every client bundle; access is governed by Firebase's auth
# checks, not by hiding this value) — it is still kept out of source and read
# from FISHERTIGER_FANTALAB_FIREBASE_API_KEY instead of being hardcoded here,
# purely so an automated secret scanner never mistakes it for a leaked one.
SECURE_TOKEN_URL = "https://securetoken.googleapis.com/v1/token"
ID_TOKEN_SAFETY_MARGIN_SECONDS = 5 * 60
FormRequester = Callable[[str, Mapping[str, str]], Any]


class FantaLabError(RuntimeError):
    """A safe, user-facing FantaLab integration error."""


@dataclass(frozen=True)
class DatabaseChoice:
    value: int | None
    automatic: bool = False

    @property
    def label(self) -> int | str:
        return "default" if self.value is None else self.value


def parse_room_id(value: Any) -> str:
    """Return a room UUID from a FantaLab room URL or a bare UUID."""
    text = str(value or "").strip()
    if UUID.fullmatch(text):
        return text.lower()
    parsed = urlsplit(text)
    query = parse_qs(parsed.query)
    if "invitation_id" in query or "join-asta" in parsed.path:
        raise FantaLabError(
            "Questo è un invito, non il link della stanza. Aprilo in FantaLab e copia "
            "l'indirizzo /asta?asta=… mostrato dopo l'accesso."
        )
    candidate = query.get("asta", [""])[0]
    if UUID.fullmatch(candidate):
        return candidate.lower()
    raise FantaLabError(
        "Link FantaLab non riconosciuto. Incolla il link della stanza "
        "https://app.fantalab.it/asta?asta=…"
    )


def parse_database(value: Any) -> DatabaseChoice:
    if value in (None, "", "auto"):
        return DatabaseChoice(None, automatic=True)
    if value == "default":
        return DatabaseChoice(None)
    if isinstance(value, bool):
        raise FantaLabError("Lo shard FantaLab non è valido.")
    try:
        shard = int(value)
    except (TypeError, ValueError):
        raise FantaLabError("Lo shard deve essere 'auto', 'default' oppure un numero da 0 a 19.") from None
    if not 0 <= shard <= 19:
        raise FantaLabError("Lo shard FantaLab deve essere compreso tra 0 e 19.")
    return DatabaseChoice(shard)


def database_url(db: int | None) -> str:
    return DEFAULT_DATABASE if db is None else REGIONAL_DATABASE.format(shard=db)


def node_url(db: int | None, node: str, room_id: str) -> str:
    return f"{database_url(db)}/{node}/{room_id}.json"


def request_json(
    method: str,
    url: str,
    body: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> Any:
    """Small JSON transport that never includes response bodies in errors."""
    payload = json.dumps(dict(body)).encode("utf-8") if body is not None else None
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=12) as response:  # noqa: S310 - fixed/validated hosts
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise FantaLabError(f"FantaLab ha risposto con stato HTTP {error.code}.") from None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise FantaLabError("FantaLab non è raggiungibile in questo momento.") from None


def _request_form(url: str, params: Mapping[str, str]) -> Any:
    """Form-encoded transport for Google's OAuth2-style token endpoint, which
    (unlike FantaLab's own API) does not accept a JSON body."""
    payload = urlencode(dict(params)).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:  # noqa: S310 - fixed host
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise FantaLabError(f"Google ha risposto con stato HTTP {error.code}.") from None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise FantaLabError("Il servizio di autenticazione FantaLab non è raggiungibile in questo momento.") from None


def _refresh_id_token(
    refresh_token: str,
    *,
    api_key: str,
    requester: FormRequester = _request_form,
) -> dict[str, str]:
    """Exchange a long-lived Firebase refresh token for a fresh ID token.

    Google rotates the refresh token on every exchange; the caller must
    persist the one returned here, not the one it sent, or the credential
    goes stale after the first automatic renewal.
    """
    body = requester(
        f"{SECURE_TOKEN_URL}?key={api_key}",
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    if (
        not isinstance(body, Mapping)
        or not isinstance(body.get("id_token"), str)
        or not isinstance(body.get("refresh_token"), str)
        or not isinstance(body.get("expires_in"), str)
    ):
        raise FantaLabError("Il rinnovo dell'accesso FantaLab non è riuscito.")
    return {
        "id_token": body["id_token"],
        "refresh_token": body["refresh_token"],
        "expires_in": body["expires_in"],
    }


def _load_cached_credentials(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        isinstance(value, dict)
        and isinstance(value.get("refresh_token"), str)
        and isinstance(value.get("id_token"), str)
        and isinstance(value.get("expires_at"), (int, float))
    ):
        return value
    return None


def _save_cached_credentials(path: Path, value: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(value)), encoding="utf-8")
    except OSError:
        pass


def resolve_fantalab_id_token(
    *,
    bootstrap_refresh_token: str | None,
    api_key: str | None,
    cache_path: Path,
    requester: FormRequester = _request_form,
    now: float | None = None,
) -> str | None:
    """Return a fresh FantaLab ID token, renewing it from the configured
    refresh token whenever it is missing or close to expiry.

    The renewal response's rotated refresh token is cached to disk, so the
    integration keeps renewing itself indefinitely, across polls and across
    server restarts, without ever needing a new manual capture — only the
    very first ``bootstrap_refresh_token`` has to come from the browser.
    """
    if not bootstrap_refresh_token or not api_key:
        return None
    current_time = time.time() if now is None else now
    cached = _load_cached_credentials(cache_path)
    if cached and cached["expires_at"] - ID_TOKEN_SAFETY_MARGIN_SECONDS > current_time:
        return cached["id_token"]

    for candidate in ([cached["refresh_token"]] if cached else []) + [bootstrap_refresh_token]:
        try:
            response = _refresh_id_token(candidate, api_key=api_key, requester=requester)
        except FantaLabError:
            continue
        _save_cached_credentials(
            cache_path,
            {
                "refresh_token": response["refresh_token"],
                "id_token": response["id_token"],
                "expires_at": current_time + int(response["expires_in"]),
            },
        )
        return response["id_token"]
    return None


def _room_config(room_id: str, token: str, requester: JsonRequester) -> dict[str, Any]:
    body = requester(
        "POST",
        f"{FANTALAB_API}/fantaleague/fetch",
        {"fantaleague_id": room_id, "type": "fantaleague"},
        {"Authorization": f"Bearer {token}"},
    )
    if not isinstance(body, Mapping):
        raise FantaLabError("FantaLab ha restituito una configurazione stanza non valida.")
    return dict(body)


def _config_database(config: Mapping[str, Any]) -> int | None:
    value = config.get("db")
    if value is None or value == "":
        return None
    try:
        shard = int(value)
    except (TypeError, ValueError):
        return None
    return shard if 0 <= shard <= 19 else None


def _read_nodes(
    db: int | None,
    room_id: str,
    requester: JsonRequester,
    *,
    parallel: bool = True,
) -> dict[str, Any]:
    nodes = ("auction", "assign", "purchases")
    if not parallel:
        values: dict[str, Any] = {}
        for node in nodes:
            value = requester("GET", node_url(db, node, room_id), None, None)
            values[node] = value if isinstance(value, Mapping) else None
        return values

    values = {}
    with ThreadPoolExecutor(max_workers=len(nodes)) as executor:
        futures = {
            executor.submit(requester, "GET", node_url(db, node, room_id), None, None): node
            for node in nodes
        }
        for future in as_completed(futures):
            value = future.result()
            values[futures[future]] = value if isinstance(value, Mapping) else None
    return values


def discover_database(room_id: str, requester: JsonRequester) -> tuple[int | None, dict[str, Any]]:
    """Probe FantaLab's known namespaces once; subsequent polls reuse the result."""
    choices: list[int | None] = [None, *range(20)]
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(_read_nodes, choice, room_id, requester, parallel=False): choice
            for choice in choices
        }
        for future in as_completed(futures):
            try:
                values = future.result()
            except FantaLabError:
                continue
            if any(values.values()):
                for pending in futures:
                    pending.cancel()
                return futures[future], values
    raise FantaLabError(
        "Stanza non trovata automaticamente. Se l'asta non è ancora partita, indica lo "
        "shard mostrato da FantaLab oppure configura il token sul server."
    )


def _load_cached_listone(
    path: Path,
    *,
    max_age_seconds: int = LISTONE_MAX_AGE_SECONDS,
) -> dict[str, Any] | None:
    try:
        if time.time() - path.stat().st_mtime > max_age_seconds:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("players") else None


def load_listone(
    path: Path,
    requester: JsonRequester,
    *,
    max_age_seconds: int = LISTONE_MAX_AGE_SECONDS,
) -> dict[str, dict[str, Any]]:
    payload = _load_cached_listone(path, max_age_seconds=max_age_seconds)
    if payload is None:
        try:
            fetched = requester("GET", LISTONE_URL, None, None)
            payload = dict(fetched) if isinstance(fetched, Mapping) else {}
            if payload.get("players"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except (FantaLabError, OSError):
            try:
                stale = json.loads(path.read_text(encoding="utf-8"))
                payload = stale if isinstance(stale, dict) else {}
            except (OSError, ValueError):
                payload = {}

    result: dict[str, dict[str, Any]] = {}
    for raw in payload.get("players", ()):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("player_id"), str):
            continue
        result[raw["player_id"]] = {
            "fantacalcio_id": raw.get("fantacalcio_id") if isinstance(raw.get("fantacalcio_id"), int) else None,
            "name": raw.get("name") if isinstance(raw.get("name"), str) else None,
            "team": raw.get("team_name") if isinstance(raw.get("team_name"), str) else None,
            "role": raw.get("role") if isinstance(raw.get("role"), str) else None,
        }
    return result


def _latest_lot(nodes: Mapping[str, Any]) -> Mapping[str, Any] | None:
    candidates = [nodes.get("auction"), nodes.get("assign")]
    valid = [item for item in candidates if isinstance(item, Mapping) and isinstance(item.get("player_id"), str)]
    if not valid:
        return None
    return max(valid, key=lambda item: int(item.get("last_update") or item.get("last_bid_time") or 0))


def _normalize_player(player_uuid: Any, bridge: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    key = player_uuid if isinstance(player_uuid, str) else ""
    item = bridge.get(key, {})
    return {
        "fantalab_id": key or None,
        "player_id": item.get("fantacalcio_id"),
        "name": item.get("name"),
        "team": item.get("team"),
        "role": item.get("role"),
    }


def _normalize_lot(raw: Mapping[str, Any] | None, bridge: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    if raw is None:
        return None
    player = _normalize_player(raw.get("player_id"), bridge)
    price = raw.get("price")
    return {
        **player,
        "price": price if isinstance(price, int) and not isinstance(price, bool) else 0,
        "leader_team_id": raw.get("fantateam_id") if isinstance(raw.get("fantateam_id"), str) else None,
        "leader_user_id": raw.get("user_id") if isinstance(raw.get("user_id"), str) else None,
        "update_type": raw.get("update_type") if isinstance(raw.get("update_type"), str) else None,
        "closed": raw.get("asta_state") == "closed" or raw.get("update_type") == "close_auction",
        "last_bid_time": raw.get("last_bid_time") if isinstance(raw.get("last_bid_time"), int) else None,
        "timer_seconds": raw.get("timeToPass") if isinstance(raw.get("timeToPass"), int) else None,
    }


def _normalize_purchases(raw: Any, bridge: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return []
    rows: list[tuple[str, Mapping[str, Any]]] = [
        (str(key), value) for key, value in raw.items() if isinstance(value, Mapping)
    ]
    rows.sort(key=lambda item: int(item[1].get("created_at") or 0))
    result = []
    for purchase_id, record in rows:
        price = record.get("price")
        if not isinstance(record.get("player_id"), str) or isinstance(price, bool) or not isinstance(price, int):
            continue
        result.append({
            "purchase_id": purchase_id,
            **_normalize_player(record["player_id"], bridge),
            "price": price,
            "buyer_team_id": record.get("fantateam_id") if isinstance(record.get("fantateam_id"), str) else None,
            "buyer_user_id": record.get("user_id") if isinstance(record.get("user_id"), str) else None,
            "created_at": record.get("created_at") if isinstance(record.get("created_at"), int) else None,
            "unsold": price <= 0 or not isinstance(record.get("fantateam_id"), str),
        })
    return result


def _team_rows(
    config: Mapping[str, Any] | None,
    purchases: list[dict[str, Any]],
    lot: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    if config:
        for index, raw in enumerate(config.get("fantateams", ())):
            if not isinstance(raw, Mapping) or not isinstance(raw.get("fantateam_id"), str):
                continue
            team_id = raw["fantateam_id"]
            found[team_id] = {
                "id": team_id,
                "name": raw.get("team_name") if isinstance(raw.get("team_name"), str) else None,
                "position": raw.get("position") if isinstance(raw.get("position"), int) else index + 1,
                "starting_credits": raw.get("max_credits") if isinstance(raw.get("max_credits"), int) else None,
            }
    for purchase in purchases:
        team_id = purchase.get("buyer_team_id")
        if team_id and team_id not in found:
            found[team_id] = {"id": team_id, "name": None, "position": None, "starting_credits": None}
    leader_id = lot.get("leader_team_id") if isinstance(lot, Mapping) else None
    if isinstance(leader_id, str) and leader_id not in found:
        found[leader_id] = {
            "id": leader_id,
            "name": None,
            "position": None,
            "starting_credits": None,
        }
    return sorted(found.values(), key=lambda team: (team["position"] is None, team["position"] or 999, team["id"]))


def live_snapshot(
    request: Mapping[str, Any],
    *,
    cache_path: Path,
    token: str | None = None,
    requester: JsonRequester = request_json,
) -> dict[str, Any]:
    """Resolve a room and return one normalized, read-only live frame."""
    room_id = parse_room_id(request.get("room_url") or request.get("room_id"))
    choice = parse_database(request.get("db", "auto"))
    config: dict[str, Any] | None = None
    if token:
        try:
            config = _room_config(room_id, token, requester)
        except FantaLabError:
            config = None

    if choice.automatic and config is not None:
        db = _config_database(config)
        nodes = _read_nodes(db, room_id, requester)
        connection = "token"
    elif choice.automatic:
        db, nodes = discover_database(room_id, requester)
        connection = "scansione"
    else:
        db = choice.value
        nodes = _read_nodes(db, room_id, requester)
        connection = "manuale"

    bridge = load_listone(cache_path, requester)
    purchases = _normalize_purchases(nodes.get("purchases"), bridge)
    lot = _normalize_lot(_latest_lot(nodes), bridge)
    unmapped_players = sum(1 for item in purchases if item["player_id"] is None) + (
        1 if lot and lot["player_id"] is None else 0
    )
    # FantaLab can add a player UUID during the evening. Fantabot learned this
    # from live rooms and refreshes its id bridge when a new UUID cannot be
    # resolved. Retry at most once a minute so the 1.2 s room polling never
    # hammers the listone endpoint, then resolve the same frame immediately.
    if unmapped_players and bridge:
        refreshed = load_listone(
            cache_path,
            requester,
            max_age_seconds=LISTONE_RETRY_AGE_SECONDS,
        )
        if refreshed != bridge:
            bridge = refreshed
            purchases = _normalize_purchases(nodes.get("purchases"), bridge)
            lot = _normalize_lot(_latest_lot(nodes), bridge)
            unmapped_players = sum(
                1 for item in purchases if item["player_id"] is None
            ) + (1 if lot and lot["player_id"] is None else 0)
    return {
        "room_id": room_id,
        "db": "default" if db is None else db,
        "connection": connection,
        "read_only": True,
        "server_time_ms": int(time.time() * 1000),
        "room": {
            "name": config.get("fantaleague_name") if config and isinstance(config.get("fantaleague_name"), str) else None,
            "participants": config.get("num_teams") if config and isinstance(config.get("num_teams"), int) else None,
            "starting_credits": config.get("num_credits") if config and isinstance(config.get("num_credits"), int) else None,
            "auction_type": config.get("asta_type") if config and isinstance(config.get("asta_type"), str) else None,
            "is_live": bool(config.get("is_live")) if config else None,
        },
        "teams": _team_rows(config, purchases, lot),
        "lot": lot,
        "purchases": purchases,
        "unmapped_players": unmapped_players,
    }

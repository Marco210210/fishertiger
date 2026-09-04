"""Refresh the persistent Scout snapshot from verified public information."""
from __future__ import annotations

import json
import re
import tempfile
import threading
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process

from .scout import load_scout_snapshot, normalize_scout_snapshot
from .sosfanta_updates import fetch_page


INJURIES_URL = "https://www.fantacalcio.it/serie-a/indisponibili"
FetchPage = Callable[[str], str]
_LOCK = threading.Lock()


def _key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in normalized if character.isalnum()).lower()


def _injury_impact(description: str) -> tuple[str, float, float]:
    text = _key(description)
    improving = any(
        marker in text
        for marker in (
            "haripreso",
            "staultimando",
            "prontoatornare",
            "inripresa",
            "tornatoadallenarsi",
        )
    )
    markers = ("recuperabile", "rientro", "tornare", "torna", "convocabile", "arruolabile")
    positions = [text.rfind(marker) for marker in markers if marker in text]
    recovery = text[max(positions) :] if positions else text
    if "2027" in recovery or "gennaio" in recovery:
        return "out", -30, 0.05
    if "dicembre" in recovery or "tremesi" in recovery:
        return "out", -24, 0.1
    if "novembre" in recovery or "duemesi" in recovery:
        return "out", -18, 0.15
    if improving:
        return "monitor", -1, 0.65
    if "ottobre" in recovery or "gironedandata" in recovery:
        return "out", -10, 0.2
    if "finedisettembre" in recovery or "secondametadisettembre" in recovery:
        return "out", -6, 0.3
    if "metadisettembre" in recovery or "primametadisettembre" in recovery:
        return "doubt", -4, 0.45
    if any(word in text for word in ("rischio", "davalutare", "indubbio", "forfait", "assente")):
        return "doubt", -3, 0.4
    return "monitor", -2, 0.55


def official_injury_rows(
    players: list[dict[str, Any]], fetcher: FetchPage = fetch_page
) -> list[dict[str, Any]]:
    """Join the official injury table to the current Fantacalcio player IDs."""
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in players:
        by_team[_key(player.get("squadra"))].append(player)
    soup = BeautifulSoup(fetcher(INJURIES_URL), "html.parser")
    result: list[dict[str, Any]] = []
    for card in soup.select(".team-card"):
        team_node = card.select_one(".team-name")
        injury_label = card.select_one(".aa-infirmary-label")
        injury_column = injury_label.find_parent(class_="col") if injury_label else None
        if not team_node or injury_column is None:
            continue
        team = team_node.get_text(" ", strip=True)
        roster = by_team.get(_key(team), [])
        choices = {str(player["id"]): player["nome"] for player in roster}
        for item in injury_column.select("li"):
            name_node = item.select_one(".item-name")
            description_node = item.select_one(".item-description")
            if not name_node or not description_node or not choices:
                continue
            source_name = name_node.get_text(" ", strip=True)
            match = process.extractOne(source_name, choices, scorer=fuzz.WRatio, score_cutoff=65)
            if not match:
                continue
            _, score, player_id = match
            player = next(candidate for candidate in roster if str(candidate["id"]) == player_id)
            description = description_node.get_text(" ", strip=True)
            status, impact, availability = _injury_impact(description)
            result.append(
                {
                    "player_id": player["id"],
                    "name": player["nome"],
                    "team": player["squadra"],
                    "status": status,
                    "availability": availability,
                    "starter": 0.5,
                    "impact_percent": impact,
                    "confidence": min(0.95, 0.65 + score / 400),
                    "headline": f"{source_name}: situazione fisica aggiornata",
                    "summary": description,
                    "sources": [INJURIES_URL],
                }
            )
    return result


def refresh_official_scout(
    players: list[dict[str, Any]],
    season_slug: str,
    *,
    raw_dir: Path,
    updates_dir: Path,
    fetcher: FetchPage = fetch_page,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh injuries and retain short-lived positive recovery signals."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    previous = load_scout_snapshot(season_slug, raw_dir=raw_dir, updates_dir=updates_dir)
    current_rows = official_injury_rows(players, fetcher)
    current_ids = {str(row["player_id"]) for row in current_rows}
    rows: dict[str, dict[str, Any]] = {str(row["player_id"]): row for row in current_rows}

    for player_id, old in previous.get("players", {}).items():
        if player_id in current_ids:
            continue
        expires_at = old.get("expires_at")
        if old.get("status") == "positive" and expires_at:
            try:
                if datetime.fromisoformat(expires_at) > now:
                    rows[player_id] = old
            except ValueError:
                pass
            continue
        if old.get("status") not in {"out", "doubt", "monitor"}:
            continue
        if INJURIES_URL not in old.get("sources", []):
            continue
        rows[player_id] = {
            "player_id": old["player_id"],
            "name": old.get("name", ""),
            "team": old.get("team", ""),
            "status": "positive",
            "availability": 0.9,
            "starter": old.get("starter", 0.5),
            "impact_percent": 2,
            "confidence": 0.75,
            "headline": f"{old.get('name', 'Giocatore')}: segnale di rientro",
            "summary": "Non compare piu nell'elenco ufficiale degli indisponibili. Segnale positivo da confermare con convocazioni e ultime dai campi.",
            "sources": [INJURIES_URL],
            "expires_at": (now + timedelta(days=7)).isoformat(),
        }

    snapshot = normalize_scout_snapshot(
        {
            "version": 1,
            "season": season_slug.replace("-", "/", 1),
            "generated_at": now.isoformat(),
            "provider": "Fantacalcio.it · aggiornamento automatico",
            "lookback_days": 7,
            "players": rows,
        },
        season_slug,
    )
    destination = updates_dir / "scout" / f"{season_slug}.json"
    with _LOCK:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=destination.parent, delete=False
        ) as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(destination)
    snapshot["counts"] = dict(Counter(row["status"] for row in snapshot["players"].values()))
    return snapshot

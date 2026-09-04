"""Validated snapshots produced by the optional AI news scout."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SEASON_SLUG = re.compile(r"20\d{2}-\d{2}\Z")
ALLOWED_STATUS = {"out", "doubt", "monitor", "positive", "neutral"}


def _number(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return fallback


def _text(value: Any, maximum: int = 1000) -> str:
    return str(value or "").strip()[:maximum]


def _source(value: Any) -> str | None:
    text = _text(value, 2000)
    parsed = urlsplit(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else None


def normalize_scout_snapshot(value: Any, season_slug: str) -> dict[str, Any]:
    if not SEASON_SLUG.fullmatch(season_slug):
        raise ValueError("Invalid season slug")
    raw = value if isinstance(value, Mapping) else {}
    raw_players = raw.get("players", {})
    if isinstance(raw_players, list):
        raw_players = {str(item.get("player_id")): item for item in raw_players if isinstance(item, Mapping) and item.get("player_id") is not None}
    if not isinstance(raw_players, Mapping):
        raw_players = {}

    players: dict[str, Any] = {}
    for key, item in raw_players.items():
        if not isinstance(item, Mapping):
            continue
        player_id = str(item.get("player_id", key)).strip()
        if not player_id or not player_id.isdigit():
            continue
        impact = _number(item.get("impact_percent"), -40, 12, 0)
        status = _text(item.get("status"), 20).lower()
        raw_sources = item.get("sources", ())
        if not isinstance(raw_sources, (list, tuple)):
            raw_sources = ()
        sources = [source for source in (_source(url) for url in raw_sources) if source]
        players[player_id] = {
            "player_id": int(player_id),
            "name": _text(item.get("name"), 120),
            "team": _text(item.get("team"), 80),
            "status": status if status in ALLOWED_STATUS else "monitor",
            "availability": _number(item.get("availability"), 0, 1, 1),
            "starter": _number(item.get("starter"), 0, 1, 0.5),
            "impact_percent": impact,
            "multiplier": round(1 + impact / 100, 4),
            "confidence": _number(item.get("confidence"), 0, 1, 0),
            "headline": _text(item.get("headline"), 180),
            "summary": _text(item.get("summary"), 1200),
            "sources": sources[:6],
            **({"expires_at": _text(item.get("expires_at"), 60)} if item.get("expires_at") else {}),
        }

    return {
        "version": 1,
        "season": _text(raw.get("season"), 20) or season_slug.replace("-", "/", 1),
        "generated_at": _text(raw.get("generated_at"), 60) or None,
        "provider": _text(raw.get("provider"), 60) or None,
        "lookback_days": int(_number(raw.get("lookback_days"), 1, 30, 10)),
        "players": players,
    }


def load_scout_snapshot(season_slug: str, *, raw_dir: Path, updates_dir: Path) -> dict[str, Any]:
    """Prefer a private/persistent update over the optional committed snapshot."""
    if not SEASON_SLUG.fullmatch(season_slug):
        raise ValueError("Invalid season slug")
    candidates = [
        updates_dir / "scout" / f"{season_slug}.json",
        raw_dir / f"scout_ai_{season_slug.replace('-', '_')}.json",
    ]
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return normalize_scout_snapshot(value, season_slug)
    return normalize_scout_snapshot({}, season_slug)

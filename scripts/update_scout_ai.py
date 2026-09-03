"""Build a compact, source-linked news snapshot with a Claude subscription.

The script makes one request per Serie A team and stores only material news.
It is intentionally separate from the web server: the auction reads a cached
snapshot and never waits for an AI response.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process

from advisor.scout import normalize_scout_snapshot
from advisor.sosfanta_updates import fetch_page


DEFAULT_DATASET = Path("data/processed/example-2026-27/2026-27/auction_data.json")
DEFAULT_OUTPUT = Path("data/raw/scout_ai_2026_27.json")
INJURIES_URL = "https://www.fantacalcio.it/serie-a/indisponibili"

SCHEMA = {
    "type": "object",
    "properties": {
        "players": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "player_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "team": {"type": "string"},
                    "status": {"type": "string", "enum": ["out", "doubt", "monitor", "positive", "neutral"]},
                    "availability": {"type": "number", "minimum": 0, "maximum": 1},
                    "starter": {"type": "number", "minimum": 0, "maximum": 1},
                    "impact_percent": {"type": "number", "minimum": -40, "maximum": 12},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                },
                "required": ["player_id", "name", "team", "status", "availability", "starter", "impact_percent", "confidence", "headline", "summary", "sources"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["players"],
    "additionalProperties": False,
}


def _key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in normalized if character.isalnum()).lower()


def _prompt(team: str, players: list[dict[str, Any]], *, today: str, lookback_days: int) -> str:
    roster = "\n".join(f"- {player['id']}: {player['nome']} ({player['ruolo']})" for player in players)
    return f"""Sei lo scout di un'asta di fantacalcio Classic che si terrà domani.

OGGI: {today}
SQUADRA: {team}
FINESTRA: ultimi {lookback_days} giorni.

ROSA DEL LISTONE (usa esclusivamente questi ID):
{roster}

Cerca sul web notizie recenti e verificabili. Dai priorità a sito ufficiale del club, fantacalcio.it, Sky Sport, SOS Fanta/Fantamaster e fonti locali affidabili. Restituisci SOLO giocatori per cui una notizia può cambiare concretamente il prezzo d'asta: infortunio, recupero, squalifica, trasferimento, perdita/conquista del posto, cambio rigorista o gerarchia netta. Non segnalare articoli generici, voti dell'ultima partita o semplici opinioni.

Regole:
- fonti: solo URL realmente aperti; preferisci almeno due conferme per notizie non ufficiali;
- summary: breve, in italiano, con data del fatto e tempi di rientro se disponibili;
- availability e starter sono probabilità 0..1;
- impact_percent è prudente e stagionale, non sulla sola prossima giornata: da -40 a +12;
- confidence è 0..1 e deve scendere se la fonte o i tempi sono incerti;
- se non esiste alcuna notizia materiale restituisci players vuoto;
- non inventare e non includere calciatori fuori dalla lista.
"""


def _structured_result(stdout: str) -> dict[str, Any]:
    envelope = json.loads(stdout)
    if isinstance(envelope, dict) and isinstance(envelope.get("structured_output"), dict):
        return envelope["structured_output"]
    result = envelope.get("result") if isinstance(envelope, dict) else envelope
    if isinstance(result, str):
        return json.loads(result)
    if isinstance(result, dict):
        return result
    raise ValueError("Claude did not return structured JSON")


def run_claude(prompt: str, *, model: str, timeout: int) -> dict[str, Any]:
    command = [
        "claude",
        "--print",
        "--model", model,
        "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA, separators=(",", ":")),
        "--allowedTools", "WebSearch,WebFetch",
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
        prompt,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if completed.returncode:
        message = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else ""
        if not message:
            try:
                failure = json.loads(completed.stdout)
                message = str(failure.get("result") or "").strip()
            except (ValueError, AttributeError):
                pass
        message = message or f"exit {completed.returncode}"
        raise RuntimeError(f"Claude failed: {message}")
    return _structured_result(completed.stdout)


def _injury_impact(description: str) -> tuple[str, float, float]:
    text = _key(description)
    markers = ("recuperabile", "rientro", "tornare", "torna", "convocabile", "arruolabile")
    positions = [text.rfind(marker) for marker in markers if marker in text]
    recovery = text[max(positions):] if positions else text
    if "2027" in recovery or "gennaio" in recovery:
        return "out", -30, 0.05
    if "dicembre" in recovery or "tremesi" in recovery:
        return "out", -24, 0.1
    if "novembre" in recovery or "duemesi" in recovery:
        return "out", -18, 0.15
    if "ottobre" in recovery or "gironedandata" in recovery:
        return "out", -10, 0.2
    if "finedisettembre" in recovery or "secondametadisettembre" in recovery:
        return "out", -6, 0.3
    if "metadisettembre" in recovery or "primametadisettembre" in recovery:
        return "doubt", -4, 0.45
    if any(word in text for word in ("rischio", "davalutare", "indubbio", "forfait", "assente")):
        return "doubt", -3, 0.4
    return "monitor", -2, 0.55


def official_injury_rows(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic fallback: current official injury page, exact team + fuzzy name join."""
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in players:
        by_team[_key(player.get("squadra"))].append(player)
    soup = BeautifulSoup(fetch_page(INJURIES_URL), "html.parser")
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
                print(f"Scout fallback: nessuna corrispondenza per {team} / {source_name}")
                continue
            _, score, player_id = match
            player = next(candidate for candidate in roster if str(candidate["id"]) == player_id)
            description = description_node.get_text(" ", strip=True)
            status, impact, availability = _injury_impact(description)
            result.append({
                "player_id": player["id"],
                "name": player["nome"],
                "team": player["squadra"],
                "status": status,
                "availability": availability,
                "starter": 0.5,
                "impact_percent": impact,
                "confidence": min(0.95, 0.65 + score / 400),
                "headline": f"{source_name}: indisponibilità da valutare nell'asta",
                "summary": description,
                "sources": [INJURIES_URL],
            })
    return result


def _write_snapshot(path: Path, snapshot: dict[str, Any]) -> bool:
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None
    if isinstance(current, dict):
        before = {key: value for key, value in current.items() if key != "generated_at"}
        after = {key: value for key, value in snapshot.items() if key != "generated_at"}
        if before == after:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update the cached Scout AI news snapshot.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--team", action="append", default=[], help="Only refresh this team (repeatable).")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-official-fallback", action="store_true")
    parser.add_argument("--official-only", action="store_true", help="Skip Claude and refresh only official injuries.")
    args = parser.parse_args(argv)

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    teams: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in dataset.get("players", ()):
        if isinstance(player, dict) and player.get("id") is not None and player.get("squadra"):
            teams[str(player["squadra"])].append(player)
    selected = {_key(team) for team in args.team}
    if selected:
        teams = {team: players for team, players in teams.items() if _key(team) in selected}
    if not teams:
        raise SystemExit("No matching teams in the dataset")

    profile_meta = dataset.get("meta", {}).get("profile", {})
    season_value = profile_meta.get("season", "2026/27") if isinstance(profile_meta, dict) else "2026/27"
    season = season_value.get("season", "2026/27") if isinstance(season_value, dict) else season_value
    season_slug = str(season).replace("/", "-")
    today = datetime.now(UTC).date().isoformat()
    existing: dict[str, Any] = {}
    try:
        old = normalize_scout_snapshot(json.loads(args.output.read_text(encoding="utf-8")), season_slug)
        existing = dict(old["players"])
    except (OSError, ValueError):
        pass

    if args.official_only:
        rows = official_injury_rows(dataset.get("players", []))
        snapshot = normalize_scout_snapshot(
            {
                "version": 1,
                "season": season,
                "generated_at": datetime.now(UTC).isoformat(),
                "provider": "Fantacalcio.it · elenco ufficiale",
                "lookback_days": args.lookback_days,
                "players": rows,
            },
            season_slug,
        )
        _write_snapshot(args.output, snapshot)
        print(f"Salvate {len(snapshot['players'])} indisponibilità in {args.output}")
        return 0

    for team, roster in sorted(teams.items()):
        print(f"Scout AI: {team} ({len(roster)} giocatori)", flush=True)
        if args.dry_run:
            continue
        try:
            response = run_claude(
                _prompt(team, roster, today=today, lookback_days=args.lookback_days),
                model=args.model,
                timeout=args.timeout,
            )
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            if args.no_official_fallback:
                raise
            print(f"Claude non disponibile ({error}). Uso l'elenco ufficiale degli indisponibili.")
            rows = official_injury_rows(dataset.get("players", []))
            snapshot = normalize_scout_snapshot(
                {
                    "version": 1,
                    "season": season,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "provider": "Fantacalcio.it · fallback verificato",
                    "lookback_days": args.lookback_days,
                    "players": rows,
                },
                season_slug,
            )
            _write_snapshot(args.output, snapshot)
            print(f"Salvate {len(snapshot['players'])} indisponibilità in {args.output}")
            return 0
        roster_ids = {str(player["id"]) for player in roster}
        for player_id in [key for key, row in existing.items() if _key(row.get("team")) == _key(team)]:
            existing.pop(player_id, None)
        for row in response.get("players", ()):
            if isinstance(row, dict) and str(row.get("player_id")) in roster_ids:
                existing[str(row["player_id"])] = row
        snapshot = normalize_scout_snapshot(
            {
                "version": 1,
                "season": season,
                "generated_at": datetime.now(UTC).isoformat(),
                "provider": f"Claude Code ({args.model})",
                "lookback_days": args.lookback_days,
                "players": existing,
            },
            season_slug,
        )
        _write_snapshot(args.output, snapshot)

    if args.dry_run:
        print("Dry run: nessuna chiamata AI e nessun file scritto.")
    else:
        print(f"Salvate {len(existing)} segnalazioni in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

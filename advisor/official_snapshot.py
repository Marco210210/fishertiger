"""Parse a versioned snapshot from Fantacalcio's public quotations and stats pages."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
from bs4 import BeautifulSoup, Tag


QUOTATIONS_URL = "https://www.fantacalcio.it/quotazioni-fantacalcio"
STATS_URL = "https://www.fantacalcio.it/statistiche-serie-a/{season}/italia"
MAX_PAGE_BYTES = 5_000_000
SNAPSHOT_SCHEMA_VERSION = 1
CURRENT_STATS_PRIOR_APPEARANCES = 8.0


class OfficialSnapshotError(ValueError):
    """The public pages or stored snapshot do not have the expected shape."""


def fetch_page(url: str) -> str:
    request = Request(url, headers={"User-Agent": "FantaAdvisor/1.0 (public data snapshot)"})
    try:
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise OfficialSnapshotError(f"Fantacalcio returned HTTP {response.status}.")
            payload = response.read(MAX_PAGE_BYTES + 1)
    except OfficialSnapshotError:
        raise
    except Exception as error:
        raise OfficialSnapshotError("Fantacalcio could not be reached.") from error
    if len(payload) > MAX_PAGE_BYTES:
        raise OfficialSnapshotError("Fantacalcio returned an unexpectedly large page.")
    return payload.decode(response.headers.get_content_charset() or "utf-8", "replace")


def _number(value: str, field: str) -> float | int:
    cleaned = re.sub(r"[^0-9,.-]", "", value.strip()).replace(",", ".")
    try:
        number = float(cleaned)
    except ValueError as error:
        raise OfficialSnapshotError(f"Invalid {field} value in the public page.") from error
    return int(number) if number.is_integer() else number


def _cell(row: Tag, key: str) -> str:
    node = row.select_one(f'[data-col-key="{key}"]')
    return node.get_text(" ", strip=True) if node is not None else ""


def _identity(row: Tag) -> tuple[int, str, str, str, str]:
    link = row.select_one("a.player-link")
    href = str(link.get("href", "")) if link else ""
    match = re.search(r"/squadre/([^/]+)/[^/]+/(\d+)(?:/|$)", href)
    if not match or link is None:
        raise OfficialSnapshotError("A public player row has no recognizable identity.")
    classic = str(row.get("data-filter-role-classic", "")).strip().upper()
    mantra = str(row.get("data-filter-role-mantra", "")).strip()
    if classic not in {"P", "D", "C", "A"} or not mantra:
        raise OfficialSnapshotError("A public player row has an invalid role.")
    mantra = ";".join(part.capitalize() for part in mantra.split(";") if part)
    return int(match.group(2)), link.get_text(" ", strip=True), match.group(1).replace("-", " "), classic, mantra


def parse_quotations(html: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for row in soup.select("tr.player-row"):
        player_id, name, team, classic, mantra = _identity(row)
        initial = _number(_cell(row, "c_qi"), "classic initial quotation")
        current = _number(_cell(row, "c_qa"), "classic current quotation")
        initial_mantra = _number(_cell(row, "m_qi"), "Mantra initial quotation")
        current_mantra = _number(_cell(row, "m_qa"), "Mantra current quotation")
        records.append({
            "Id": player_id, "R": classic, "RM": mantra, "Nome": name, "Squadra": team,
            "Qt.A": current, "Qt.I": initial, "Diff.": current - initial,
            "Qt.A M": current_mantra, "Qt.I M": initial_mantra, "Diff.M": current_mantra - initial_mantra,
            "FVM": _number(_cell(row, "c_fvm"), "classic FVM"),
            "FVM M": _number(_cell(row, "m_fvm"), "Mantra FVM"),
        })
    _validate_unique_records(records, "quotations")
    return records


def parse_statistics(html: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for row in soup.select("tr.player-row"):
        player_id, name, team, classic, mantra = _identity(row)
        penalty_parts = [part.strip() for part in _cell(row, "rig").split("/")]
        if len(penalty_parts) != 2:
            raise OfficialSnapshotError("Invalid penalty statistics in the public page.")
        penalties_scored = _number(penalty_parts[0], "penalties scored")
        penalties_taken = _number(penalty_parts[1], "penalties taken")
        records.append({
            "Id": player_id, "R": classic, "Rm": mantra, "Nome": name, "Squadra": team,
            "Pv": _number(_cell(row, "pg"), "appearances"),
            "Mv": _number(_cell(row, "mv"), "average rating"),
            "Fm": _number(_cell(row, "mfv"), "fantasy average"),
            "Gf": _number(_cell(row, "gol"), "goals"), "Gs": _number(_cell(row, "gs"), "goals conceded"),
            "Rp": _number(_cell(row, "rp"), "penalties saved"),
            "Rc": penalties_taken, "R+": penalties_scored, "R-": penalties_taken - penalties_scored,
            "Ass": _number(_cell(row, "ass"), "assists"),
            "Amm": _number(_cell(row, "amm"), "yellow cards"),
            "Esp": _number(_cell(row, "esp"), "red cards"),
            # The public summary has no own-goal column; zero is neutral until the official sheet is available.
            "Au": 0,
        })
    _validate_unique_records(records, "statistics")
    return records


def _validate_unique_records(records: list[dict[str, object]], label: str) -> None:
    if not records:
        raise OfficialSnapshotError(f"No {label} records were found.")
    ids = [int(record["Id"]) for record in records]
    if len(ids) != len(set(ids)):
        raise OfficialSnapshotError(f"The public {label} contain duplicate player IDs.")


def build_snapshot(season: str, quotations_html: str, stats_html: str) -> dict[str, object]:
    quotations = parse_quotations(quotations_html)
    statistics = parse_statistics(stats_html)
    quote_ids = {int(item["Id"]) for item in quotations}
    stat_ids = {int(item["Id"]) for item in statistics}
    if quote_ids != stat_ids:
        raise OfficialSnapshotError("Quotations and statistics do not contain the same players.")
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION, "season": season,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sources": {"quotations": QUOTATIONS_URL, "statistics": STATS_URL.format(season=season.replace("/", "-"))},
        "observed_matchdays": max((int(item["Pv"]) for item in statistics), default=0),
        "players": quotations, "statistics": statistics,
    }


def update_snapshot(path: Path, season: str) -> dict[str, object]:
    snapshot = build_snapshot(season, fetch_page(QUOTATIONS_URL), fetch_page(STATS_URL.format(season=season.replace("/", "-"))))
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        stable_keys = ("schema_version", "season", "observed_matchdays", "players", "statistics")
        if isinstance(existing, dict) and all(existing.get(key) == snapshot.get(key) for key in stable_keys):
            return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)
    return snapshot


def load_snapshot(path: Path, season: str) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OfficialSnapshotError("The official data snapshot is unreadable.") from error
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION or snapshot.get("season") != season:
        raise OfficialSnapshotError("The official data snapshot has an incompatible schema or season.")
    players = pd.DataFrame(snapshot.get("players", []))
    statistics = pd.DataFrame(snapshot.get("statistics", []))
    from .pipeline import LISTONE_COLUMNS, STATS_COLUMNS
    missing_players = LISTONE_COLUMNS - set(players.columns)
    missing_stats = STATS_COLUMNS - set(statistics.columns)
    if missing_players or missing_stats or players.Id.duplicated().any() or statistics.Id.duplicated().any():
        raise OfficialSnapshotError("The official data snapshot is incomplete or contains duplicate IDs.")
    if set(players.Id.astype(int)) != set(statistics.Id.astype(int)):
        raise OfficialSnapshotError("The official data snapshot has inconsistent player IDs.")
    return players, statistics, int(snapshot.get("observed_matchdays", 0))


def blend_current(baseline: float, current: float, appearances: float) -> float:
    """Shrink a small current-season sample toward the multi-season baseline."""
    appearances = max(float(appearances), 0.0)
    weight = appearances / (appearances + CURRENT_STATS_PRIOR_APPEARANCES)
    return (1.0 - weight) * float(baseline) + weight * float(current)

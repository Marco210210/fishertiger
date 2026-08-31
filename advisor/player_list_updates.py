"""Assisted, profile-scoped updates for the official Fantacalcio player list."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd
from bs4 import BeautifulSoup, Tag

from .pipeline import LISTONE_COLUMNS, normalize
from .generate import auction_dataset_path, dataset_manifest


PUBLIC_URL = "https://www.fantacalcio.it/quotazioni-fantacalcio"
MAX_PAGE_BYTES = 5_000_000
MAX_DETAILS = 1000
NUMERIC_COLUMNS = {"Qt.A", "Qt.I", "Diff.", "Qt.A M", "Qt.I M", "Diff.M", "FVM", "FVM M"}
PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SEASON = re.compile(r"(\d{4})/(\d{2}|\d{4})\Z")
FetchPage = Callable[[str], str]
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class PlayerListUpdateError(ValueError):
    """A public page, candidate workbook, or update transaction is invalid."""


class StalePlayerListUpdateError(PlayerListUpdateError):
    """A compare-and-swap precondition changed after review."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def season_years(season: str) -> tuple[int, int]:
    match = SEASON.fullmatch(season.strip())
    if not match:
        raise PlayerListUpdateError("The selected season must use YYYY/YY or YYYY/YYYY format.")
    start = int(match.group(1))
    raw_end = match.group(2)
    end = int(str(start)[:2] + raw_end) if len(raw_end) == 2 else int(raw_end)
    if end != start + 1:
        raise PlayerListUpdateError("The selected season must contain consecutive years.")
    return start, end


def provider_season_id(season: str) -> int:
    start, _ = season_years(season)
    provider_id = start - 2005
    if provider_id < 0:
        raise PlayerListUpdateError("The selected season predates the official provider archive.")
    return provider_id


def official_download_url(season: str) -> str:
    return f"https://www.fantacalcio.it/api/v1/Excel/prices/{provider_season_id(season)}/1"


def public_check_url(season: str | None = None) -> str:
    if season is not None:
        season_years(season)
    return PUBLIC_URL


def season_slug(season: str) -> str:
    start, end = season_years(season)
    return f"{start}-{str(end)[-2:]}"


def fetch_public_page(url: str) -> str:
    request = Request(url, headers={"User-Agent": "FantaAdvisor/1.0 (local update checker)"})
    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise PlayerListUpdateError(f"Fantacalcio returned HTTP {response.status}.")
            payload = response.read(MAX_PAGE_BYTES + 1)
            if len(payload) > MAX_PAGE_BYTES:
                raise PlayerListUpdateError("Fantacalcio returned an unexpectedly large page.")
            return payload.decode(response.headers.get_content_charset() or "utf-8", "replace")
    except PlayerListUpdateError:
        raise
    except Exception as error:
        raise PlayerListUpdateError("Fantacalcio could not be reached.") from error


def _number(value: str, field: str) -> float | int:
    cleaned = re.sub(r"[^0-9,.-]", "", value.strip()).replace(",", ".")
    try:
        number = float(cleaned)
    except ValueError as error:
        raise PlayerListUpdateError(f"The public player list contains an invalid {field} value.") from error
    return int(number) if number.is_integer() else number


def _field(row: Tag, names: tuple[str, ...], header_indexes: dict[str, int]) -> str:
    for name in names:
        value = row.get(f"data-{name}")
        if value:
            return str(value).strip()
        node = row.select_one(f".{name}, [data-field='{name}']")
        if node is not None:
            value = node.get("data-value") or node.get_text(" ", strip=True)
            if value:
                return str(value).strip()
    cells = row.find_all(["td", "th"], recursive=False)
    for name in names:
        index = header_indexes.get(name.lower())
        if index is not None and index < len(cells):
            return cells[index].get_text(" ", strip=True)
    return ""


def parse_public_players(html: str, season: str) -> list[dict[str, object]]:
    start, end = season_years(season)
    soup = BeautifulSoup(html, "html.parser")
    declaration_texts = [node.get_text(" ", strip=True) for node in soup.select("h1, #season option[selected]")]
    season_texts = [text for text in declaration_texts if re.search(r"\b\d{4}\D{1,3}\d{2,4}\b", text)]
    declarations = {
        f"{start}/{str(end)[-2:]}", f"{start}/{end}",
        f"{start}-{str(end)[-2:]}", f"{start}-{end}",
    }
    if not season_texts or any(not any(value in text for value in declarations) for text in season_texts):
        raise PlayerListUpdateError("The Fantacalcio page does not declare the requested season.")

    records: list[dict[str, object]] = []
    for row in soup.select("tr.player-row"):
        link = row.select_one("a[href*='/']")
        href = str(link.get("href", "")) if link else ""
        id_match = re.search(r"(?:/|=)(\d+)(?:[/#?&]|$)", href)
        if not id_match:
            raise PlayerListUpdateError("A public player row has no recognizable player ID.")
        table = row.find_parent("table")
        header_indexes: dict[str, int] = {}
        if table is not None:
            headers = table.select("thead th") or table.find_all("th")
            header_indexes = {re.sub(r"\s+", "", cell.get_text(" ", strip=True)).lower(): index for index, cell in enumerate(headers)}
        role_node = row.select_one(".player-role-classic .role")
        role = str(role_node.get("data-value", "") if role_node else "") or _field(row, ("role", "ruolo", "r"), header_indexes)
        role = role.upper()
        name = _field(row, ("name", "nome", "player-name"), header_indexes) or (link.get_text(" ", strip=True) if link else "")
        team_match = re.search(r"/squadre/([^/]+)/", href)
        team = team_match.group(1).replace("-", " ") if team_match else _field(row, ("player-team", "team", "squadra"), header_indexes)
        quotation = _field(row, ("player-classic-current-price", "quotation", "quotazione", "qt.a", "qta"), header_indexes)
        fvm = _field(row, ("player-classic-fvm", "fvm"), header_indexes)
        if role not in {"P", "D", "C", "A"} or not name or not team or not quotation or not fvm:
            raise PlayerListUpdateError("A public player row is missing role, name, team, quotation, or FVM.")
        records.append({
            "Id": int(id_match.group(1)), "R": role, "Nome": name, "Squadra": team,
            "Qt.A": _number(quotation, "quotation"), "FVM": _number(fvm, "FVM"),
        })
    if not records:
        raise PlayerListUpdateError("No player records were found on the Fantacalcio page.")
    if len({record["Id"] for record in records}) != len(records):
        raise PlayerListUpdateError("The Fantacalcio page contains duplicate player IDs.")
    return records


def canonical_player_hash(records: list[dict[str, object]]) -> str:
    canonical = json.dumps(sorted(records, key=lambda item: int(item["Id"])), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_source_path(path: str | Path) -> Path:
    declared = Path(path)
    candidates = [declared] if declared.is_absolute() else [declared, Path.cwd() / declared, Path(__file__).resolve().parents[1] / declared]
    return next((candidate for candidate in candidates if candidate.is_file()), declared)


def _integral_ids(series: pd.Series, label: str, *, unique: bool) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().any() or (numeric % 1 != 0).any() or (unique and numeric.duplicated().any()):
        qualifier = "non-null, unique integral" if unique else "non-null integral"
        raise PlayerListUpdateError(f"{label}.Id must contain {qualifier} values.")
    return numeric.astype("int64")


def read_player_list(path: Path, expected_season: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    try:
        with pd.ExcelFile(path, engine="openpyxl") as workbook:
            if not {"Tutti", "Ceduti"}.issubset(workbook.sheet_names):
                raise PlayerListUpdateError("The workbook must contain Tutti and Ceduti sheets.")
            banner = pd.read_excel(workbook, sheet_name="Tutti", header=None, nrows=1)
            players = pd.read_excel(workbook, sheet_name="Tutti", header=1)
            ceduti = pd.read_excel(workbook, sheet_name="Ceduti", header=1)
    except PlayerListUpdateError:
        raise
    except Exception as error:
        raise PlayerListUpdateError("The uploaded file is not a readable XLSX workbook.") from error
    if expected_season is not None:
        start, end = season_years(expected_season)
        banner_text = " ".join(str(value) for value in banner.iloc[0].dropna()) if not banner.empty else ""
        normalized_banner = re.sub(r"[^0-9]+", " ", banner_text).strip()
        valid_seasons = {f"{start} {str(end)[-2:]}", f"{start} {end}"}
        if not any(re.search(rf"(?<!\d){re.escape(value)}(?!\d)", normalized_banner) for value in valid_seasons):
            raise PlayerListUpdateError(f"The workbook banner does not declare season {season_slug(expected_season)}.")
    missing = LISTONE_COLUMNS - set(players.columns)
    if missing:
        raise PlayerListUpdateError(f"Tutti is missing required columns: {sorted(missing)}.")
    if "Id" not in ceduti:
        raise PlayerListUpdateError("Ceduti is missing required column: Id.")
    players = players.copy()
    ceduti = ceduti.copy()
    players["Id"] = _integral_ids(players["Id"], "Tutti", unique=True)
    ceduti = ceduti.loc[ceduti["Id"].notna()].copy()
    ceduti["Id"] = _integral_ids(ceduti["Id"], "Ceduti", unique=True)
    roles = players["R"].astype(str).str.strip().str.upper()
    if players["R"].isna().any() or not roles.isin({"P", "D", "C", "A"}).all():
        raise PlayerListUpdateError("Tutti.R must contain only classic roles P, D, C, or A.")
    players["R"] = roles
    for column in ("RM", "Nome", "Squadra"):
        if players[column].isna().any() or not players[column].astype(str).str.strip().all():
            raise PlayerListUpdateError(f"Tutti.{column} must contain non-empty values.")
    for column in NUMERIC_COLUMNS:
        numeric = pd.to_numeric(players[column], errors="coerce")
        if numeric.isna().any():
            raise PlayerListUpdateError(f"Tutti.{column} must contain numeric values.")
        players[column] = numeric
    return players, ceduti


def _records(frame: pd.DataFrame) -> dict[int, dict[str, object]]:
    columns = ["Id", "R", "RM", "Nome", "Squadra", *sorted(NUMERIC_COLUMNS)]
    return {int(row["Id"]): {column: _json_value(row[column]) for column in columns} for _, row in frame[columns].iterrows()}


def _json_value(value: object) -> object:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    return value


def semantic_diff(active_path: Path, candidate_path: Path, *, detail_limit: int = MAX_DETAILS) -> dict[str, object]:
    active, active_ceduti = read_player_list(active_path)
    candidate, candidate_ceduti = read_player_list(candidate_path)
    old = _records(active)
    new = _records(candidate)
    old_ceduti = set(active_ceduti["Id"].astype(int))
    new_ceduti = set(candidate_ceduti["Id"].astype(int))
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    field_groups = {
        "role": ("R", "RM"),
        "name": ("Nome",),
        "team": ("Squadra",),
        "quotation": ("Qt.A", "Qt.I", "Diff.", "Qt.A M", "Qt.I M", "Diff.M"),
        "fvm": ("FVM", "FVM M"),
    }
    changes: list[dict[str, object]] = []
    counts = {label: 0 for label in field_groups}
    for player_id in sorted(set(old) & set(new)):
        fields = {}
        for label, columns in field_groups.items():
            changed_columns = [column for column in columns if old[player_id][column] != new[player_id][column]]
            if changed_columns:
                before = {column: old[player_id][column] for column in changed_columns}
                after = {column: new[player_id][column] for column in changed_columns}
                fields[label] = {
                    "before": next(iter(before.values())) if len(before) == 1 else before,
                    "after": next(iter(after.values())) if len(after) == 1 else after,
                }
                counts[label] += 1
        if fields:
            changes.append({"id": player_id, "name": new[player_id]["Nome"], "fields": fields})
    ceduti_added = sorted(new_ceduti - old_ceduti)
    ceduti_removed = sorted(old_ceduti - new_ceduti)
    summary = {
        "added": len(added), "removed": len(removed), "ceduti_added": len(ceduti_added),
        "ceduti_removed": len(ceduti_removed), **counts, "changed_players": len(changes),
    }
    return {
        "summary": summary,
        "details": {
            "added": [{"id": value, "name": new[value]["Nome"]} for value in added[:detail_limit]],
            "removed": [{"id": value, "name": old[value]["Nome"]} for value in removed[:detail_limit]],
            "ceduti_added": ceduti_added[:detail_limit], "ceduti_removed": ceduti_removed[:detail_limit],
            "changed": changes[:detail_limit], "truncated": any(len(values) > detail_limit for values in (added, removed, ceduti_added, ceduti_removed, changes)),
        },
    }


def public_check(profile: object, fetcher: FetchPage = fetch_public_page) -> dict[str, object]:
    season = profile.season.season
    source = next((item for item in profile.current_sources if item.name == "player_list"), None)
    if source is None:
        raise PlayerListUpdateError("The profile does not declare a player_list source.")
    active, ceduti = read_player_list(resolve_source_path(source.path))
    html = fetcher(public_check_url(season))
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        raise PlayerListUpdateError("Fantacalcio returned an invalid or unexpectedly large page.")
    public = parse_public_players(html, season)
    current = _records(active.loc[~active["Id"].isin(set(ceduti["Id"]))])
    remote = {int(item["Id"]): item for item in public}
    added = sorted(set(remote) - set(current))
    removed = sorted(set(current) - set(remote))
    changed = []
    for player_id in sorted(set(remote) & set(current)):
        fields = [field for field in ("R", "Nome", "Qt.A", "FVM") if remote[player_id][field] != current[player_id][field]]
        if normalize(remote[player_id]["Squadra"]) != normalize(current[player_id]["Squadra"]):
            fields.append("Squadra")
        if fields:
            changed.append({"id": player_id, "name": remote[player_id]["Nome"], "fields": fields})
    state = "unchanged" if not added and not removed and not changed else "changed"
    checked_at = datetime.now(timezone.utc).isoformat()
    return {
        "source": "fantacalcio", "season": season, "state": state,
        "source_url": public_check_url(season), "download_url": official_download_url(season),
        "checked_at": checked_at, "content_hash": canonical_player_hash(public),
        "summary": {"public_players": len(public), "active_players": len(current), "added": len(added), "removed": len(removed), "changed": len(changed)},
        "details": {"added": added[:MAX_DETAILS], "removed": removed[:MAX_DETAILS], "changed": changed[:MAX_DETAILS], "truncated": any(len(value) > MAX_DETAILS for value in (added, removed, changed))},
    }


def candidate_directory(root: Path, profile_id: str, season: str) -> Path:
    if not PROFILE_ID.fullmatch(profile_id):
        raise PlayerListUpdateError("The candidate profile or season path is invalid.")
    return root / profile_id / season_slug(season) / "fantacalcio-listone"


@contextmanager
def profile_transaction(root: Path, profile_id: str):
    key = (root / profile_id).resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_candidate(root: Path, profile_id: str, season: str, payload: bytes, filename: str) -> dict[str, object]:
    directory = candidate_directory(root, profile_id, season)
    directory.mkdir(parents=True, exist_ok=True)
    with profile_transaction(root, profile_id):
        with tempfile.NamedTemporaryFile("wb", suffix=".xlsx", dir=directory, delete=False) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        try:
            players, ceduti = read_player_list(temporary, season)
            content_hash = file_sha256(temporary)
            candidate_name = f"candidate-{content_hash}.xlsx"
            metadata = {
                "schema_version": 1, "candidate_hash": content_hash,
                "candidate_file": candidate_name,
                "uploaded_at": datetime.now(timezone.utc).isoformat(), "filename": Path(filename).name,
                "size_bytes": len(payload), "row_count": len(players), "ceduti_count": len(ceduti),
            }
            candidate_path = directory / candidate_name
            created = not candidate_path.exists()
            if created:
                temporary.replace(candidate_path)
            else:
                temporary.unlink()
            try:
                _write_json(directory / "metadata.json", metadata)
            except Exception:
                if created:
                    candidate_path.unlink(missing_ok=True)
                raise
            for old_candidate in directory.glob("candidate-*.xlsx"):
                if old_candidate != candidate_path:
                    old_candidate.unlink(missing_ok=True)
            response_metadata = {key: value for key, value in metadata.items() if key != "candidate_file"}
            return {"source": "fantacalcio", "profile_id": profile_id, "season": season, "state": "candidate_ready", **response_metadata}
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        temporary = Path(handle.name)
    temporary.replace(path)


def _candidate(root: Path, profile_id: str, season: str) -> tuple[Path, dict[str, object]] | None:
    directory = candidate_directory(root, profile_id, season)
    metadata_path = directory / "metadata.json"
    if not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlayerListUpdateError("Candidate metadata is missing or invalid.") from error
    candidate_name = metadata.get("candidate_file") if isinstance(metadata, dict) else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema_version") != 1
        or not isinstance(metadata.get("candidate_hash"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["candidate_hash"])
        or not isinstance(metadata.get("uploaded_at"), str)
        or not isinstance(metadata.get("row_count"), int)
        or not isinstance(metadata.get("ceduti_count"), int)
        or not isinstance(metadata.get("size_bytes"), int)
        or candidate_name != f"candidate-{metadata.get('candidate_hash')}.xlsx"
        or Path(str(candidate_name)).name != candidate_name
    ):
        raise PlayerListUpdateError("The candidate failed its integrity check.")
    candidate = directory / candidate_name
    if (
        not candidate.is_file()
        or metadata.get("candidate_hash") != file_sha256(candidate)
        or metadata.get("size_bytes") != candidate.stat().st_size
    ):
        raise PlayerListUpdateError("The candidate failed its integrity check.")
    players, ceduti = read_player_list(candidate, season)
    if metadata.get("row_count") != len(players) or metadata.get("ceduti_count") != len(ceduti):
        raise PlayerListUpdateError("The candidate metadata does not match the workbook.")
    return candidate, metadata


def active_player_list_path(profile: object) -> Path:
    source = next((item for item in profile.current_sources if item.name == "player_list"), None)
    if source is None:
        raise PlayerListUpdateError("The profile does not declare a player_list source.")
    return resolve_source_path(source.path)


def active_starters_path(profile: object) -> Path:
    source = next((item for item in profile.current_sources if item.name == "starters"), None)
    if source is None:
        raise PlayerListUpdateError("The profile does not declare a starters source.")
    return resolve_source_path(source.path)


def reconcile_departed_starters(starters_path: Path, candidate_path: Path) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    try:
        starters = pd.read_csv(starters_path, dtype=str, keep_default_na=False)
    except Exception as error:
        raise PlayerListUpdateError("The current starters CSV is unavailable or invalid.") from error
    required = {"squadra", "nome", "id_fantacalcio"}
    if not required.issubset(starters.columns):
        raise PlayerListUpdateError("The current starters CSV does not have the expected columns.")
    _, ceduti = read_player_list(candidate_path)
    ceduti_by_id = {int(row.Id): row for _, row in ceduti.iterrows()}
    ceduti_by_identity: dict[tuple[str, str], list[pd.Series]] = {}
    for _, row in ceduti.iterrows():
        team = row.get("Squadra")
        name = row.get("Nome")
        if pd.notna(team) and pd.notna(name):
            ceduti_by_identity.setdefault((normalize(team), normalize(name)), []).append(row)

    removed: list[dict[str, object]] = []
    removed_indexes: list[int] = []
    for index, row in starters.iterrows():
        raw_id = str(row.id_fantacalcio).strip()
        declared_id = int(raw_id) if raw_id.isdigit() else None
        departed = ceduti_by_id.get(declared_id) if declared_id is not None else None
        method = "authoritative_id"
        if departed is None and declared_id is None:
            candidates = ceduti_by_identity.get((normalize(row.squadra), normalize(row.nome)), [])
            if len(candidates) == 1:
                departed = candidates[0]
                method = "exact_identity"
        if departed is not None:
            removed_indexes.append(index)
            removed.append({
                "id": int(departed.Id), "name": str(row.nome).strip(), "team": str(row.squadra).strip(),
                "match_method": method,
            })
    return starters.drop(index=removed_indexes).reset_index(drop=True), removed


def persisted_or_inline_profile(profiles_dir: Path, profile: object, profile_loader: Callable[[dict[str, object]], object]) -> object:
    path = profiles_dir / f"{profile.profile_id}.json"
    if not path.exists():
        return profile
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        persisted = profile_loader(value)
    except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
        raise PlayerListUpdateError("The persisted profile is invalid or unreadable.") from error
    if persisted.profile_id != profile.profile_id:
        raise PlayerListUpdateError("The persisted profile ID does not match its file name.")
    return persisted


def candidate_status(root: Path, profile: object) -> dict[str, object]:
    season = profile.season.season
    with profile_transaction(root, profile.profile_id):
        active_path = active_player_list_path(profile)
        starters_path = active_starters_path(profile)
        active_hash = file_sha256(active_path)
        starters_hash = file_sha256(starters_path)
        common = {
            "source": "fantacalcio", "profile_id": profile.profile_id, "season": season,
            "profile_hash": profile.configuration_hash, "active_hash": active_hash, "starters_hash": starters_hash,
        }
        stored = _candidate(root, profile.profile_id, season)
        if stored is None:
            return {**common, "state": "never_uploaded", "candidate_hash": None, "summary": {}, "details": {}}
        candidate, metadata = stored
        difference = semantic_diff(active_path, candidate)
        _, departed_starters = reconcile_departed_starters(starters_path, candidate)
        summary = {**difference["summary"], "starters_removed": len(departed_starters)}
        details = {**difference["details"], "starters_removed": departed_starters}
        changed = any(value for key, value in summary.items() if key != "changed_players")
        return {
            **common,
            "state": "candidate_ready" if changed else "unchanged", "candidate_hash": metadata["candidate_hash"],
            "uploaded_at": metadata["uploaded_at"], "row_count": metadata["row_count"], "ceduti_count": metadata["ceduti_count"],
            "summary": summary, "details": details,
        }


def apply_candidate(
    root: Path, uploads_dir: Path, profiles_dir: Path, profile: object, expected_candidate_hash: str,
    expected_profile_hash: str, expected_active_hash: str, expected_starters_hash: str,
    datasets_dir: Path, generator: object, profile_loader: Callable[[dict[str, object]], object],
    generate: Callable[..., dict[str, object]], profile_transform: Callable[[object], object] | None = None,
) -> dict[str, object]:
    with profile_transaction(root, profile.profile_id):
        if profile.configuration_hash != expected_profile_hash:
            raise StalePlayerListUpdateError("stale_profile", "The submitted profile changed after player-list status was reviewed.")
        active_profile = persisted_or_inline_profile(profiles_dir, profile, profile_loader)
        if profile_transform is not None:
            active_profile = profile_transform(active_profile)
        if active_profile.configuration_hash != expected_profile_hash:
            raise StalePlayerListUpdateError("stale_profile", "The persisted profile changed after player-list status was reviewed.")
        active_path = active_player_list_path(active_profile)
        if file_sha256(active_path) != expected_active_hash:
            raise StalePlayerListUpdateError("stale_active_source", "The active player list changed after status was reviewed.")
        starters_path = active_starters_path(active_profile)
        if file_sha256(starters_path) != expected_starters_hash:
            raise StalePlayerListUpdateError("stale_starters_source", "The starters CSV changed after player-list status was reviewed.")
        stored = _candidate(root, active_profile.profile_id, active_profile.season.season)
        if stored is None:
            raise PlayerListUpdateError("Upload a candidate player list before applying it.")
        candidate, metadata = stored
        if not expected_candidate_hash or expected_candidate_hash != metadata["candidate_hash"]:
            raise StalePlayerListUpdateError("stale_candidate", "The candidate changed after status was reviewed.")
        target = uploads_dir / active_profile.profile_id / "current_sources" / f"player_list-{expected_candidate_hash}.xlsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
            try:
                shutil.copyfile(candidate, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        cleaned_starters, departed_starters = reconcile_departed_starters(starters_path, candidate)
        starters_target = None
        if departed_starters:
            starters_payload = cleaned_starters.to_csv(index=False).encode("utf-8")
            starters_content_hash = hashlib.sha256(starters_payload).hexdigest()
            starters_target = target.parent / f"starters-{starters_content_hash}.csv"
            if not starters_target.exists():
                with tempfile.NamedTemporaryFile("wb", dir=starters_target.parent, delete=False) as handle:
                    handle.write(starters_payload)
                    temporary = Path(handle.name)
                try:
                    temporary.replace(starters_target)
                finally:
                    temporary.unlink(missing_ok=True)
        value = active_profile.to_dict()
        replaced = False
        for source in value["current_sources"]:
            if source["name"] == "player_list":
                source["path"] = target.as_posix()
                source["format"] = "xlsx"
                replaced = True
            elif source["name"] == "starters" and starters_target is not None:
                source["path"] = starters_target.as_posix()
                source["format"] = "csv"
        if not replaced:
            raise PlayerListUpdateError("The profile does not declare a player_list source.")
        updated = profile_loader(value)
        datasets_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix="player-list-generation-", dir=datasets_dir.parent))
        relative_output = Path(auction_dataset_path(updated)).parent
        staged_output = staging_root / relative_output
        real_output = datasets_dir / relative_output
        backup_output = real_output.with_name(f".{real_output.name}.backup-{uuid.uuid4().hex}")
        promoted = False
        had_previous = False
        try:
            result = generate(updated, staging_root, generator=generator)
            if not staged_output.is_dir():
                raise PlayerListUpdateError("Generation did not create the expected profile and season output.")
            real_output.parent.mkdir(parents=True, exist_ok=True)
            if real_output.exists():
                real_output.replace(backup_output)
                had_previous = True
            staged_output.replace(real_output)
            promoted = True
            try:
                _write_json(profiles_dir / f"{updated.profile_id}.json", updated.to_dict())
            except Exception:
                shutil.rmtree(real_output, ignore_errors=True)
                if had_previous:
                    backup_output.replace(real_output)
                promoted = False
                raise
            if had_previous:
                shutil.rmtree(backup_output, ignore_errors=True)
            return {
                **result, "profile": updated.to_dict(), "candidate_hash": expected_candidate_hash,
                "starters_removed": departed_starters,
                "dataset_manifest": dataset_manifest(datasets_dir),
                "dataset_path": (relative_output / "auction_data.json").as_posix() if (real_output / "auction_data.json").is_file() else None,
            }
        finally:
            if not promoted and had_previous and backup_output.exists() and not real_output.exists():
                backup_output.replace(real_output)
            shutil.rmtree(staging_root, ignore_errors=True)

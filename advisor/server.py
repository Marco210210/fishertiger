"""Small local HTTP API for editing profiles and triggering data generation.

Generator integration is deliberately injected: this module has no dependency on
the data pipeline and does not select a generator implementation itself.
"""
from __future__ import annotations

import argparse
import base64
import hmac
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from zipfile import BadZipFile
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .generate import (
    PipelineGenerator,
    ProfileRequestError,
    auction_dataset_path,
    dataset_manifest,
    generate_dataset,
    load_profile,
    resolve_profile,
)
from .auth import AccountError, CredentialStore
from .freshness import dataset_configuration_hash, simulation_configuration_hash, source_fingerprints
from .fantalab_live import (
    FantaLabError,
    fantalab_credentials_available,
    live_snapshot,
    personal_credentials_path,
    resolve_fantalab_id_token,
    store_fantalab_refresh_token,
)
from .scout import load_scout_snapshot, load_scout_snapshot_claude
from .scout_refresh import refresh_official_scout
from .league_calendar import build_legacy_calendar_template, preprocess_legacy_calendar
from .simulation import RosterValidationError
from .player_list_updates import (
    FetchPage as PlayerListFetchPage,
    PlayerListUpdateError,
    StalePlayerListUpdateError,
    apply_candidate,
    candidate_status,
    fetch_public_page,
    persisted_or_inline_profile,
    profile_transaction,
    public_check,
    season_slug,
    store_candidate,
)
from .sosfanta_updates import (
    FetchPage,
    SosFantaError,
    accept_latest,
    build_bundle,
    check_updates,
    fetch_page,
    stored_status,
)
from .sosfanta_set_piece_updates import (
    accept_latest as accept_latest_set_pieces,
    build_bundle as build_set_piece_bundle,
    check_updates as check_set_piece_updates,
    stored_status as stored_set_piece_status,
)
from .sosfanta_formations_updates import (
    accept_latest as accept_latest_formations,
    build_bundle as build_formations_bundle,
    check_updates as check_formation_updates,
    stored_status as stored_formation_status,
)
from .sosfanta_goalkeeper_updates import (
    accept_latest as accept_latest_goalkeepers,
    apply_update as apply_goalkeeper_update,
    check_updates as check_goalkeeper_updates,
    stored_status as stored_goalkeeper_status,
)


def profile_response(profile: Any) -> dict[str, Any]:
    """The profile as the browser needs it: stored fields plus derived hashes."""
    return {
        **profile.to_dict(),
        "configuration_hash": profile.configuration_hash,
        "dataset_configuration_hash": dataset_configuration_hash(profile),
        "simulation_configuration_hash": simulation_configuration_hash(profile),
    }


MAX_BODY_BYTES = 1_000_000
MAX_UPLOAD_BYTES = 50_000_000
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SOURCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SOURCE_GROUPS = {"current_sources", "history_sources"}
FIXED_SOURCE_SUFFIXES = {
    "current_sources": {
        "player_list": ".xlsx",
        "serie_a_calendar": ".xlsx",
        "teams": ".csv",
        "starters": ".csv",
        "set_pieces": ".csv",
        "auction_guide": ".csv",
        "official_snapshot": ".json",
        "league_calendar": ".xlsx",
    },
    "history_sources": {
        "stats_2025_26": ".xlsx",
        "stats_2024_25": ".xlsx",
        "stats_2023_24": ".xlsx",
    },
}
VITE_ORIGIN = re.compile(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?\Z")
ProfileLoader = Callable[[dict[str, Any]], Any]
SimulationRunner = Callable[[Any, Path, int, int, dict[str, list[int]] | None], dict[str, Any]]
FantaLabReader = Callable[[dict[str, Any]], dict[str, Any]]


class LocalApiServer(ThreadingHTTPServer):
    """HTTP server state with filesystem locations and an optional generator."""

    def __init__(
        self,
        address: tuple[str, int] = ("127.0.0.1", 8000),
        *,
        profiles_dir: Path | str = Path("config/profiles"),
        datasets_dir: Path | str = Path("data/processed"),
        uploads_dir: Path | str = Path("data/uploads"),
        updates_dir: Path | str = Path("data/updates"),
        auction_states_dir: Path | str = Path("data/auction-states"),
        default_profile_path: Path | str = Path("config/default_profile.json"),
        raw_dir: Path | str = Path("data/raw"),
        static_dir: Path | str | None = None,
        auth_username: str | None = None,
        auth_password: str | None = None,
        auth_file: Path | str | None = None,
        generator: PipelineGenerator | None = None,
        simulator: SimulationRunner | None = None,
        profile_loader: ProfileLoader = load_profile,
        update_fetcher: FetchPage = fetch_page,
        formations_fetcher: FetchPage = fetch_page,
        set_piece_fetcher: FetchPage = fetch_page,
        goalkeeper_fetcher: FetchPage = fetch_page,
        player_list_fetcher: PlayerListFetchPage = fetch_public_page,
        fantalab_reader: FantaLabReader | None = None,
    ) -> None:
        self.profiles_dir = Path(profiles_dir)
        self.datasets_dir = Path(datasets_dir)
        self.uploads_dir = Path(uploads_dir)
        self.updates_dir = Path(updates_dir)
        self.auction_states_dir = Path(auction_states_dir)
        self.default_profile_path = Path(default_profile_path)
        self.raw_dir = Path(raw_dir)
        self.static_dir = Path(static_dir).resolve() if static_dir is not None else None
        if bool(auth_username) != bool(auth_password):
            raise ValueError("auth_username and auth_password must be configured together")
        self.auth_username = auth_username
        self.auth_password = auth_password
        self.credential_store = (
            CredentialStore(
                auth_file,
                bootstrap_username=auth_username,
                bootstrap_password=auth_password,
            )
            if auth_file is not None
            else None
        )
        self.generator = generator
        self.simulator = simulator or _simulate_current_dataset
        self.profile_loader = profile_loader
        self.update_fetcher = update_fetcher
        self.formations_fetcher = formations_fetcher
        self.set_piece_fetcher = set_piece_fetcher
        self.goalkeeper_fetcher = goalkeeper_fetcher
        self.player_list_fetcher = player_list_fetcher
        self.fantalab_reader = fantalab_reader
        super().__init__(address, LocalApiHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Stay quiet when a client drops the connection mid-response."""
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class LocalApiHandler(BaseHTTPRequestHandler):
    server: LocalApiServer

    def do_OPTIONS(self) -> None:
        self._send_json(HTTPStatus.NO_CONTENT, None)

    def do_GET(self) -> None:
        path = self._path()
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
        elif not self._authorize():
            return
        elif path == "/api/auth/session":
            self._auth_session()
        elif path == "/api/auth/users":
            self._auth_users()
        elif path == "/api/profiles":
            self._profile_index()
        elif path == "/api/default-profile":
            self._default_profile()
        elif path == "/api/fantalab/status":
            self._fantalab_status()
        elif path.startswith("/api/auction-state/"):
            self._get_auction_state(path.removeprefix("/api/auction-state/"))
        elif path.startswith("/api/scout/"):
            self._scout_snapshot(path.removeprefix("/api/scout/"))
        elif path.startswith("/api/scout-claude/"):
            self._scout_snapshot_claude(path.removeprefix("/api/scout-claude/"))
        elif path.startswith("/api/profiles/"):
            self._get_profile(path.removeprefix("/api/profiles/"))
        elif path == "/api/datasets/manifest":
            self._dataset_manifest()
        elif path == "/api/templates/league-calendar.xlsx":
            self._league_calendar_template()
        elif path.startswith("/api/datasets/"):
            self._get_dataset(path.removeprefix("/api/datasets/"))
        elif self.server.static_dir is not None and not path.startswith("/api/"):
            self._serve_static(path)
        else:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "The requested endpoint does not exist.")

    def do_PUT(self) -> None:
        if not self._authorize():
            return
        path = self._path()
        if path == "/api/auth/password":
            self._change_password()
        elif path == "/api/fantalab/credentials":
            self._put_fantalab_credentials()
        elif path.startswith("/api/auction-state/"):
            self._put_auction_state(path.removeprefix("/api/auction-state/"))
        elif path.startswith("/api/updates/player-list/candidate/"):
            self._put_player_list_candidate(path.removeprefix("/api/updates/player-list/candidate/"))
        elif path.startswith("/api/uploads/"):
            self._put_upload(path.removeprefix("/api/uploads/"))
        elif path.startswith("/api/profiles/"):
            self._put_profile(path.removeprefix("/api/profiles/"))
        else:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "The requested endpoint does not exist.")

    def do_DELETE(self) -> None:
        if not self._authorize():
            return
        path = self._path()
        if path.startswith("/api/auth/users/"):
            self._delete_auth_user(path.removeprefix("/api/auth/users/"))
        elif path == "/api/fantalab/credentials":
            self._delete_fantalab_credentials()
        elif path.startswith("/api/profiles/"):
            self._delete_profile(path.removeprefix("/api/profiles/"))
        else:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "The requested endpoint does not exist.")

    def do_POST(self) -> None:
        if not self._authorize():
            return
        if self._path() == "/api/updates/all/run":
            self._update_all()
            return
        if self._path() == "/api/scout/refresh":
            self._refresh_scout()
            return
        if self._path() == "/api/auth/users":
            self._create_auth_user()
            return
        if self._path() == "/api/updates/player-list/check":
            self._check_player_list_updates()
            return
        if self._path() == "/api/updates/player-list/status":
            self._player_list_status()
            return
        if self._path() == "/api/updates/player-list/apply":
            self._apply_player_list_candidate()
            return
        if self._path() == "/api/updates/sosfanta/check":
            self._check_sosfanta_updates()
            return
        if self._path() == "/api/updates/sosfanta/status":
            self._sosfanta_status()
            return
        if self._path() == "/api/updates/sosfanta/accept":
            self._accept_sosfanta_updates()
            return
        if self._path() == "/api/updates/sosfanta/bundle":
            self._sosfanta_bundle()
            return
        if self._path() == "/api/updates/sosfanta-formations/check":
            self._check_formation_updates()
            return
        if self._path() == "/api/updates/sosfanta-formations/status":
            self._formation_status()
            return
        if self._path() == "/api/updates/sosfanta-formations/accept":
            self._accept_formation_updates()
            return
        if self._path() == "/api/updates/sosfanta-formations/bundle":
            self._formation_bundle()
            return
        if self._path() == "/api/updates/sosfanta-goalkeepers/check":
            self._check_goalkeeper_updates()
            return
        if self._path() == "/api/updates/sosfanta-goalkeepers/status":
            self._goalkeeper_status()
            return
        if self._path() == "/api/updates/sosfanta-goalkeepers/accept":
            self._accept_goalkeeper_updates()
            return
        if self._path() == "/api/updates/sosfanta-goalkeepers/apply":
            self._apply_goalkeeper_updates()
            return
        if self._path() == "/api/updates/sosfanta-set-pieces/check":
            self._check_set_piece_updates()
            return
        if self._path() == "/api/updates/sosfanta-set-pieces/status":
            self._set_piece_status()
            return
        if self._path() == "/api/updates/sosfanta-set-pieces/accept":
            self._accept_set_piece_updates()
            return
        if self._path() == "/api/updates/sosfanta-set-pieces/bundle":
            self._set_piece_bundle()
            return
        if self._path() == "/api/sources/status":
            self._source_status()
            return
        if self._path() == "/api/fantalab/snapshot":
            self._fantalab_snapshot()
            return
        if self._path() == "/api/simulate":
            self._simulate()
            return
        if self._path() != "/api/generate":
            self._error(HTTPStatus.NOT_FOUND, "not_found", "The requested endpoint does not exist.")
            return
        request = self._read_json_object()
        if request is None:
            return
        try:
            profile = resolve_profile(request, self.server.profiles_dir, profile_loader=self.server.profile_loader)
            with profile_transaction(self.server.updates_dir, profile.profile_id):
                profile = resolve_profile(request, self.server.profiles_dir, profile_loader=self.server.profile_loader)
                profile = self._derive_calendar_participants(profile)
                result = generate_dataset(profile, self.server.datasets_dir, generator=self.server.generator)
        except ProfileRequestError as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_profile", str(error))
            return
        except (OSError, ValueError) as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_source_data", str(error))
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "generation_failed", "Generation failed.")
            return
        else:
            self._send_json(HTTPStatus.OK, result)

    def _simulate(self) -> None:
        request = self._read_json_object()
        if request is None:
            return
        try:
            profile = resolve_profile(request, self.server.profiles_dir, profile_loader=self.server.profile_loader)
            profile = self._derive_calendar_participants(profile)
        except ProfileRequestError as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_profile", str(error))
            return
        except (OSError, ValueError) as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_source_data", str(error))
            return
        iterations = request.get("iterations", 1000)
        seed = request.get("seed", 202627)
        if isinstance(iterations, bool) or not isinstance(iterations, int) or not 100 <= iterations <= 50000:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_iterations", "Iterations must be an integer between 100 and 50000.")
            return
        if isinstance(seed, bool) or not isinstance(seed, int):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_seed", "Seed must be an integer.")
            return
        roster_mode = request.get("roster_mode", "sample")
        if roster_mode not in {"sample", "auction"}:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_roster_mode", "roster_mode must be 'sample' or 'auction'.")
            return
        rosters = request.get("rosters")
        if roster_mode == "auction":
            if not isinstance(rosters, dict):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_rosters", "Auction simulation requires a roster object.")
                return
            if any(not isinstance(team, str) or not isinstance(roster, list) or any(isinstance(player_id, bool) or not isinstance(player_id, int) for player_id in roster) for team, roster in rosters.items()):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_rosters", "Rosters must map team names to arrays of integer player IDs.")
                return
        elif "rosters" in request:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_rosters", "Sample simulation does not accept custom rosters.")
            return
        try:
            output_dir = self.server.datasets_dir / profile.profile_id / profile.season.season.replace("/", "-")
            with profile_transaction(self.server.updates_dir, profile.profile_id):
                result = self.server.simulator(profile, output_dir, iterations, seed, rosters if roster_mode == "auction" else None)
        except RosterValidationError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_rosters", str(error))
            return
        except (FileNotFoundError, ValueError) as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_source_data", str(error))
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "simulation_failed", "Simulation failed.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _fantalab_status(self) -> None:
        """Report capabilities without ever returning the configured credential."""
        if not self._require_fantalab_admin():
            return
        personal = fantalab_credentials_available(self._personal_fantalab_path())
        shared = bool(
            os.environ.get("FISHERTIGER_FANTALAB_REFRESH_TOKEN")
            or os.environ.get("FISHERTIGER_FANTALAB_TOKEN")
        )
        self._send_json(
            HTTPStatus.OK,
            {
                "available": True,
                "read_only": True,
                "token_configured": personal or shared,
                "personal_token_configured": personal,
                "shared_token_configured": shared,
            },
        )

    def _personal_fantalab_path(self) -> Path:
        username = (self._authenticated_user or {}).get("username") or "local"
        return personal_credentials_path(self.server.updates_dir, str(username))

    def _put_fantalab_credentials(self) -> None:
        if not self._require_fantalab_admin():
            return
        request = self._read_json_object()
        if request is None:
            return
        api_key = os.environ.get("FISHERTIGER_FANTALAB_FIREBASE_API_KEY")
        if not api_key:
            self._error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "fantalab_auth_unavailable",
                "Il server non ha la chiave pubblica Firebase necessaria per collegare account FantaLab.",
            )
            return
        try:
            store_fantalab_refresh_token(
                request.get("refresh_token"),
                api_key=api_key,
                cache_path=self._personal_fantalab_path(),
            )
        except FantaLabError as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_fantalab_token", str(error))
            return
        self._send_json(HTTPStatus.OK, {"connected": True})

    def _delete_fantalab_credentials(self) -> None:
        if not self._require_fantalab_admin():
            return
        try:
            self._personal_fantalab_path().unlink(missing_ok=True)
        except OSError:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "fantalab_disconnect_failed",
                "Non riesco a scollegare l'account FantaLab.",
            )
            return
        self._send_json(HTTPStatus.OK, {"connected": False})

    def _scout_snapshot(self, season_slug: str) -> None:
        try:
            value = load_scout_snapshot(
                season_slug,
                raw_dir=self.server.raw_dir,
                updates_dir=self.server.updates_dir,
            )
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_season", "La stagione richiesta non è valida.")
            return
        self._send_json(HTTPStatus.OK, value)

    def _scout_snapshot_claude(self, season_slug: str) -> None:
        try:
            value = load_scout_snapshot_claude(
                season_slug,
                raw_dir=self.server.raw_dir,
                updates_dir=self.server.updates_dir,
            )
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_season", "La stagione richiesta non è valida.")
            return
        self._send_json(HTTPStatus.OK, value)

    def _fantalab_snapshot(self) -> None:
        if not self._require_fantalab_admin():
            return
        request = self._read_json_object()
        if request is None:
            return
        try:
            if self.server.fantalab_reader is not None:
                result = self.server.fantalab_reader(request)
            else:
                api_key = os.environ.get("FISHERTIGER_FANTALAB_FIREBASE_API_KEY")
                personal_token = resolve_fantalab_id_token(
                    bootstrap_refresh_token=None,
                    api_key=api_key,
                    cache_path=self._personal_fantalab_path(),
                )
                renewed_token = resolve_fantalab_id_token(
                    bootstrap_refresh_token=os.environ.get("FISHERTIGER_FANTALAB_REFRESH_TOKEN"),
                    api_key=api_key,
                    cache_path=self.server.updates_dir / "fantalab" / "credentials.json",
                )
                result = live_snapshot(
                    request,
                    cache_path=self.server.updates_dir / "fantalab" / "listone.json",
                    token=(
                        personal_token
                        or renewed_token
                        or os.environ.get("FISHERTIGER_FANTALAB_TOKEN")
                    ),
                )
        except FantaLabError as error:
            self._error(HTTPStatus.BAD_GATEWAY, "fantalab_unavailable", str(error))
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "fantalab_failed",
                "La sincronizzazione con FantaLab non è riuscita.",
            )
            return
        self._send_json(HTTPStatus.OK, result)

    def _default_profile(self) -> None:
        try:
            value = json.loads(self.server.default_profile_path.read_text(encoding="utf-8"))
            profile = self.server.profile_loader(value)
            profile = self._derive_calendar_participants(profile)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "default_profile_unavailable", "The default profile is unavailable.")
            return
        self._send_json(HTTPStatus.OK, profile_response(profile))

    def _profile_index(self) -> None:
        directory = self.server.profiles_dir
        if not directory.exists():
            self._send_json(HTTPStatus.OK, {"profiles": []})
            return
        if not directory.is_dir():
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "Profile storage is unavailable.")
            return
        profiles = sorted(path.stem for path in directory.glob("*.json") if path.is_file() and PROFILE_NAME.fullmatch(path.stem))
        self._send_json(HTTPStatus.OK, {"profiles": profiles})

    def _get_profile(self, name: str) -> None:
        profile_path = self._profile_path(name)
        if profile_path is None:
            return
        try:
            profile = resolve_profile({"profile_id": name}, self.server.profiles_dir, profile_loader=self.server.profile_loader)
        except ProfileRequestError as error:
            if not profile_path.exists():
                self._error(HTTPStatus.NOT_FOUND, "profile_not_found", "The profile does not exist.")
            else:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The stored profile is invalid or unreadable.")
            return
        try:
            self._send_json(HTTPStatus.OK, profile_response(self._derive_calendar_participants(profile)))
        except (OSError, ValueError) as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_source_data", str(error))

    def _put_profile(self, name: str) -> None:
        profile_path = self._profile_path(name)
        if profile_path is None:
            return
        value = self._read_json_object()
        if value is None:
            return
        try:
            profile = self.server.profile_loader(value)
            if profile.profile_id != name:
                raise ValueError("profile_id must match the saved profile name")
        except (AttributeError, TypeError, ValueError, KeyError) as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_profile", str(error))
            return
        try:
            with profile_transaction(self.server.updates_dir, profile.profile_id):
                profile_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=profile_path.parent, delete=False) as handle:
                    json.dump(profile.to_dict(), handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                    temporary_path = Path(handle.name)
                temporary_path.replace(profile_path)
        except (OSError, TypeError, ValueError):
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The profile could not be saved.")
            return
        self._send_json(HTTPStatus.OK, profile_response(profile))

    def _auction_state_path(self, name: str, *, require_profile: bool = True) -> Path | None:
        profile_path = self._profile_path(name)
        if profile_path is None:
            return None
        if require_profile and not profile_path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "profile_not_found", "The profile does not exist.")
            return None
        return self.server.auction_states_dir / f"{name}.json"

    def _get_auction_state(self, name: str) -> None:
        state_path = self._auction_state_path(name)
        if state_path is None:
            return
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._send_json(
                HTTPStatus.OK,
                {"profile_id": name, "revision": 0, "updated_at": None, "updated_by": None, "state": None},
            )
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The saved auction is invalid or unreadable.")
            return
        if not isinstance(value, dict) or value.get("profile_id") != name:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The saved auction is invalid or unreadable.")
            return
        self._send_json(HTTPStatus.OK, value)

    def _put_auction_state(self, name: str) -> None:
        state_path = self._auction_state_path(name)
        if state_path is None:
            return
        request = self._read_json_object()
        if request is None:
            return
        state = request.get("state")
        base_revision = request.get("base_revision")
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("version"), int)
            or not isinstance(state.get("teams"), list)
            or not isinstance(state.get("history"), list)
            or not isinstance(state.get("undone"), list)
        ):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_auction_state", "The auction state is incomplete or invalid.")
            return
        if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 0:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_revision", "base_revision must be a non-negative integer.")
            return
        try:
            with profile_transaction(self.server.auction_states_dir, name):
                try:
                    current = json.loads(state_path.read_text(encoding="utf-8"))
                    current_revision = int(current.get("revision", 0))
                except FileNotFoundError:
                    current = None
                    current_revision = 0
                if base_revision != current_revision:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": {
                                "code": "auction_state_conflict",
                                "message": "The auction changed on another device.",
                                "details": current,
                            }
                        },
                    )
                    return
                value = {
                    "profile_id": name,
                    "revision": current_revision + 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "updated_by": (self._authenticated_user or {}).get("username"),
                    "state": state,
                }
                state_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=state_path.parent, delete=False) as handle:
                    json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                    temporary_path = Path(handle.name)
                temporary_path.replace(state_path)
        except (OSError, TypeError, ValueError):
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The auction could not be saved.")
            return
        self._send_json(HTTPStatus.OK, value)

    def _put_upload(self, relative_path: str) -> None:
        parts = relative_path.strip("/").split("/")
        if (
            len(parts) != 3
            or not PROFILE_NAME.fullmatch(parts[0])
            or parts[1] not in SOURCE_GROUPS
            or not SOURCE_NAME.fullmatch(parts[2])
            or parts[2] not in FIXED_SOURCE_SUFFIXES.get(parts[1], {})
        ):
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_upload_path",
                "Percorso di caricamento non valido. Controlla che l'ID profilo contenga solo lettere, numeri, trattini o underscore, senza spazi o barre.",
            )
            return
        filename = self.headers.get("X-Filename", "")
        suffix = Path(filename).suffix.lower()
        expected_suffix = FIXED_SOURCE_SUFFIXES[parts[1]][parts[2]]
        if suffix != expected_suffix:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_upload_type", f"This source requires a {expected_suffix} file.")
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            content_length = -1
        if content_length < 1 or content_length > MAX_UPLOAD_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "invalid_upload_size", "Upload size must be between 1 byte and 50 MB.")
            return
        profile_id, group, source_name = parts
        target = self.server.uploads_dir / profile_id / group / f"{source_name}{suffix}"
        temporary_path: Path | None = None
        try:
            with profile_transaction(self.server.updates_dir, profile_id):
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
                    handle.write(self.rfile.read(content_length))
                    temporary_path = Path(handle.name)
                if group == "current_sources" and source_name == "league_calendar":
                    try:
                        preprocess_legacy_calendar(temporary_path, profile_id)
                    except (BadZipFile, KeyError, OSError, ValueError) as error:
                        self._error(
                            HTTPStatus.UNPROCESSABLE_ENTITY,
                            "invalid_league_calendar",
                            f"{error}. Download the calendar template and keep the worksheet named 'Calendario'.",
                        )
                        return
                temporary_path.replace(target)
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "upload_failed", "The source file could not be stored.")
            return
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        self._send_json(HTTPStatus.OK, {"path": target.as_posix(), "filename": Path(filename).name, "size": content_length})

    def _league_calendar_template(self) -> None:
        self._send_bytes(
            HTTPStatus.OK,
            build_legacy_calendar_template(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "calendario_lega_template.xlsx",
        )

    def _source_status(self) -> None:
        value = self._read_json_object()
        if value is None:
            return
        try:
            profile = self.server.profile_loader(value)
        except (AttributeError, TypeError, ValueError, KeyError) as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_profile", str(error))
            return
        self._send_json(HTTPStatus.OK, {"sources": source_fingerprints(profile, Path())})

    def _player_list_profile_request(self) -> tuple[Any, dict[str, Any]] | None:
        value = self._read_json_object()
        if value is None:
            return None
        try:
            profile = resolve_profile(value, self.server.profiles_dir, profile_loader=self.server.profile_loader)
        except ProfileRequestError as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_profile", str(error))
            return None
        return profile, value

    def _put_player_list_candidate(self, relative_path: str) -> None:
        parts = relative_path.split("/")
        if len(parts) != 2 or not PROFILE_NAME.fullmatch(parts[0]) or not re.fullmatch(r"\d{4}-\d{2}", parts[1]):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_candidate_path", "Candidate paths must identify a profile and YYYY-YY season.")
            return
        profile_id, slug = parts
        season = slug.replace("-", "/")
        try:
            if season_slug(season) != slug:
                raise PlayerListUpdateError("The candidate season is invalid.")
        except PlayerListUpdateError as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_candidate_path", str(error))
            return
        filename = self.headers.get("X-Filename", "")
        if Path(filename).suffix.lower() != ".xlsx":
            self._error(HTTPStatus.BAD_REQUEST, "invalid_upload_type", "The candidate must be an .xlsx file.")
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            content_length = -1
        if content_length < 1 or content_length > MAX_UPLOAD_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "invalid_upload_size", "Upload size must be between 1 byte and 50 MB.")
            return
        try:
            result = store_candidate(self.server.updates_dir, profile_id, season, self.rfile.read(content_length), filename)
        except PlayerListUpdateError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_candidate", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The candidate could not be stored.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _check_player_list_updates(self) -> None:
        request = self._player_list_profile_request()
        if request is None:
            return
        profile, _ = request
        try:
            result = public_check(profile, self.server.player_list_fetcher)
        except PlayerListUpdateError as error:
            self._error(HTTPStatus.BAD_GATEWAY, "update_check_failed", str(error))
            return
        except OSError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_source_data", str(error))
            return
        self._send_json(HTTPStatus.OK, result)

    def _player_list_status(self) -> None:
        request = self._player_list_profile_request()
        if request is None:
            return
        profile, _ = request
        try:
            with profile_transaction(self.server.updates_dir, profile.profile_id):
                active_profile = persisted_or_inline_profile(self.server.profiles_dir, profile, self.server.profile_loader)
                active_profile = self._derive_calendar_participants(active_profile)
                result = candidate_status(self.server.updates_dir, active_profile)
        except PlayerListUpdateError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "candidate_unavailable", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "Candidate status is unavailable.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _apply_player_list_candidate(self) -> None:
        request = self._player_list_profile_request()
        if request is None:
            return
        profile, value = request
        profile = self._derive_calendar_participants(profile)
        candidate_hash = value.get("candidate_hash")
        profile_hash = value.get("profile_hash")
        active_hash = value.get("active_hash")
        starters_hash = value.get("starters_hash")
        if not isinstance(candidate_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_hash):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_candidate_hash", "candidate_hash must be a SHA-256 string.")
            return
        if not isinstance(profile_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", profile_hash):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_profile_hash", "profile_hash must be a SHA-256 string.")
            return
        if not isinstance(active_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", active_hash):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_active_hash", "active_hash must be a SHA-256 string.")
            return
        if not isinstance(starters_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", starters_hash):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_starters_hash", "starters_hash must be a SHA-256 string.")
            return
        try:
            result = apply_candidate(
                self.server.updates_dir, self.server.uploads_dir, self.server.profiles_dir,
                profile, candidate_hash, profile_hash, active_hash, starters_hash, self.server.datasets_dir, self.server.generator,
                self.server.profile_loader, generate_dataset, self._derive_calendar_participants,
            )
        except StalePlayerListUpdateError as error:
            self._error(HTTPStatus.CONFLICT, error.code, str(error))
            return
        except PlayerListUpdateError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "candidate_unavailable", str(error))
            return
        except (FileNotFoundError, ValueError) as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_source_data", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The updated profile could not be stored.")
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "generation_failed", "Generation failed.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _update_request(self) -> tuple[Any, str, str, str, str] | None:
        value = self._read_json_object()
        if value is None:
            return None
        try:
            profile = resolve_profile(value, self.server.profiles_dir, profile_loader=self.server.profile_loader)
        except ProfileRequestError as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_profile", str(error))
            return None
        content_hash = value.get("content_hash", "")
        audit_hash = value.get("audit_hash", "")
        if not isinstance(content_hash, str) or not isinstance(audit_hash, str):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_snapshot_hash", "Snapshot hashes must be strings.")
            return None
        return profile, profile.profile_id, profile.season.season, content_hash, audit_hash

    def _check_sosfanta_updates(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, _, _ = request
        try:
            result = check_updates(
                self.server.updates_dir,
                profile_id,
                season,
                self.server.update_fetcher,
            )
        except SosFantaError as error:
            self._error(HTTPStatus.BAD_GATEWAY, "update_check_failed", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The update snapshot could not be stored.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _sosfanta_status(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, _, _ = request
        try:
            result = stored_status(self.server.updates_dir, profile_id, season)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "snapshot_unavailable", str(error))
            return
        self._send_json(HTTPStatus.OK, result)

    def _accept_sosfanta_updates(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, content_hash, _ = request
        try:
            result = accept_latest(self.server.updates_dir, profile_id, season, content_hash)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "snapshot_unavailable", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The update snapshot could not be accepted.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _sosfanta_bundle(self) -> None:
        request = self._update_request()
        if request is None:
            return
        profile, profile_id, season, content_hash, _ = request
        source = next((item for item in profile.current_sources if item.name == "starters"), None)
        if source is None:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "source_unavailable", "The profile does not declare a starters source.")
            return
        declared = Path(source.path)
        candidates = [declared] if declared.is_absolute() else [
            declared,
            Path.cwd() / declared,
            Path(__file__).resolve().parents[1] / declared,
        ]
        starters_path = next((candidate for candidate in candidates if candidate.is_file()), declared)
        try:
            bundle = build_bundle(self.server.updates_dir, profile_id, season, starters_path, content_hash)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "bundle_unavailable", str(error))
            return
        self._send_bytes(
            HTTPStatus.OK,
            bundle.encode("utf-8"),
            "text/plain; charset=utf-8",
            f'sosfanta-update-{season.replace("/", "-")}.txt',
        )

    def _formation_source_paths(self, profile: Any) -> tuple[Path, Path] | None:
        paths = []
        for name in ("starters", "player_list"):
            source = next((item for item in profile.current_sources if item.name == name), None)
            if source is None:
                self._error(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "source_unavailable",
                    f"The profile does not declare a {name} source.",
                )
                return None
            declared = Path(source.path)
            candidates = [declared] if declared.is_absolute() else [
                declared,
                Path.cwd() / declared,
                Path(__file__).resolve().parents[1] / declared,
            ]
            paths.append(next((candidate for candidate in candidates if candidate.is_file()), declared))
        return paths[0], paths[1]

    def _persistent_source_copy(self, profile: Any, name: str) -> tuple[Any, Path]:
        """Copy a packaged source to writable storage and persist the new profile path."""
        source = next((item for item in profile.current_sources if item.name == name), None)
        if source is None:
            raise SosFantaError(f"The profile does not declare a {name} source.")
        declared = Path(source.path)
        candidates = [declared] if declared.is_absolute() else [
            declared,
            Path.cwd() / declared,
            Path(__file__).resolve().parents[1] / declared,
        ]
        current_path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
        if current_path is None:
            raise SosFantaError(f"The {name} source is unavailable.")
        writable_root = self.server.uploads_dir.resolve()
        if current_path.is_relative_to(writable_root):
            return profile, current_path

        suffix = current_path.suffix or ".dat"
        destination = (
            writable_root
            / profile.profile_id
            / profile.season.season.replace("/", "-")
            / f"{name}{suffix}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=destination.parent, delete=False) as handle:
            with current_path.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, handle)
            temporary_source = Path(handle.name)
        temporary_source.replace(destination)

        value = profile.to_dict()
        for item in value["current_sources"]:
            if item["name"] == name:
                item["path"] = str(destination)
                break
        updated_profile = self.server.profile_loader(value)
        self.server.profiles_dir.mkdir(parents=True, exist_ok=True)
        profile_path = self.server.profiles_dir / f"{profile.profile_id}.json"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.server.profiles_dir, delete=False
        ) as handle:
            json.dump(updated_profile.to_dict(), handle, ensure_ascii=False, separators=(",", ":"))
            temporary_profile = Path(handle.name)
        temporary_profile.replace(profile_path)
        return updated_profile, destination

    def _refresh_scout_for_profile(self, profile: Any) -> dict[str, Any]:
        season_slug = profile.season.season.replace("/", "-")
        dataset_file = self.server.datasets_dir / auction_dataset_path(profile)
        try:
            dataset = json.loads(dataset_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SosFantaError("Generate the dataset before refreshing Scout AI.") from error
        players = dataset.get("players")
        if not isinstance(players, list) or not players:
            raise SosFantaError("The generated dataset does not contain players.")
        return refresh_official_scout(
            players,
            season_slug,
            raw_dir=self.server.raw_dir,
            updates_dir=self.server.updates_dir,
        )

    def _refresh_scout(self) -> None:
        value = self._read_json_object()
        if value is None:
            return
        try:
            profile = resolve_profile(value, self.server.profiles_dir, profile_loader=self.server.profile_loader)
            snapshot = self._refresh_scout_for_profile(profile)
        except ProfileRequestError as error:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_profile", str(error))
            return
        except SosFantaError as error:
            self._error(HTTPStatus.BAD_GATEWAY, "scout_refresh_failed", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The Scout snapshot could not be stored.")
            return
        self._send_json(HTTPStatus.OK, snapshot)

    def _update_all(self) -> None:
        request = self._update_request()
        if request is None:
            return
        profile, profile_id, season, _, _ = request
        profile = self._derive_calendar_participants(profile)
        results: list[dict[str, Any]] = []
        generation: dict[str, Any] = {}

        def completed(source: str, label: str, result: dict[str, Any], message: str) -> None:
            results.append({
                "source": source,
                "label": label,
                "ok": True,
                "state": result.get("state", "unchanged"),
                "change_count": result.get("change_count", 0),
                "message": message,
            })

        def failed(source: str, label: str, error: Exception) -> None:
            results.append({
                "source": source,
                "label": label,
                "ok": False,
                "state": "error",
                "change_count": 0,
                "message": str(error),
            })

        try:
            guide = check_updates(self.server.updates_dir, profile_id, season, self.server.update_fetcher)
            accept_latest(self.server.updates_dir, profile_id, season, guide["content_hash"])
            completed("sosfanta", "Guida asta", guide, "Controllata e salvata.")
        except (SosFantaError, OSError, ValueError) as error:
            failed("sosfanta", "Guida asta", error)

        try:
            set_pieces = check_set_piece_updates(
                self.server.updates_dir, profile_id, season, self.server.set_piece_fetcher
            )
            accept_latest_set_pieces(
                self.server.updates_dir, profile_id, season, set_pieces["content_hash"]
            )
            completed("sosfanta-set-pieces", "Rigoristi e piazzati", set_pieces, "Controllati e salvati.")
        except (SosFantaError, OSError, ValueError) as error:
            failed("sosfanta-set-pieces", "Rigoristi e piazzati", error)

        try:
            goalkeepers = check_goalkeeper_updates(
                self.server.updates_dir, profile_id, season, self.server.goalkeeper_fetcher
            )
            profile, starters_path = self._persistent_source_copy(profile, "starters")
            paths = self._formation_source_paths(profile)
            if paths is None:
                raise SosFantaError("The starters or player-list source is unavailable.")
            _, listone_path = paths

            def regenerate() -> dict[str, Any]:
                result = generate_dataset(profile, self.server.datasets_dir, generator=self.server.generator)
                generation.update(result)
                return result

            applied = apply_goalkeeper_update(
                self.server.updates_dir,
                profile_id,
                season,
                starters_path,
                listone_path,
                goalkeepers["content_hash"],
                regenerate,
            )
            completed(
                "sosfanta-goalkeepers",
                "Gerarchie portieri",
                goalkeepers,
                f"Applicate e salvate ({applied.get('updated_rows', 0)} righe).",
            )
        except (SosFantaError, OSError, ValueError) as error:
            failed("sosfanta-goalkeepers", "Gerarchie portieri", error)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            failed("sosfanta-goalkeepers", "Gerarchie portieri", error)

        try:
            paths = self._formation_source_paths(profile)
            if paths is None:
                raise SosFantaError("The starters or player-list source is unavailable.")
            formations = check_formation_updates(
                self.server.updates_dir,
                profile_id,
                season,
                *paths,
                self.server.formations_fetcher,
            )
            accept_latest_formations(
                self.server.updates_dir, profile_id, season, formations["content_hash"]
            )
            issues = formations.get("audit", {}).get("summary", {}).get("issue_count", 0)
            completed(
                "sosfanta-formations",
                "Formazioni e titolarita",
                formations,
                f"Controllate e salvate; {issues} differenze da monitorare.",
            )
        except (SosFantaError, OSError, ValueError) as error:
            failed("sosfanta-formations", "Formazioni e titolarita", error)

        try:
            player_list = public_check(profile, self.server.player_list_fetcher)
            needs_file = player_list.get("state") == "changed"
            completed(
                "player-list",
                "Listone Fantacalcio",
                player_list,
                "Aggiornamento rilevato: serve il file XLSX ufficiale." if needs_file else "Controllato: nessuna variazione.",
            )
        except (PlayerListUpdateError, OSError, ValueError) as error:
            failed("player-list", "Listone Fantacalcio", error)

        try:
            scout = self._refresh_scout_for_profile(profile)
            counts = scout.pop("counts", {})
            completed(
                "scout",
                "Scout AI",
                {"state": "unchanged", "change_count": sum(counts.values())},
                "Aggiornato: " + ", ".join(f"{value} {key}" for key, value in sorted(counts.items())),
            )
        except (SosFantaError, OSError, ValueError) as error:
            failed("scout", "Scout AI", error)

        payload = {
            "ok": all(item["ok"] for item in results),
            "profile_id": profile.profile_id,
            "profile": profile.to_dict(),
            "dataset_path": generation.get("dataset_path"),
            "sources": results,
        }
        self._send_json(HTTPStatus.OK, payload)

    def _check_formation_updates(self) -> None:
        request = self._update_request()
        if request is None:
            return
        profile, profile_id, season, _, _ = request
        paths = self._formation_source_paths(profile)
        if paths is None:
            return
        try:
            result = check_formation_updates(
                self.server.updates_dir, profile_id, season, *paths, self.server.formations_fetcher,
            )
        except SosFantaError as error:
            self._error(HTTPStatus.BAD_GATEWAY, "update_check_failed", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The formations snapshot could not be stored.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _formation_status(self) -> None:
        request = self._update_request()
        if request is None:
            return
        profile, profile_id, season, _, _ = request
        paths = self._formation_source_paths(profile)
        if paths is None:
            return
        try:
            result = stored_formation_status(self.server.updates_dir, profile_id, season, *paths)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "snapshot_unavailable", str(error))
            return
        self._send_json(HTTPStatus.OK, result)

    def _accept_formation_updates(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, content_hash, _ = request
        try:
            result = accept_latest_formations(self.server.updates_dir, profile_id, season, content_hash)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "snapshot_unavailable", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The formations snapshot could not be accepted.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _formation_bundle(self) -> None:
        request = self._update_request()
        if request is None:
            return
        profile, profile_id, season, content_hash, audit_hash = request
        paths = self._formation_source_paths(profile)
        if paths is None:
            return
        try:
            bundle = build_formations_bundle(
                self.server.updates_dir, profile_id, season, *paths, content_hash, audit_hash,
            )
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "bundle_unavailable", str(error))
            return
        self._send_bytes(
            HTTPStatus.OK,
            bundle.encode("utf-8"),
            "text/plain; charset=utf-8",
            f'sosfanta-formazioni-update-{season.replace("/", "-")}.txt',
        )

    def _check_goalkeeper_updates(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, _, _ = request
        try:
            result = check_goalkeeper_updates(self.server.updates_dir, profile_id, season, self.server.goalkeeper_fetcher)
        except SosFantaError as error:
            self._error(HTTPStatus.BAD_GATEWAY, "update_check_failed", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The goalkeeper snapshot could not be stored.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _goalkeeper_status(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, _, _ = request
        try:
            result = stored_goalkeeper_status(self.server.updates_dir, profile_id, season)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "snapshot_unavailable", str(error))
            return
        self._send_json(HTTPStatus.OK, result)

    def _accept_goalkeeper_updates(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, content_hash, _ = request
        try:
            result = accept_latest_goalkeepers(self.server.updates_dir, profile_id, season, content_hash)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "snapshot_unavailable", str(error))
            return
        self._send_json(HTTPStatus.OK, result)

    def _apply_goalkeeper_updates(self) -> None:
        request = self._update_request()
        if request is None:
            return
        profile, profile_id, season, content_hash, _ = request
        profile = self._derive_calendar_participants(profile)
        paths = self._formation_source_paths(profile)
        if paths is None:
            return
        starters_path, listone_path = paths
        try:
            result = apply_goalkeeper_update(
                self.server.updates_dir,
                profile_id,
                season,
                starters_path,
                listone_path,
                content_hash,
                lambda: generate_dataset(profile, self.server.datasets_dir, generator=self.server.generator),
            )
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "update_unavailable", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The goalkeeper update could not be stored.")
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "generation_failed", "The dataset could not be regenerated.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _check_set_piece_updates(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, _, _ = request
        try:
            result = check_set_piece_updates(self.server.updates_dir, profile_id, season, self.server.set_piece_fetcher)
        except SosFantaError as error:
            self._error(HTTPStatus.BAD_GATEWAY, "update_check_failed", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The set-piece snapshot could not be stored.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _set_piece_status(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, _, _ = request
        try:
            result = stored_set_piece_status(self.server.updates_dir, profile_id, season)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "snapshot_unavailable", str(error))
            return
        self._send_json(HTTPStatus.OK, result)

    def _accept_set_piece_updates(self) -> None:
        request = self._update_request()
        if request is None:
            return
        _, profile_id, season, content_hash, _ = request
        try:
            result = accept_latest_set_pieces(self.server.updates_dir, profile_id, season, content_hash)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "snapshot_unavailable", str(error))
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The set-piece snapshot could not be accepted.")
            return
        self._send_json(HTTPStatus.OK, result)

    def _set_piece_bundle(self) -> None:
        request = self._update_request()
        if request is None:
            return
        profile, profile_id, season, content_hash, _ = request
        source = next((item for item in profile.current_sources if item.name == "set_pieces"), None)
        if source is None:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "source_unavailable", "The profile does not declare a set_pieces source.")
            return
        declared = Path(source.path)
        candidates = [declared] if declared.is_absolute() else [
            declared, Path.cwd() / declared, Path(__file__).resolve().parents[1] / declared,
        ]
        set_pieces_path = next((candidate for candidate in candidates if candidate.is_file()), declared)
        try:
            bundle = build_set_piece_bundle(self.server.updates_dir, profile_id, season, set_pieces_path, content_hash)
        except SosFantaError as error:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "bundle_unavailable", str(error))
            return
        self._send_bytes(
            HTTPStatus.OK, bundle.encode("utf-8"), "text/plain; charset=utf-8",
            f'sosfanta-piazzati-update-{season.replace("/", "-")}.txt',
        )

    def _derive_calendar_participants(self, profile: Any) -> Any:
        """Use the league calendar as the authoritative participant roster when available."""
        source = next((item for item in profile.current_sources if item.name == "league_calendar"), None)
        if source is None:
            return profile
        declared = Path(source.path)
        candidates = [declared] if declared.is_absolute() else [declared, Path.cwd() / declared, Path(__file__).resolve().parents[1] / declared]
        calendar_path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if calendar_path is None:
            return profile
        if calendar_path.suffix.lower() == ".json":
            from .league_calendar import validate_calendar

            calendar = json.loads(calendar_path.read_text(encoding="utf-8"))
            validate_calendar(calendar)
        else:
            from .league_calendar import preprocess_legacy_calendar

            calendar = preprocess_legacy_calendar(calendar_path, profile.profile_id)
        value = profile.to_dict()
        teams = calendar["teams"]
        value["participants"] = {
            "team_names": teams,
            "user_team": profile.participants.user_team if profile.participants.user_team in teams else teams[0],
        }
        return self.server.profile_loader(value)

    def _dataset_manifest(self) -> None:
        try:
            self._send_json(HTTPStatus.OK, dataset_manifest(self.server.datasets_dir))
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "Dataset storage is unavailable.")

    def _get_dataset(self, relative_path: str) -> None:
        dataset_path = self._safe_dataset_path(relative_path)
        if dataset_path is None:
            return
        try:
            with dataset_path.open(encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError:
            self._error(HTTPStatus.NOT_FOUND, "dataset_not_found", "The dataset does not exist.")
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The dataset is invalid or unreadable.")
            return
        self._send_json(HTTPStatus.OK, value)

    def _safe_dataset_path(self, relative_path: str) -> Path | None:
        if not relative_path or "\\" in relative_path or not relative_path.endswith(".json"):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_dataset_path", "Dataset paths must be relative JSON paths.")
            return None
        root = self.server.datasets_dir.resolve()
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_dataset_path", "Dataset paths must stay within dataset storage.")
            return None
        return candidate

    def _delete_profile(self, name: str) -> None:
        """Remove a stored profile; generated datasets are deliberately left in place."""
        profile_path = self._profile_path(name)
        if profile_path is None:
            return
        try:
            profile_path.unlink()
        except FileNotFoundError:
            self._error(HTTPStatus.NOT_FOUND, "profile_not_found", "The profile does not exist.")
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The profile could not be deleted.")
            return
        try:
            (self.server.auction_states_dir / f"{name}.json").unlink(missing_ok=True)
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The profile auction could not be deleted.")
            return
        self._send_json(HTTPStatus.OK, {"profile_id": name, "deleted": True})

    def _profile_path(self, name: str) -> Path | None:
        if not PROFILE_NAME.fullmatch(name):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_profile_name", "Profile names must use letters, numbers, underscores, or hyphens.")
            return None
        return self.server.profiles_dir / f"{name}.json"

    def _read_json_object(self) -> dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "Content-Length must be an integer.")
            return None
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request_too_large", "Request body exceeds the size limit.")
            return None
        if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "invalid_content_type", "Content-Type must be application/json.")
            return None
        try:
            value = json.loads(
                self.rfile.read(content_length).decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "Request body must be valid UTF-8 JSON.")
            return None
        if not isinstance(value, dict):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "Request body must be a JSON object.")
            return None
        return value

    def _path(self) -> str:
        return unquote(urlparse(self.path).path)

    def _authorize(self) -> bool:
        self._authenticated_user = None
        store = self.server.credential_store
        if store is not None:
            header = self.headers.get("Authorization", "")
            try:
                scheme, encoded = header.split(" ", 1)
                decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
                username, password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                username = password = ""
                scheme = ""
            if scheme.lower() == "basic":
                self._authenticated_user = store.authenticate(username, password)
            if self._authenticated_user is not None:
                return True
            self._authentication_required()
            return False
        username = self.server.auth_username
        password = self.server.auth_password
        if not username or not password:
            return True
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        if hmac.compare_digest(self.headers.get("Authorization", ""), f"Basic {token}"):
            self._authenticated_user = {"username": username, "is_admin": True}
            return True
        self._authentication_required()
        return False

    def _authentication_required(self) -> None:
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="AstaFanta Support", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _auth_session(self) -> None:
        user = self._authenticated_user
        self._send_json(
            HTTPStatus.OK,
            {
                "authentication_enabled": self.server.credential_store is not None
                or bool(self.server.auth_username),
                "username": user.get("username") if user else None,
                "is_admin": bool(user and user.get("is_admin")),
            },
        )

    def _require_admin(self) -> bool:
        if self._authenticated_user and self._authenticated_user.get("is_admin"):
            return True
        self._error(HTTPStatus.FORBIDDEN, "admin_required", "Questa operazione richiede un account amministratore.")
        return False

    def _require_fantalab_admin(self) -> bool:
        """Keep local unauthenticated development working, but lock hosted live access."""
        if self.server.credential_store is None and not self.server.auth_username:
            return True
        if self._authenticated_user and self._authenticated_user.get("is_admin"):
            return True
        self._error(
            HTTPStatus.FORBIDDEN,
            "fantalab_admin_only",
            "L'asta live FantaLab e temporaneamente riservata all'amministratore.",
        )
        return False

    def _require_credential_store(self) -> CredentialStore | None:
        store = self.server.credential_store
        if store is None:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "accounts_disabled", "La gestione degli account non e attiva su questo server.")
        return store

    def _auth_users(self) -> None:
        store = self._require_credential_store()
        if store is None or not self._require_admin():
            return
        self._send_json(HTTPStatus.OK, {"users": store.list_users()})

    def _create_auth_user(self) -> None:
        store = self._require_credential_store()
        if store is None or not self._require_admin():
            return
        request = self._read_json_object()
        if request is None:
            return
        try:
            user = store.create_user(request.get("username"), request.get("password"))
        except AccountError as error:
            self._error(HTTPStatus.CONFLICT if error.code == "user_exists" else HTTPStatus.BAD_REQUEST, error.code, str(error))
            return
        self._send_json(HTTPStatus.CREATED, {"user": user})

    def _change_password(self) -> None:
        store = self._require_credential_store()
        if store is None:
            return
        request = self._read_json_object()
        if request is None:
            return
        try:
            store.change_password(
                self._authenticated_user["username"],
                request.get("current_password"),
                request.get("new_password"),
            )
        except AccountError as error:
            self._error(HTTPStatus.BAD_REQUEST, error.code, str(error))
            return
        self._send_json(HTTPStatus.OK, {"changed": True})

    def _delete_auth_user(self, username: str) -> None:
        store = self._require_credential_store()
        if store is None or not self._require_admin():
            return
        try:
            store.delete_user(username, requested_by=self._authenticated_user["username"])
        except AccountError as error:
            status = HTTPStatus.NOT_FOUND if error.code == "user_not_found" else HTTPStatus.BAD_REQUEST
            self._error(status, error.code, str(error))
            return
        try:
            personal_credentials_path(self.server.updates_dir, username).unlink(missing_ok=True)
        except OSError:
            # The account is already gone; a stale, unreadable credential file
            # must not turn a successful account deletion into a false failure.
            pass
        self._send_json(HTTPStatus.OK, {"deleted": True})

    def _serve_static(self, request_path: str) -> None:
        root = self.server.static_dir
        if root is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "The requested endpoint does not exist.")
            return
        relative = request_path.lstrip("/") or "index.html"
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_static_path", "Static paths must stay within the web root.")
            return
        if not candidate.is_file() and "." not in Path(relative).name:
            candidate = root / "index.html"
        try:
            body = candidate.read_bytes()
        except FileNotFoundError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "The requested file does not exist.")
            return
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "storage_error", "The requested file could not be read.")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send_bytes(HTTPStatus.OK, body, content_type)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _send_json(self, status: HTTPStatus, value: Any) -> None:
        body = b"" if value is None else json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str, filename: str | None = None) -> None:
        self.send_response(status)
        origin = self.headers.get("Origin")
        if origin and VITE_ORIGIN.fullmatch(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.send_header("Content-Type", content_type)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except ConnectionError:
                self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the local API quiet; callers receive structured HTTP errors."""


def create_server(
    address: tuple[str, int] = ("127.0.0.1", 8000),
    *,
    profiles_dir: Path | str = Path("config/profiles"),
    datasets_dir: Path | str = Path("data/processed"),
    uploads_dir: Path | str = Path("data/uploads"),
    updates_dir: Path | str = Path("data/updates"),
    auction_states_dir: Path | str = Path("data/auction-states"),
    default_profile_path: Path | str = Path("config/default_profile.json"),
    raw_dir: Path | str = Path("data/raw"),
    static_dir: Path | str | None = None,
    auth_username: str | None = None,
    auth_password: str | None = None,
    auth_file: Path | str | None = None,
    generator: PipelineGenerator | None = None,
    simulator: SimulationRunner | None = None,
    profile_loader: ProfileLoader = load_profile,
    update_fetcher: FetchPage = fetch_page,
    formations_fetcher: FetchPage = fetch_page,
    set_piece_fetcher: FetchPage = fetch_page,
    goalkeeper_fetcher: FetchPage = fetch_page,
    player_list_fetcher: PlayerListFetchPage = fetch_public_page,
    fantalab_reader: FantaLabReader | None = None,
) -> LocalApiServer:
    """Create a local API server; inject a pipeline generator for tests or embedding."""
    return LocalApiServer(address, profiles_dir=profiles_dir, datasets_dir=datasets_dir, uploads_dir=uploads_dir, updates_dir=updates_dir, auction_states_dir=auction_states_dir, default_profile_path=default_profile_path, raw_dir=raw_dir, static_dir=static_dir, auth_username=auth_username, auth_password=auth_password, auth_file=auth_file, generator=generator, simulator=simulator, profile_loader=profile_loader, update_fetcher=update_fetcher, formations_fetcher=formations_fetcher, set_piece_fetcher=set_piece_fetcher, goalkeeper_fetcher=goalkeeper_fetcher, player_list_fetcher=player_list_fetcher, fantalab_reader=fantalab_reader)


def _simulate_current_dataset(profile: Any, output_dir: Path, iterations: int, seed: int, rosters: dict[str, list[int]] | None = None) -> dict[str, Any]:
    from .simulate import run_simulation
    from .config import LeagueConfig

    return run_simulation(output_dir, iterations=iterations, seed=seed, rosters=rosters, league=LeagueConfig.from_profile(profile), profile=profile)


def main(argv: list[str] | None = None) -> None:
    """Run the local API without creating a server during module import."""
    parser = argparse.ArgumentParser(description="Run the local fantasy advisor API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--profiles-dir", type=Path, default=Path("config/profiles"))
    parser.add_argument("--datasets-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--uploads-dir", type=Path, default=Path("data/uploads"))
    parser.add_argument("--updates-dir", type=Path, default=Path("data/updates"))
    parser.add_argument("--auction-states-dir", type=Path, default=Path("data/auction-states"))
    parser.add_argument("--static-dir", type=Path)
    args = parser.parse_args(argv)
    server = create_server(
        (args.host, args.port),
        profiles_dir=args.profiles_dir,
        datasets_dir=args.datasets_dir,
        uploads_dir=args.uploads_dir,
        updates_dir=args.updates_dir,
        auction_states_dir=args.auction_states_dir,
        static_dir=args.static_dir,
        auth_username=os.environ.get("FISHERTIGER_USERNAME"),
        auth_password=os.environ.get("FISHERTIGER_PASSWORD"),
        auth_file=os.environ.get("FISHERTIGER_AUTH_FILE"),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

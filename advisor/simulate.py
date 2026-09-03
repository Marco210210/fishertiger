"""CLI entrypoint for reproducible sample or real-auction season simulation."""
import argparse
import copy
import json
import tempfile
from pathlib import Path
from typing import Any

from .config import LeagueConfig
from .league_profile import LeagueProfile
from .simulation import make_sample_rosters, normalize_rosters, simulate_season
from .freshness import SIMULATOR_VERSION, dataset_input_hash, roster_input_hash, simulation_configuration_hash, simulation_input_hash, source_fingerprints


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary_path = Path(handle.name)
            json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        temporary_path.replace(path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def run_simulation(output_dir: Path, *, iterations: int = 1000, seed: int = 202627, rosters: dict[str, list[int]] | None = None, league: LeagueConfig | None = None, profile: LeagueProfile | None = None, raw_dir: Path = Path("data/raw")) -> dict:
    """Simulate the current dataset and replace its previous season report."""
    league = league or LeagueConfig()
    payload = json.loads((output_dir / "auction_data.json").read_text(encoding="utf-8"))
    metadata = payload.get("meta", {}).get("profile") or {}
    expected_dataset_hash = metadata.get("dataset_input_hash")
    if profile is not None and expected_dataset_hash is None:
        raise ValueError("Dataset metadata is missing; regenerate the dataset before simulating")
    if profile is not None:
        current_dataset_hash = dataset_input_hash(profile, source_fingerprints(profile, raw_dir))
        if current_dataset_hash != expected_dataset_hash:
            raise ValueError("Dataset inputs changed; regenerate the dataset before simulating")
    roster_mode = "auction" if rosters is not None else "sample"
    rosters = normalize_rosters(payload, rosters if rosters is not None else make_sample_rosters(payload, league), league)
    result = simulate_season(payload, rosters, iterations=iterations, seed=seed, league=league)
    roster_hash = roster_input_hash(rosters)
    output = {"iterations": result.iterations, "teams": result.teams, "scenarios": result.scenarios, "diagnostics": result.diagnostics, "rosters": rosters, "meta": {"dataset_input_hash": expected_dataset_hash, "simulation_configuration_hash": simulation_configuration_hash(profile) if profile else None, "simulation_input_hash": simulation_input_hash(expected_dataset_hash or "", profile, roster_mode, roster_hash) if profile else None, "roster_mode": roster_mode, "roster_input_hash": roster_hash, "seed": seed, "iterations": iterations, "simulator_version": SIMULATOR_VERSION}}
    _write_json_atomic(output_dir / "season_simulation.json", output)
    return output


def anonymize_public_simulation(simulation: dict, calendar: dict) -> dict:
    """Replace local fantasy-team names before publishing a browser report."""
    public_simulation = copy.deepcopy(simulation)
    replacements = {
        name: f"Squadra {index}"
        for index, name in enumerate(calendar.get("teams", []), start=1)
    }
    for field in ("teams", "scenarios", "rosters"):
        if field in public_simulation:
            public_simulation[field] = {
                replacements.get(name, name): value
                for name, value in public_simulation[field].items()
            }
    return public_simulation


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Simulate a fantasy league season.")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--web-export-dir", type=Path)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=202627)
    parser.add_argument("--rosters", type=Path, help="JSON file containing complete real-auction rosters")
    args = parser.parse_args(argv)
    profile = LeagueProfile.load_json(args.profile) if args.profile else None
    league = LeagueConfig.from_profile(profile) if profile else LeagueConfig()
    output_dir = args.output_dir / profile.profile_id / profile.season.season.replace("/", "-") if profile else args.output_dir
    rosters = json.loads(args.rosters.read_text(encoding="utf-8")) if args.rosters else None
    if rosters is not None and not isinstance(rosters, dict):
        raise ValueError("Rosters JSON must be an object keyed by calendar team name")
    output = run_simulation(output_dir, iterations=args.iterations, seed=args.seed, rosters=rosters, league=league, profile=profile, raw_dir=args.raw_dir)
    if args.web_export_dir:
        payload = json.loads((output_dir / "auction_data.json").read_text(encoding="utf-8"))
        public_output = anonymize_public_simulation(output, payload["calendario_lega"])
        _write_json_atomic(args.web_export_dir / "season_simulation.json", public_output)
    print(f"Simulated {args.iterations:,} seasons to {output_dir / 'season_simulation.json'}")


if __name__ == "__main__":
    main()

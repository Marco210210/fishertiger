"""Refresh the committed public Fantacalcio snapshot."""
from __future__ import annotations

import argparse
from pathlib import Path

from advisor.official_snapshot import update_snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default="2026/27")
    parser.add_argument("--output", type=Path, default=Path("data/raw/fantacalcio_2026_27.json"))
    args = parser.parse_args()
    snapshot = update_snapshot(args.output, args.season)
    print(f"Updated {args.output}: {len(snapshot['players'])} players, {snapshot['observed_matchdays']} observed matchdays, fetched {snapshot['fetched_at']}")


if __name__ == "__main__":
    main()

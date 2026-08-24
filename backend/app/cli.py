from __future__ import annotations

import argparse
import sys

from app.config import load_settings
from app.coverage import build_coverage_report, estimate_effort
from app.osm.fetch import fetch_district
from app.osm.overpass import OverpassClient
from app.osm.snapshot import DistrictSnapshot


def _say(message: str) -> None:
    print(message, flush=True)


def cmd_fetch_district(args: argparse.Namespace) -> int:
    settings = load_settings()
    path = settings.snapshot_path
    if path.exists() and not args.force:
        _say(f"snapshot already exists at {path} ({path.stat().st_size / 1e6:.1f} MB)")
        _say("pass --force to refetch")
        return 0

    client = OverpassClient(
        mirrors=settings.mirrors,
        max_attempts=settings.max_attempts,
        timeout_s=settings.overpass_timeout_s,
        on_attempt=lambda attempt, mirror, msg: _say(
            f"    [attempt {attempt + 1} {mirror.split('/')[2]}] {msg}"
        ),
    )
    snapshot, stats = fetch_district(
        client, settings.district_relation_id, settings.overpass_timeout_s, progress=_say
    )
    size = snapshot.save(path)
    _say(f"\nsaved {path} ({size / 1e6:.2f} MB gzipped)")
    for warning in stats.warnings:
        _say(f"  WARNING: {warning}")
    _say("\nthe app now runs entirely offline; no further Overpass calls are made.")
    return 0


def cmd_report(_args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.snapshot_path.exists():
        _say(f"no snapshot at {settings.snapshot_path}; run `make fetch-district` first")
        return 1
    snapshot = DistrictSnapshot.load(settings.snapshot_path)
    report = build_coverage_report(snapshot)
    effort = estimate_effort(report.doors)

    _say(f"{report.district_name}  (snapshot {report.fetched_at})")
    _say(f"  extent                : {report.extent_km[0]} km N-S x {report.extent_km[1]} km E-W")
    _say(f"  boundary rings        : {report.boundary_rings} ({report.boundary_closed_rings} closed)")
    _say(f"  doors                 : {report.doors}")
    _say(f"  stops (street+number) : {report.stops}")
    _say(f"  streets               : {report.streets}")
    _say(f"  doors with a unit     : {report.doors_with_unit}")
    _say(f"  multi-unit stops      : {report.multi_unit_stops}")
    _say(f"  likely gated blocks   : {report.gated_complex_candidates} (>=8 doors at one number)")
    _say(f"  blockfaces            : {report.blockfaces}")
    _say(f"      one side per pass : {report.blockfaces_one_side_per_pass} (arterials)")
    _say(f"      off street network: {report.blockfaces_off_network} (clipped boundary roads)")
    _say(f"  knocking hours        : {report.knock_hours} (gated blocks capped)")
    _say(f"      uncapped          : {report.uncapped_knock_hours} (every door in every tower)")
    _say(f"  addresses w/o street  : {report.addresses_missing_street}")
    _say(f"  walkable ways         : {report.walkable_ways}")
    _say(f"  largest stops         :")
    for stop in report.largest_stops[:5]:
        _say(f"      {stop['number']} {stop['street']}: {stop['doors']} doors")
    _say(f"  doors per pair-session: ~{effort['doors_per_pair_session']}")
    _say(f"  pair-sessions for 100%: ~{effort['pair_sessions_for_full_coverage']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="doorknock", description="Doorknock planner tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-district", help="one-time district extract from Overpass")
    fetch.add_argument("--force", action="store_true", help="refetch even if cached")
    fetch.set_defaults(func=cmd_fetch_district)

    report = subparsers.add_parser("report", help="print the coverage report for the cached district")
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

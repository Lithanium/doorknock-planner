from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.osm.boundary import Point, assemble_rings, ring_is_closed
from app.osm.overpass import OverpassClient
from app.osm.snapshot import (
    WAY_TAG_WHITELIST,
    Address,
    DistrictSnapshot,
    WalkWay,
    utc_now_iso,
)

WALKABLE_HIGHWAY_RE = (
    "^(residential|footway|path|pedestrian|living_street|service|unclassified"
    "|tertiary|tertiary_link|secondary|secondary_link|primary|primary_link"
    "|steps|track|cycleway|road)$"
)

Progress = Callable[[str], None]


@dataclass
class FetchStats:
    boundary_ways: int = 0
    boundary_rings: int = 0
    closed_rings: int = 0
    address_elements: int = 0
    addresses_kept: int = 0
    skipped_no_street: int = 0
    skipped_no_position: int = 0
    way_elements: int = 0
    ways_kept: int = 0
    warnings: list[str] = field(default_factory=list)


def boundary_query(relation_id: int, timeout_s: int) -> str:
    # `out geom` (body mode) is required: `out tags` suppresses relation
    # members, which silently yields a boundary with no geometry at all.
    return f"[out:json][timeout:{timeout_s}];rel({relation_id});out geom;"


def addresses_query(relation_id: int, timeout_s: int) -> str:
    return (
        f"[out:json][timeout:{timeout_s}];\n"
        f"rel({relation_id});map_to_area->.d;\n"
        '(node["addr:housenumber"](area.d);way["addr:housenumber"](area.d););\n'
        "out center tags;"
    )


def ways_query(relation_id: int, timeout_s: int) -> str:
    return (
        f"[out:json][timeout:{timeout_s}];\n"
        f"rel({relation_id});map_to_area->.d;\n"
        f'way["highway"~"{WALKABLE_HIGHWAY_RE}"](area.d);\n'
        "out geom;"
    )


def parse_boundary(elements: list[dict[str, Any]], stats: FetchStats) -> tuple[str, list[list[Point]]]:
    relations = [e for e in elements if e.get("type") == "relation"]
    if not relations:
        raise ValueError("boundary query returned no relation")
    relation = relations[0]
    name = relation.get("tags", {}).get("name", "Unknown district")
    ways = [
        [(p["lat"], p["lon"]) for p in member.get("geometry") or []]
        for member in relation.get("members", [])
        if member.get("type") == "way" and member.get("geometry")
    ]
    stats.boundary_ways = len(ways)
    if not ways:
        raise ValueError(
            f"relation {relation.get('id')} returned no member geometry; "
            "the boundary query must use `out geom` (body mode), not `out tags`"
        )
    rings = assemble_rings(ways)
    stats.boundary_rings = len(rings)
    stats.closed_rings = sum(ring_is_closed(r) for r in rings)
    if stats.closed_rings == 0:
        stats.warnings.append(
            "no boundary ring closed cleanly; the district outline may be incomplete"
        )
    return name, rings


def _position(element: dict[str, Any]) -> Point | None:
    if element.get("type") == "node" and "lat" in element:
        return (element["lat"], element["lon"])
    center = element.get("center")
    if center:
        return (center["lat"], center["lon"])
    return None


def parse_addresses(elements: list[dict[str, Any]], stats: FetchStats) -> list[Address]:
    addresses: list[Address] = []
    stats.address_elements = len(elements)
    for element in elements:
        tags = element.get("tags", {})
        number = tags.get("addr:housenumber")
        street = tags.get("addr:street")
        if not number:
            continue
        if not street:
            stats.skipped_no_street += 1
            continue
        position = _position(element)
        if position is None:
            stats.skipped_no_position += 1
            continue
        addresses.append(
            Address(
                osm_id=f"{element['type'][0]}{element['id']}",
                lat=position[0],
                lon=position[1],
                number=str(number),
                street=street,
                unit=tags.get("addr:unit"),
                postcode=tags.get("addr:postcode"),
            )
        )
    stats.addresses_kept = len(addresses)
    if stats.skipped_no_street:
        stats.warnings.append(
            f"{stats.skipped_no_street} address points had no addr:street and were skipped"
        )
    return addresses


def parse_ways(elements: list[dict[str, Any]], stats: FetchStats) -> list[WalkWay]:
    ways: list[WalkWay] = []
    stats.way_elements = len(elements)
    for element in elements:
        geometry = [(p["lat"], p["lon"]) for p in element.get("geometry") or []]
        if len(geometry) < 2:
            continue
        tags = {k: v for k, v in element.get("tags", {}).items() if k in WAY_TAG_WHITELIST}
        ways.append(WalkWay(osm_id=element["id"], geometry=geometry, tags=tags))
    stats.ways_kept = len(ways)
    return ways


def fetch_district(
    client: OverpassClient,
    relation_id: int,
    timeout_s: int = 600,
    progress: Progress | None = None,
) -> tuple[DistrictSnapshot, FetchStats]:
    """Performs the one-time bulk extract for a whole electoral district."""
    say = progress or (lambda _msg: None)
    stats = FetchStats()

    say("fetching district boundary...")
    name, rings = parse_boundary(client.elements(boundary_query(relation_id, timeout_s)), stats)
    say(f"  {name}: {stats.boundary_ways} ways -> {stats.boundary_rings} ring(s)")

    say("fetching addresses inside the district...")
    addresses = parse_addresses(client.elements(addresses_query(relation_id, timeout_s)), stats)
    say(f"  {len(addresses)} addresses")

    say("fetching walkable street network...")
    ways = parse_ways(client.elements(ways_query(relation_id, timeout_s)), stats)
    say(f"  {len(ways)} walkable ways")

    snapshot = DistrictSnapshot(
        district_name=name,
        relation_id=relation_id,
        fetched_at=utc_now_iso(),
        rings=rings,
        addresses=addresses,
        ways=ways,
    )
    return snapshot, stats

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.geocode import spatial_clusters
from app.osm.snapshot import Address

APPROACH_SECONDS = 30.0
"""Footpath to front door and back: the cost of visiting a stop at all."""

PER_DOOR_SECONDS = 75.0
"""Knock, wait, converse, hand over a pamphlet. Matches `estimate_effort`."""

GATED_COMPLEX_DOOR_THRESHOLD = 8
"""A street number with this many doors is likely a gated block needing a human call."""

GATED_DWELL_CAP_DOORS = GATED_COMPLEX_DOOR_THRESHOLD
"""Doors charged at a gated block, however many are really behind the lobby.

A 95-door tower is not 95 walk-ups; it is one buzzer panel a pair can work for
about as long as a run of detached houses before moving on. Capping at the
gated threshold keeps dwell continuous - a 7-door stop and an 8-door stop cost
almost the same - so no stop becomes artificially attractive by having one
fewer door. `Stop.uncapped_dwell_seconds` keeps the honest full figure.
"""


@dataclass(frozen=True, slots=True)
class Stop:
    """One place a pair physically walks up to, and every door behind it.

    A stop is not a door. `2/14 Brenbeal Street` and `3/14 Brenbeal Street`
    are two doors at one stop, and routing over doors instead of stops would
    invent travel time between addresses that share a coordinate.
    """

    stop_id: str
    lat: float
    lon: float
    street: str
    number: str
    doors: tuple[Address, ...]

    @property
    def door_count(self) -> int:
        return len(self.doors)

    @property
    def units(self) -> tuple[str, ...]:
        return tuple(d.unit for d in self.doors if d.unit)

    @property
    def is_gated_candidate(self) -> bool:
        return self.door_count >= GATED_COMPLEX_DOOR_THRESHOLD

    @property
    def point(self) -> tuple[float, float]:
        return (self.lat, self.lon)

    @property
    def label(self) -> str:
        base = f"{self.number} {self.street}"
        return f"{base} ({self.door_count} doors)" if self.door_count > 1 else base

    @property
    def dwell_seconds(self) -> float:
        """Planning dwell, capped for probable gated blocks."""
        return dwell_seconds(self.door_count)

    @property
    def uncapped_dwell_seconds(self) -> float:
        """What knocking every door would really cost, cap ignored."""
        return dwell_seconds(self.door_count, cap_doors=None)

    @property
    def dwell_minutes(self) -> float:
        return self.dwell_seconds / 60

    def to_dict(self) -> dict:
        """GeoJSON feature properties.

        Deliberately omits the id and the coordinates: they belong to the
        enclosing Feature and its geometry, and repeating them across 24,366
        stops costs megabytes on the wire for a phone in the field.
        """
        payload = {
            "street": self.street,
            "number": self.number,
            "label": self.label,
            "door_count": self.door_count,
            "dwell_minutes": round(self.dwell_minutes, 1),
        }
        if self.is_gated_candidate:
            payload["gated_candidate"] = True
            payload["uncapped_dwell_minutes"] = round(self.uncapped_dwell_seconds / 60, 1)
        return payload


def dwell_seconds(
    door_count: int,
    approach: float = APPROACH_SECONDS,
    per_door: float = PER_DOOR_SECONDS,
    cap_doors: int | None = GATED_DWELL_CAP_DOORS,
) -> float:
    charged = door_count if cap_doors is None else min(door_count, cap_doors)
    return approach + per_door * charged


def stop_groups(addresses: list[Address]) -> list[list[Address]]:
    """Groups doors into stops by street name, number *and* spatial cluster.

    Street names are not unique within a district (two Mary Streets 5.8 km
    apart), so a (street, number) key alone would merge doors from distinct
    streets into one stop.
    """
    by_key: dict[tuple[str, str], list[Address]] = defaultdict(list)
    for address in addresses:
        by_key[(address.street, address.number)].append(address)
    return [
        cluster
        for group in by_key.values()
        for cluster in (spatial_clusters(group) if len(group) > 1 else [group])
    ]


def build_stops(addresses: list[Address]) -> list[Stop]:
    """Collapses doors into stops, ordered by street then house number."""
    stops = [_stop_from_doors(group) for group in stop_groups(addresses)]
    stops.sort(key=lambda s: (s.street, sort_key(s.number), s.stop_id))
    return stops


def _stop_from_doors(doors: list[Address]) -> Stop:
    ordered = sorted(doors, key=lambda d: d.osm_id)
    return Stop(
        # Keyed on the lowest OSM id in the group, so the id is stable across
        # runs however the addresses happen to be ordered in the snapshot.
        stop_id=f"s{ordered[0].osm_id}",
        lat=sum(d.lat for d in ordered) / len(ordered),
        lon=sum(d.lon for d in ordered) / len(ordered),
        street=ordered[0].street,
        number=ordered[0].number,
        doors=tuple(ordered),
    )


def sort_key(number: str) -> tuple[int, str]:
    """Orders house numbers numerically, so 9 precedes 10 and 10A follows 10."""
    digits = ""
    for char in number:
        if not char.isdigit():
            break
        digits += char
    return (int(digits) if digits else 0, number)

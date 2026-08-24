from __future__ import annotations

import difflib
import re
from collections import defaultdict
from dataclasses import dataclass

from app.osm.boundary import haversine_m
from app.osm.snapshot import Address

STREET_TYPE_ABBREVIATIONS = {
    "st": "street",
    "str": "street",
    "rd": "road",
    "ave": "avenue",
    "av": "avenue",
    "cres": "crescent",
    "cr": "crescent",
    "ct": "court",
    "crt": "court",
    "dr": "drive",
    "drv": "drive",
    "pde": "parade",
    "par": "parade",
    "gr": "grove",
    "gve": "grove",
    "tce": "terrace",
    "ter": "terrace",
    "pl": "place",
    "cl": "close",
    "blvd": "boulevard",
    "bvd": "boulevard",
    "hwy": "highway",
    "ln": "lane",
    "sq": "square",
    "esp": "esplanade",
    "cct": "circuit",
    "cir": "circle",
    "mws": "mews",
    "rse": "rise",
    "gdns": "gardens",
    "gdn": "garden",
    "pkwy": "parkway",
    "wlk": "walk",
    "gte": "gate",
    "grn": "green",
    "hts": "heights",
    "vw": "view",
}

NOISE_TOKENS = {"vic", "victoria", "australia", "aus", "au"}

STREET_CLUSTER_THRESHOLD_M = 200.0
"""Separates same-named-but-different streets from genuine multi-unit blocks.

Measured on the Kew district extract: multi-unit stops at a single street number
spread by at most 147 m (median 29 m), whereas the district contains two
distinct Mary Streets 5.8 km apart and two Henry Streets 5.0 km apart. Merging
those into one centroid would place a hub or a stop up to kilometres from the
real address, so they are reported as separate candidates instead.
"""

_UNIT_NUMBER_RE = re.compile(r"^\s*(?:(?:unit|apt|apartment|flat|u)\s*)?([0-9]+[a-z]?)\s*/\s*", re.I)
_LEADING_UNIT_WORD_RE = re.compile(r"^\s*(?:unit|apt|apartment|flat)\s+([0-9]+[a-z]?)\s*,?\s*", re.I)
_NUMBER_RE = re.compile(r"^\s*([0-9]+[a-z]?(?:\s*-\s*[0-9]+[a-z]?)?)\s*", re.I)
_LEADING_NUMBER_RE = re.compile(r"^(\d+[a-z]?)", re.I)
_RANGE_RE = re.compile(r"^(\d+)[a-z]?\s*-\s*(\d+)[a-z]?$", re.I)

_MAX_RANGE_SPAN = 100
"""Ranges wider than this are treated as data errors and not expanded."""


@dataclass(frozen=True, slots=True)
class GeocodeCandidate:
    label: str
    lat: float
    lon: float
    street: str
    number: str | None
    door_count: int
    match_type: str
    score: float

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "lat": self.lat,
            "lon": self.lon,
            "street": self.street,
            "number": self.number,
            "door_count": self.door_count,
            "match_type": self.match_type,
            "score": round(self.score, 3),
        }


def normalise_street(value: str) -> str:
    """Lowercase, drop punctuation and expand Australian street-type abbreviations."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", value.lower())
    tokens = [t for t in cleaned.split() if t and t not in NOISE_TOKENS and not _is_postcode(t)]
    return " ".join(STREET_TYPE_ABBREVIATIONS.get(t, t) for t in tokens)


def _is_postcode(token: str) -> bool:
    return len(token) == 4 and token.isdigit()


def normalise_number(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def number_keys(number: str) -> list[str]:
    """Index keys for a house number, covering OSM ranges like ``31-37``.

    A range record should be findable as typed in full and by any number it
    covers, which is what a volunteer reads off a letterbox. Ranges step by 2
    when both ends share parity (the Australian one-side-of-the-street
    convention), otherwise by 1.
    """
    full = normalise_number(number)
    keys = [full]
    stripped = number.strip().lower()
    if match := _RANGE_RE.match(stripped):
        low, high = int(match.group(1)), int(match.group(2))
        if low < high and high - low <= _MAX_RANGE_SPAN:
            step = 2 if (high - low) % 2 == 0 else 1
            keys.extend(
                key for v in range(low, high + 1, step) if (key := str(v)) not in keys
            )
            return keys
    if match := _LEADING_NUMBER_RE.match(stripped):
        leading = normalise_number(match.group(1))
        if leading != full:
            keys.append(leading)
    return keys


def parse_query(query: str) -> tuple[str | None, str | None, str]:
    """Splits free text into (unit, house number, remaining street text)."""
    text = query.strip()
    unit = None

    if match := _LEADING_UNIT_WORD_RE.match(text):
        unit = match.group(1)
        text = text[match.end() :]
    elif match := _UNIT_NUMBER_RE.match(text):
        unit = match.group(1)
        text = text[match.end() :]

    number = None
    if match := _NUMBER_RE.match(text):
        number = match.group(1)
        text = text[match.end() :]

    text = text.split(",")[0]
    return unit, number, text.strip()


def suffix_variants(text: str) -> list[str]:
    """Progressively trims trailing tokens so trailing suburb names fall away.

    ``"yerrin street balwyn north"`` yields ``"yerrin street balwyn north"``,
    ``"yerrin street balwyn"``, then ``"yerrin street"`` - the last of which is
    the real street name. Longest first, so an exact match always wins.
    """
    tokens = normalise_street(text).split()
    return [" ".join(tokens[:i]) for i in range(len(tokens), 0, -1)]


class LocalGeocoder:
    """Geocodes against the district's own cached addresses.

    Using the local extract instead of a remote geocoder removes the last
    external dependency and, more importantly, makes it impossible to resolve
    to a point outside the district - the failure mode that sends a pair of
    volunteers 1.6 km astray when a suburb has two accepted name orderings.
    """

    def __init__(self, addresses: list[Address]) -> None:
        self.addresses = addresses
        self._by_street: dict[str, list[Address]] = defaultdict(list)
        self._by_key: dict[tuple[str, str], list[Address]] = defaultdict(list)
        for address in addresses:
            street = normalise_street(address.street)
            self._by_street[street].append(address)
            for key in number_keys(address.number):
                self._by_key[(street, key)].append(address)
        self._streets = sorted(self._by_street)
        self._street_cluster_cache: dict[str, list[list[Address]]] = {}

    @property
    def street_count(self) -> int:
        return len(self._streets)

    def search(self, query: str, limit: int = 8) -> list[GeocodeCandidate]:
        if not query or not query.strip():
            return []
        _unit, number, street_text = parse_query(query)
        streets = self._resolve_streets(suffix_variants(street_text))
        if not streets:
            return []

        candidates: list[GeocodeCandidate] = []
        for street, score, match_type in streets:
            if number:
                candidates.extend(self._number_candidates(street, number, score, match_type, limit))
            else:
                candidates.extend(self._street_candidates(street, score))
        candidates.sort(key=lambda c: -c.score)
        return candidates[:limit]

    def nearest(self, lat: float, lon: float) -> Address | None:
        if not self.addresses:
            return None
        return min(self.addresses, key=lambda a: haversine_m((lat, lon), a.point))

    def _resolve_streets(self, variants: list[str]) -> list[tuple[str, float, str]]:
        for variant in variants:
            if variant in self._by_street:
                return [(variant, 1.0, "exact")]
        best: dict[str, float] = {}
        for variant in variants:
            if len(variant) < 4:
                continue
            for match in difflib.get_close_matches(variant, self._streets, n=3, cutoff=0.8):
                ratio = difflib.SequenceMatcher(None, variant, match).ratio()
                if ratio > best.get(match, 0.0):
                    best[match] = ratio
        ranked = sorted(best.items(), key=lambda kv: -kv[1])[:3]
        return [(street, ratio, "fuzzy") for street, ratio in ranked]

    def _number_candidates(
        self, street: str, number: str, street_score: float, match_type: str, limit: int
    ) -> list[GeocodeCandidate]:
        for key in number_keys(number):
            if exact := self._by_key.get((street, key)):
                return self._candidates_for(exact, street_score, match_type)
        return self._nearby_number_candidates(street, number, street_score, limit)

    def _candidates_for(
        self, group: list[Address], score: float, match_type: str
    ) -> list[GeocodeCandidate]:
        clusters = spatial_clusters(group)
        ambiguous = len(clusters) > 1
        return [
            self._cluster_candidate(cluster, score - 0.01 * i, match_type, ambiguous)
            for i, cluster in enumerate(clusters)
        ]

    def _nearby_number_candidates(
        self, street: str, number: str, street_score: float, limit: int
    ) -> list[GeocodeCandidate]:
        target = _numeric_part(number)
        if target is None:
            return [self._street_candidate(street, street_score * 0.6)]
        clusters: dict[str, list[Address]] = defaultdict(list)
        for address in self._by_street[street]:
            clusters[normalise_number(address.number)].append(address)
        scored = []
        for group in clusters.values():
            value = _numeric_part(group[0].number)
            if value is None:
                continue
            scored.append((abs(value - target), group))
        scored.sort(key=lambda item: item[0])
        return [
            candidate
            for _distance, group in scored[: max(1, min(limit, 3))]
            for candidate in self._candidates_for(group, street_score * 0.7, "approximate")
        ]

    def _cluster_candidate(
        self, group: list[Address], score: float, match_type: str, ambiguous: bool = False
    ) -> GeocodeCandidate:
        lat = sum(a.lat for a in group) / len(group)
        lon = sum(a.lon for a in group) / len(group)
        first = group[0]
        label = f"{first.number} {first.street}"
        if len(group) > 1:
            label += f" ({len(group)} doors)"
        if ambiguous:
            label += f" - {self._disambiguator(group)}"
        return GeocodeCandidate(
            label=label,
            lat=lat,
            lon=lon,
            street=first.street,
            number=first.number,
            door_count=len(group),
            match_type=match_type,
            score=score,
        )

    def _street_candidates(self, street: str, score: float) -> list[GeocodeCandidate]:
        clusters = self._street_clusters(street)
        ambiguous = len(clusters) > 1
        candidates = []
        for index, cluster in enumerate(clusters):
            label = f"{cluster[0].street} ({len(cluster)} doors)"
            if ambiguous:
                label += f" - {self._disambiguator(cluster)}"
            candidates.append(
                GeocodeCandidate(
                    label=label,
                    lat=sum(a.lat for a in cluster) / len(cluster),
                    lon=sum(a.lon for a in cluster) / len(cluster),
                    street=cluster[0].street,
                    number=None,
                    door_count=len(cluster),
                    match_type="street",
                    score=score * 0.9 - 0.01 * index,
                )
            )
        return candidates

    def _street_clusters(self, street: str) -> list[list[Address]]:
        if street not in self._street_cluster_cache:
            self._street_cluster_cache[street] = spatial_clusters(self._by_street[street])
        return self._street_cluster_cache[street]

    def _disambiguator(self, group: list[Address]) -> str:
        """A short hint so two same-named streets can be told apart in a list."""
        postcodes = {a.postcode for a in group if a.postcode}
        if len(postcodes) == 1:
            return postcodes.pop()
        crossing = self._nearest_other_street(group[0])
        if crossing:
            return f"near {crossing}"
        return f"{group[0].lat:.4f}, {group[0].lon:.4f}"

    def _nearest_other_street(self, address: Address) -> str | None:
        others = [a for a in self.addresses if a.street != address.street]
        if not others:
            return None
        return min(others, key=lambda a: haversine_m(address.point, a.point)).street


def _numeric_part(value: str) -> int | None:
    match = re.match(r"^(\d+)", value)
    return int(match.group(1)) if match else None


def spatial_clusters(
    addresses: list[Address], threshold_m: float = STREET_CLUSTER_THRESHOLD_M
) -> list[list[Address]]:
    """Single-link clustering, so a chain of neighbouring houses stays together.

    Consecutive houses on one street are ~20 m apart and remain a single
    cluster however long the street, while a second street of the same name
    elsewhere in the district forms its own cluster.
    """
    remaining = list(addresses)
    clusters: list[list[Address]] = []
    while remaining:
        cluster = [remaining.pop()]
        grew = True
        while grew:
            grew = False
            for index in range(len(remaining) - 1, -1, -1):
                point = remaining[index].point
                if any(haversine_m(point, member.point) <= threshold_m for member in cluster):
                    cluster.append(remaining.pop(index))
                    grew = True
        clusters.append(cluster)
    clusters.sort(key=len, reverse=True)
    return clusters

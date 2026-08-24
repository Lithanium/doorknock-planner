from __future__ import annotations

from app.blockface import Blockface, build_blockfaces
from app.osm.snapshot import Address, WalkWay
from app.stops import build_stops
from app.territory import TerritoryPlan, build_territories

# A hand-built grid of quiet residential streets. Rows run east-west, columns
# run north-south (and carry no doors, so they exist purely to create
# intersections). Row spacing is ~220 m and column spacing ~175 m, so
# neighbouring blocks are within territory adjacency range of each other.
ROW_LAT = [-37.800 - i * 0.002 for i in range(4)]
COL_LON = [145.050 + j * 0.002 for j in range(5)]

HUB = (ROW_LAT[1] - 0.001, COL_LON[2])  # in the middle of the grid


def _door(osm_id, lat, lon, number, street):
    return Address(osm_id=osm_id, lat=lat, lon=lon, number=number, street=street)


def _doors_along(row, col, street, start_number, step=1):
    a = (ROW_LAT[row], COL_LON[col])
    b = (ROW_LAT[row], COL_LON[col + 1])
    return [
        _door(
            f"d{row}.{col}.{k}",
            a[0] + 0.0002,
            a[1] + (b[1] - a[1]) * fraction,
            str(start_number + k * step),
            street,
        )
        for k, fraction in enumerate((0.2, 0.4, 0.6, 0.8))
    ]


def _column_ways(next_id=100):
    return [
        WalkWay(
            next_id + j,
            [(lat, COL_LON[j]) for lat in ROW_LAT],
            {"highway": "residential", "name": f"Column {j} Street", "maxspeed": "50"},
        )
        for j in range(len(COL_LON))
    ]


def _grid(street_name) -> list[Blockface]:
    """Blockfaces for the full grid; `street_name(row, col)` names each segment."""
    ways = _column_ways()
    doors = []
    next_id = 1
    for row in range(len(ROW_LAT)):
        for col in range(len(COL_LON) - 1):
            name = street_name(row, col)
            ways.append(
                WalkWay(
                    next_id,
                    [(ROW_LAT[row], COL_LON[col]), (ROW_LAT[row], COL_LON[col + 1])],
                    {"highway": "residential", "name": name, "maxspeed": "50"},
                )
            )
            next_id += 1
            doors.extend(_doors_along(row, col, name, start_number=col * 4 + 1))
    return build_blockfaces(build_stops(doors), ways)


def _distinct_street_grid() -> list[Blockface]:
    return _grid(lambda row, col: f"Row {row} Part {col} Street")


def _whole_street_grid() -> list[Blockface]:
    return _grid(lambda row, col: f"Row {row} Street")


def _team_of(plan: TerritoryPlan) -> dict[str, int]:
    return {
        b.blockface_id: t.team for t in plan.territories for b in t.blockfaces
    }


class TestPartition:
    def test_every_blockface_lands_in_exactly_one_territory(self):
        faces = _distinct_street_grid()
        plan = build_territories(faces, HUB, teams=3)
        assigned = [b.blockface_id for t in plan.territories for b in t.blockfaces]
        assert sorted(assigned) == sorted(b.blockface_id for b in faces)

    def test_one_team_gets_everything(self):
        faces = _distinct_street_grid()
        plan = build_territories(faces, HUB, teams=1)
        assert len(plan.territories) == 1
        assert len(plan.territories[0].blockfaces) == len(faces)
        assert plan.territories[0].contiguous
        assert plan.spread_pct == 0.0

    def test_more_teams_than_streets_leaves_the_extras_empty(self):
        faces = _distinct_street_grid()[:2]
        plan = build_territories(faces, HUB, teams=6)
        non_empty = [t for t in plan.territories if t.blockfaces]
        assert len(plan.territories) == 6
        assert 1 <= len(non_empty) <= 2
        assert all(t.contiguous for t in plan.territories if not t.blockfaces)

    def test_no_blockfaces_yield_empty_territories(self):
        plan = build_territories([], HUB, teams=3)
        assert len(plan.territories) == 3
        assert all(not t.blockfaces for t in plan.territories)
        assert plan.spread_pct == 0.0

    def test_the_same_input_partitions_the_same_way_twice(self):
        first = build_territories(_distinct_street_grid(), HUB, teams=4)
        second = build_territories(_distinct_street_grid(), HUB, teams=4)
        assert _team_of(first) == _team_of(second)


class TestBalanceAndContiguity:
    def test_teams_come_out_within_a_quarter_of_each_other(self):
        faces = _distinct_street_grid()
        for teams in (2, 3, 4):
            plan = build_territories(faces, HUB, teams=teams)
            assert plan.spread_pct <= 0.25, (
                f"{teams} teams spread {plan.spread_pct:.0%}: "
                f"{[round(t.minutes) for t in plan.territories]}"
            )

    def test_every_territory_is_contiguous_on_a_connected_grid(self):
        faces = _distinct_street_grid()
        for teams in (2, 3, 4):
            plan = build_territories(faces, HUB, teams=teams)
            assert all(t.contiguous for t in plan.territories)

    def test_a_detached_pocket_is_reported_not_hidden(self):
        near = _distinct_street_grid()
        far_doors = [
            _door(f"f{k}", -37.9000, 145.2000 + k * 0.0001, str(k * 2 + 1), "Far Street")
            for k in range(4)
        ]
        far_way = WalkWay(
            999,
            [(-37.9002, 145.2000), (-37.9002, 145.2004)],
            {"highway": "residential", "name": "Far Street", "maxspeed": "50"},
        )
        faces = near + build_blockfaces(build_stops(far_doors), [far_way])
        plan = build_territories(faces, HUB, teams=1)
        assert plan.territories[0].contiguous is False


class TestStreetIntegrity:
    def test_a_street_is_never_split_between_teams(self):
        faces = _whole_street_grid()
        plan = build_territories(faces, HUB, teams=2)
        assert plan.split_streets == []
        for territory in plan.territories:
            for street in territory.streets:
                owners = {
                    t.team
                    for t in plan.territories
                    for b in t.blockfaces
                    if b.street == street
                }
                assert owners == {territory.team}, f"{street} split across {owners}"

    def test_a_street_bigger_than_a_team_share_is_split_and_reported(self):
        # Giant Street holds ~10x the work of everything else, so keeping it
        # whole would hand one team almost the entire session.
        giant_doors = [
            _door(f"g{k}", -37.8002, 145.050 + k * 0.00012, str(k + 1), "Giant Street")
            for k in range(80)
        ]
        giant_way = WalkWay(
            50,
            [(-37.8004, 145.050), (-37.8004, 145.060)],
            {"highway": "residential", "name": "Giant Street", "maxspeed": "50"},
        )
        small_doors = [
            _door(f"s{k}", -37.8022, 145.050 + k * 0.0004, str(k * 2 + 1), "Small Street")
            for k in range(4)
        ]
        small_way = WalkWay(
            51,
            [(-37.8024, 145.050), (-37.8024, 145.0516)],
            {"highway": "residential", "name": "Small Street", "maxspeed": "50"},
        )
        faces = build_blockfaces(
            build_stops(giant_doors + small_doors), [giant_way, small_way]
        )
        plan = build_territories(faces, (-37.8010, 145.052), teams=2)
        assert plan.split_streets == ["Giant Street"]
        assigned = [b.blockface_id for t in plan.territories for b in t.blockfaces]
        assert sorted(assigned) == sorted(b.blockface_id for b in faces)
        assert plan.spread_pct <= 0.6  # far better than the 10x it started at

    def test_both_sides_of_a_busy_road_go_to_the_same_team(self):
        rows = {}
        ways = _column_ways()
        doors = []
        for row in range(len(ROW_LAT)):
            name = f"Busy Row {row} Road"
            rows[row] = name
            ways.append(
                WalkWay(
                    row + 1,
                    [(ROW_LAT[row], COL_LON[0]), (ROW_LAT[row], COL_LON[-1])],
                    {"highway": "primary", "name": name, "lanes": "4"},
                )
            )
            for col in range(len(COL_LON) - 1):
                doors.extend(_doors_along(row, col, name, start_number=col * 4 + 1))
        faces = build_blockfaces(build_stops(doors), ways)
        assert any(f.side != "both" for f in faces)
        plan = build_territories(faces, HUB, teams=2)
        team_of = _team_of(plan)
        for face in faces:
            base = face.blockface_id.rsplit("#", 1)[0]
            partners = [f for f in faces if f.blockface_id.rsplit("#", 1)[0] == base]
            assert {team_of[p.blockface_id] for p in partners} == {
                team_of[face.blockface_id]
            }

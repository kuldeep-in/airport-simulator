"""
Taxiway/runway network graph, built from the same hiaWays.json data the 3D
scene uses to draw the pavement (AirportGround.jsx). Used to route seeded
aircraft along real pavement instead of the old hand-picked waypoint lists,
which were an independent, unvalidated dataset that frequently drifted off
the drawn taxiway lines (confirmed visually — see project notes).

data/hiaWays.json here is a synced copy of frontend/src/data/hiaWays.json —
kept in the backend's own data/ dir so this module doesn't depend on the
frontend source tree layout at deploy time. Its points are in the
frontend's WORLD units (1 unit = 10 m, origin at CENTER_LON/CENTER_LAT —
see frontend/src/geo.js). We replicate that same transform here (inverted)
so routes can be computed in metres and returned as (lat, lon) pairs for
seed_runner.py.

Validated (see project notes): the network is a single connected component
(4445 points, 0 disconnected islands, 0 degenerate/zero-length segments) —
hiaWays.json itself needed no correction. Touchdown points already sit
1-8 m from the network. hiaWays.json also carries real OSM
aeroway=parking_position lead-in lines for 33 of the 61 gates (each tagged
with `ref` + `gate_end` — see load()'s `_gate_leadin_node`); a route ends
right on that real pavement point when the destination gate has one (passed
in as `route()`'s `gate_id`), and falls back to the hand-placed gate marker
coordinate otherwise. This is deliberately a verified per-gate lookup, not a
"nearest node within N metres" proximity check — a gate with no real lead-in
can still have some unrelated taxiway pass close by with a completely
unrelated heading, which pointed parked aircraft the wrong way when tried
(see project notes).
"""

import heapq
import json
import math
import os

CENTER_LON = 51.611
CENTER_LAT = 25.266
LON_SCALE = (111320 * math.cos(math.radians(CENTER_LAT))) / 10
LAT_SCALE = 111320 / 10

_WAYS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "hiaWays.json")
_SNAP_TOL = 0.05  # world units (~0.5 m) — matches validated connectivity check
_ROLLOUT_M = 2200.0  # typical landing-roll + runway-exit distance before joining the taxiway network
_ROLLOUT_SNAP_M = 80.0  # touchdown must be within this of a runway centerline to get a rollout at all
_MIN_EXIT_ANGLE_DEG = 75.0  # reject shallow/near-parallel connectors — real turnoffs are close to perpendicular
_MIN_EXIT_SEPARATION_M = 80.0  # taxiway nodes within this of each other are the same physical exit, not distinct ones
# Weight (metres) added to a graph edge when another flight is already taxiing it
# in the OPPOSING direction (see route_reservations.py). Far larger than any real
# route (~4-9 km) so Dijkstra prefers ANY clear alternative, yet finite — a
# genuinely single-lane against-traffic segment is still routable rather than
# failing (traffic.py stays the runtime backstop). Soft, not a hard ban.
_AVOID_PENALTY_M = 50_000.0


def _world_to_lonlat(x: float, z: float) -> tuple[float, float]:
    lon = x / LON_SCALE + CENTER_LON
    lat = CENTER_LAT - z / LAT_SCALE
    return lon, lat


def _lonlat_to_world(lon: float, lat: float) -> tuple[float, float]:
    x = (lon - CENTER_LON) * LON_SCALE
    z = -(lat - CENTER_LAT) * LAT_SCALE
    return x, z


class TaxiwayGraph:
    def __init__(self):
        self._node_xz: list[tuple[float, float]] = []   # node id -> (x, z)
        self._adj: dict[int, list[tuple[int, float, bool]]] = {}  # node id -> [(neighbor, weight_m, is_runway), ...]
        self._grid: dict[tuple[int, int], list[int]] = {}
        self._cell = _SNAP_TOL * 4
        self._runways: list[list[tuple[float, float]]] = []  # raw centerline polylines, world units
        self._taxiway_nodes: set[int] = set()  # nodes with >=1 non-runway edge — real taxiway exits/intersections
        self._gate_leadin_node: dict[str, int] = {}  # gate ref -> node id of its real aeroway=parking_position end
        self._loaded = False

    def _grid_key(self, x: float, z: float) -> tuple[int, int]:
        return (round(x / self._cell), round(z / self._cell))

    def _get_or_create_node(self, x: float, z: float) -> int:
        key = self._grid_key(x, z)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for nid in self._grid.get((key[0] + dx, key[1] + dz), []):
                    nx, nz = self._node_xz[nid]
                    if math.hypot(x - nx, z - nz) <= _SNAP_TOL:
                        return nid
        nid = len(self._node_xz)
        self._node_xz.append((x, z))
        self._grid.setdefault(key, []).append(nid)
        self._adj[nid] = []
        return nid

    def _find_node(self, x: float, z: float) -> int | None:
        """Look up an existing node without creating one — for points already loaded."""
        key = self._grid_key(x, z)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for nid in self._grid.get((key[0] + dx, key[1] + dz), []):
                    nx, nz = self._node_xz[nid]
                    if math.hypot(x - nx, z - nz) <= _SNAP_TOL:
                        return nid
        return None

    def _add_edge(self, a: int, b: int, is_runway: bool) -> None:
        if a == b:
            return
        ax, az = self._node_xz[a]
        bx, bz = self._node_xz[b]
        w = math.hypot(ax - bx, az - bz) * 10.0  # world units -> metres
        self._adj[a].append((b, w, is_runway))
        self._adj[b].append((a, w, is_runway))
        if not is_runway:
            self._taxiway_nodes.add(a)
            self._taxiway_nodes.add(b)

    def load(self) -> None:
        if self._loaded:
            return
        path = _WAYS_PATH
        with open(path) as f:
            ways = json.load(f)
        # hiaWays.json is already in world units (10 m, origin at CENTER).
        for way in ways:
            pts = [(x, z) for x, z in way["p"]]
            is_runway = way.get("t") == "runway"
            if is_runway:
                self._runways.append([(x, z) for x, z in pts])
            prev = None
            for x, z in pts:
                nid = self._get_or_create_node(x, z)
                if prev is not None and prev != nid:
                    self._add_edge(prev, nid, is_runway)
                prev = nid
            # `ref` + `gate_end` are only set on real OSM aeroway=parking_position
            # lead-in ways (added separately from the base network) — `gate_end`
            # is the endpoint closest to that gate's marker coordinate, baked in
            # ahead of time rather than guessed here, since a lead-in's two ends
            # aren't otherwise distinguishable at load time.
            ref = way.get("ref")
            gate_end = way.get("gate_end")
            if ref and gate_end is not None:
                self._gate_leadin_node[ref] = self._get_or_create_node(*gate_end)
        self._loaded = True
        print(f"[taxiway_graph] loaded {len(self._node_xz)} nodes from {len(ways)} ways ({path})")

    def _nearest_node(self, x: float, z: float) -> int:
        best_id, best_d = -1, float("inf")
        for nid, (nx, nz) in enumerate(self._node_xz):
            d = (nx - x) ** 2 + (nz - z) ** 2
            if d < best_d:
                best_d, best_id = d, nid
        return best_id

    def _nearest_taxiway_node(self, x: float, z: float) -> int:
        """Like _nearest_node, but restricted to nodes with >=1 non-runway
        edge. A hand-picked "runway exit" coordinate can snap to a pure-runway
        node (both its edges are runway edges) — with forbid_runway=True,
        Dijkstra from there can't move at all, since it has no legal edge to
        leave on. Restricting the snap itself to a real taxiway junction
        guarantees at least one usable edge to start from."""
        best_id, best_d = -1, float("inf")
        for nid in self._taxiway_nodes:
            nx, nz = self._node_xz[nid]
            d = (nx - x) ** 2 + (nz - z) ** 2
            if d < best_d:
                best_d, best_id = d, nid
        return best_id

    def _dijkstra(self, src: int, dst: int, forbid_runway: bool = False,
                  avoid_edges: set[tuple[int, int]] | None = None) -> list[int] | None:
        dist = {src: 0.0}
        prev: dict[int, int] = {}
        pq = [(0.0, src)]
        visited = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            if u == dst:
                break
            for v, w, is_runway in self._adj[u]:
                if forbid_runway and is_runway:
                    continue
                nd = d + w
                # Penalise (don't forbid) an edge another flight is taxiing the
                # OPPOSING way, so the router steers around a head-on when it can
                # but still finds a path when the against-traffic segment is the
                # only option. See route_reservations.blocked_for().
                if avoid_edges and (u, v) in avoid_edges:
                    nd += _AVOID_PENALTY_M
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if dst not in dist:
            return None
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        path.reverse()
        return path

    def _has_square_exit(self, nid: int, tangent_dx: float, tangent_dz: float,
                          min_angle_deg: float = _MIN_EXIT_ANGLE_DEG) -> bool:
        """True if `nid` has a non-runway edge whose angle to the runway tangent
        (`tangent_dx`, `tangent_dz`) is at least `min_angle_deg` — i.e. a real
        turnoff, not a shallow/near-parallel taxiway that merely touches the
        runway node in passing."""
        tlen = math.hypot(tangent_dx, tangent_dz)
        if tlen == 0:
            return False
        nx, nz = self._node_xz[nid]
        for v, _w, is_runway in self._adj[nid]:
            if is_runway:
                continue
            vx, vz = self._node_xz[v]
            ex, ez = vx - nx, vz - nz
            elen = math.hypot(ex, ez)
            if elen == 0:
                continue
            cos_a = max(-1.0, min(1.0, abs(ex * tangent_dx + ez * tangent_dz) / (elen * tlen)))
            if math.degrees(math.acos(cos_a)) >= min_angle_deg:
                return True
        return False

    def _rollout(self, touchdown_lonlat, heading_lonlat, rollout_m=_ROLLOUT_M,
                 one_exit_further: bool = False):
        """Trace the landing roll down the real runway centerline from touchdown.

        Default (`one_exit_further=False`): ends one real (~90°) taxiway exit
        *before* the first one at or beyond `rollout_m` — i.e. the exit a real
        crew would actually take, not the first one past the theoretical
        minimum rollout. Used for narrow-bodies with a short landing roll.

        `one_exit_further=True`: heavier aircraft (777/A350/A380) need a
        longer landing roll — starts from that same narrow-body exit, then
        takes the next physically distinct *square* (~90°) exit further down
        the runway, at least `_MIN_EXIT_SEPARATION_M` past it (so it's not
        just an adjacent digitization vertex of the same exit). Heavy
        aircraft can't take a shallow/diagonal turnoff off a runway at speed,
        so — unlike the old behaviour — this never falls back to a
        non-square connector just because it's the next taxiway link.

        Returns (anchor_lonlat, [(lat, lon), ...]) — `anchor_lonlat` is a real
        taxiway-connected node (never a mid-runway point, so post-rollout
        routing can safely forbid further runway travel and still be
        connected); the list is the intermediate centerline points between
        touchdown and the anchor (exclusive of touchdown, inclusive of anchor).
        Returns None if touchdown isn't actually near a runway.
        """
        tx, tz = _lonlat_to_world(*touchdown_lonlat)
        best = None  # (dist_m, seg_i, arclen_m, cum_m, pts)
        for pts in self._runways:
            cum = [0.0]
            for i in range(1, len(pts)):
                cum.append(cum[-1] + math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) * 10.0)
            for i in range(len(pts) - 1):
                x1, z1 = pts[i]
                x2, z2 = pts[i + 1]
                dx, dz = x2 - x1, z2 - z1
                seg_len2 = dx * dx + dz * dz
                if seg_len2 == 0:
                    continue
                t = ((tx - x1) * dx + (tz - z1) * dz) / seg_len2
                t = max(0.0, min(1.0, t))
                px, pz = x1 + t * dx, z1 + t * dz
                d = math.hypot(tx - px, tz - pz) * 10.0
                if best is None or d < best[0]:
                    arclen = cum[i] + t * math.hypot(dx, dz) * 10.0
                    best = (d, i, arclen, cum, pts)
        if best is None or best[0] > _ROLLOUT_SNAP_M:
            return None
        _, seg_i, arclen, cum, pts = best
        total_len = cum[-1]

        hx, hz = _lonlat_to_world(*heading_lonlat)
        seg_dx = pts[seg_i + 1][0] - pts[seg_i][0]
        seg_dz = pts[seg_i + 1][1] - pts[seg_i][1]
        move_dx, move_dz = hx - tx, hz - tz
        sign = 1.0 if (seg_dx * move_dx + seg_dz * move_dz) >= 0 else -1.0

        target_arclen = max(0.0, min(total_len, arclen + sign * rollout_m))

        # Walk the runway's own vertices, in travel direction, and collect:
        # - every real (~90°) taxiway exit (right angle to the runway, not a
        #   shallow/near-parallel connector) — used for the narrow-body pick
        # - the same square exits, deduped by `_MIN_EXIT_SEPARATION_M` — used
        #   to step "one exit further" without landing on an adjacent
        #   digitization vertex of the exit already picked
        indices = range(seg_i + 1, len(pts)) if sign > 0 else range(seg_i, -1, -1)
        square_exits = []          # [i, ...] real (~90°) exits, in travel order, whole runway
        square_exits_distinct = [] # square_exits, deduped, in travel order, whole runway
        fallback_i = None    # any taxiway exit, regardless of angle — used only if no square one exists
        for i in indices:
            nid = self._find_node(*pts[i])
            if nid is None or nid not in self._taxiway_nodes:
                continue
            if fallback_i is None:
                fallback_i = i
            if self._has_square_exit(nid, seg_dx, seg_dz):
                square_exits.append(i)
                if (not square_exits_distinct
                        or abs(cum[i] - cum[square_exits_distinct[-1]]) > _MIN_EXIT_SEPARATION_M):
                    square_exits_distinct.append(i)

        # Pick the first square exit at/beyond the target rollout, then back
        # off to the one immediately before it — real aircraft use an earlier
        # exit than the theoretical minimum-rollout one, not the first one
        # past it. This is the narrow-body pick, and also the base that
        # `one_exit_further` steps forward from.
        anchor_i = None
        past = lambda i: cum[i] >= target_arclen if sign > 0 else cum[i] <= target_arclen
        for j, i in enumerate(square_exits):
            if past(i):
                anchor_i = square_exits[j - 1] if j > 0 else i
                break
        if anchor_i is None and square_exits:
            anchor_i = square_exits[-1]
        if anchor_i is None:
            anchor_i = fallback_i if fallback_i is not None else (len(pts) - 1 if sign > 0 else 0)

        if one_exit_further and square_exits_distinct:
            # Take the next *square* (near-perpendicular), physically distinct
            # exit further down the runway — not just the next taxiway link of
            # any angle. Heavy aircraft (777/A350/A380) still need a
            # close-to-right-angle turnoff; a shallow/diagonal connector isn't
            # a turn they can physically make off a runway at speed, even if
            # it's the nearest taxiway-connected node.
            k = min(range(len(square_exits_distinct)),
                    key=lambda k: abs(cum[square_exits_distinct[k]] - cum[anchor_i]))
            anchor_i = (square_exits_distinct[k + 1] if k + 1 < len(square_exits_distinct)
                        else square_exits_distinct[k])

        if sign > 0:
            mid_xz = [pts[i] for i in range(seg_i + 1, anchor_i + 1)]
        else:
            mid_xz = [pts[i] for i in range(seg_i, anchor_i - 1, -1)]

        ax, az = mid_xz[-1] if mid_xz else pts[seg_i]
        anchor_lon, anchor_lat = _world_to_lonlat(ax, az)
        rollout_latlon = [(lat, lon) for lon, lat in (_world_to_lonlat(x, z) for x, z in mid_xz)]
        return (anchor_lon, anchor_lat), rollout_latlon

    def route(self, start_lonlat: tuple[float, float], gate_lonlat: tuple[float, float],
              heading_lonlat: tuple[float, float] | None = None,
              one_exit_further: bool = False,
              avoid_runway: bool = False,
              gate_id: str | None = None,
              avoid_edges: set[tuple[int, int]] | None = None,
              return_nodes: bool = False):
        """Route from `start_lonlat` to `gate_lonlat`, returns [(lat, lon), ...]
        or None if routing failed (caller should fall back to a straight line).

        `avoid_edges` (directed graph edges from route_reservations.blocked_for)
        are penalised so the route steers clear of head-on traffic where it can.
        `return_nodes=True` returns `(coords, node_path)` instead of just coords
        (`(None, None)` on failure) — the node_path is the flight's reservation.

        If `heading_lonlat` (the next recorded point after touchdown, used only
        to tell which way down the runway the aircraft is travelling) is given
        and `start_lonlat` is actually on a runway, a forced landing-roll segment
        down the real runway centerline is inserted before taxiway routing takes
        over — otherwise Dijkstra would just cut straight to the nearest exit.
        `one_exit_further` steps the exit choice one further down the runway
        for heavier aircraft that need a longer roll — see `_rollout`.

        `avoid_runway` forbids the Dijkstra leg from using runway edges even
        when there's no rollout (e.g. routing a departure from its gate to a
        taxiway holding point) — without it, Dijkstra is free to cut across
        runway pavement as if it were just another taxiway, which real
        aircraft can't do.

        `gate_id` (e.g. "B3") lets the route end on that gate's real mapped
        aeroway=parking_position lead-in when one exists (`self._gate_leadin_node`,
        built in load()) instead of the final synthetic hop to `gate_lonlat`.
        Deliberately NOT a blind "nearest node within N metres" check — a gate
        with no real lead-in can still have some unrelated taxiway pass close
        by (e.g. A7, ~32 m from a through-taxiway with an unrelated heading),
        and stopping there pointed the parked aircraft in a wrong, arbitrary
        direction (`_park_heading` in seed_runner.py is the bearing of the
        route's last segment). Only a *verified* lead-in for this exact gate
        is trusted to end pointed the right way.
        """
        self.load()
        out = [(start_lonlat[1], start_lonlat[0])]  # (lat, lon) — original start, not the snap
        route_from = start_lonlat
        rolled_out = False
        if heading_lonlat is not None:
            rolled = self._rollout(start_lonlat, heading_lonlat, one_exit_further=one_exit_further)
            if rolled is not None:
                route_from = rolled[0]
                out.extend(rolled[1])
                rolled_out = True

        sx, sz = _lonlat_to_world(*route_from)
        gx, gz = _lonlat_to_world(*gate_lonlat)
        forbid_runway = rolled_out or avoid_runway
        # A hand-picked exit/holding coordinate can snap to a pure-runway
        # node (both its edges are runway edges) — with runway edges
        # forbidden, Dijkstra couldn't leave a node like that at all. Snap
        # to the nearest real taxiway junction instead whenever runway travel
        # is forbidden and there was no forced rollout (which already
        # guarantees a taxiway-connected anchor — see _rollout()).
        if avoid_runway and not rolled_out:
            s_node = self._nearest_taxiway_node(sx, sz)
        else:
            s_node = self._nearest_node(sx, sz)
        leadin_node = self._gate_leadin_node.get(gate_id) if gate_id else None
        g_node = leadin_node if leadin_node is not None else self._nearest_node(gx, gz)
        # Once past the forced rollout, the anchor is a real taxiway exit, so
        # it's safe (and correct — planes taxi, they don't cruise the runway)
        # to forbid the route from using runway edges any further. Same forced
        # avoidance applies whenever the caller explicitly asks for it.
        node_path = self._dijkstra(s_node, g_node, forbid_runway=forbid_runway,
                                   avoid_edges=avoid_edges)
        if node_path is None:
            return (None, None) if return_nodes else None

        for nid in node_path:
            nx, nz = self._node_xz[nid]
            lon, lat = _world_to_lonlat(nx, nz)
            out.append((lat, lon))

        # A verified real lead-in already ends right on the gate — no further
        # synthetic hop needed. Everything else (no mapped lead-in for this
        # gate, or a non-gate destination like a departure holding point)
        # keeps the original behaviour: one final hop onto the exact
        # `gate_lonlat` coordinate.
        if leadin_node is None:
            out.append((gate_lonlat[1], gate_lonlat[0]))
        return (out, node_path) if return_nodes else out

    def _project_arc(self, pts, cum, wx, wz):
        """Nearest point on a runway polyline to (wx,wz), returned as
        (dist_m, seg_i, arclen_m, (px,pz)) or None. Shared by the fixed-exit roll."""
        b = None
        for i in range(len(pts) - 1):
            x1, z1 = pts[i]; x2, z2 = pts[i + 1]
            dx, dz = x2 - x1, z2 - z1; sl2 = dx * dx + dz * dz
            if sl2 == 0:
                continue
            t = max(0.0, min(1.0, ((wx - x1) * dx + (wz - z1) * dz) / sl2))
            px, pz = x1 + t * dx, z1 + t * dz
            d = math.hypot(wx - px, wz - pz) * 10.0
            if b is None or d < b[0]:
                b = (d, i, cum[i] + t * math.hypot(dx, dz) * 10.0, (px, pz))
        return b

    def _roll_to_exit(self, touchdown_lonlat, heading_lonlat, exit_lonlat):
        """Landing-roll centerline points from touchdown to the point on the runway
        centerline ABEAM a fixed exit taxiway — used when every arrival must vacate at
        one specific exit (see arrival_route(fixed_exit_lonlat=...)). Returns
        [(lat,lon)...] exclusive of touchdown, inclusive of the abeam point (the
        caller then adds the short turn-off onto the exit taxiway). None if the
        touchdown isn't near a runway. `heading_lonlat` is unused here (the exit's own
        position fixes the travel direction) but kept for signature symmetry."""
        tx, tz = _lonlat_to_world(*touchdown_lonlat)
        ex, ez = _lonlat_to_world(*exit_lonlat)
        best = None
        for pts in self._runways:
            cum = [0.0]
            for i in range(1, len(pts)):
                cum.append(cum[-1] + math.hypot(pts[i][0] - pts[i - 1][0],
                                                pts[i][1] - pts[i - 1][1]) * 10.0)
            b = self._project_arc(pts, cum, tx, tz)
            if b and (best is None or b[0] < best[0]):
                best = (b[0], pts, cum, b)
        if best is None or best[0] > _ROLLOUT_SNAP_M:
            return None
        _, pts, cum, tb = best
        eb = self._project_arc(pts, cum, ex, ez)
        if eb is None:
            return None
        td_seg, td_arc = tb[1], tb[2]
        ex_seg, ex_arc, ex_pt = eb[1], eb[2], eb[3]
        sign = 1.0 if ex_arc > td_arc else -1.0
        rng = range(td_seg + 1, ex_seg + 1) if sign > 0 else range(td_seg, ex_seg, -1)
        out = [(_world_to_lonlat(*pts[i])[1], _world_to_lonlat(*pts[i])[0]) for i in rng]
        alon, alat = _world_to_lonlat(*ex_pt)
        out.append((alat, alon))
        return out

    def arrival_route(self, touchdown_lonlat, heading_lonlat, gate_lonlat,
                      one_exit_further: bool = False, gate_id: str | None = None,
                      avoid_edges: set[tuple[int, int]] | None = None,
                      fixed_exit_lonlat: tuple[float, float] | None = None,
                      via_lonlat: tuple[float, float] | None = None):
        """Full ARRIVAL path: a real landing-roll down the runway centerline to an
        exit, then taxiway pavement to the gate.

        Unlike route(start, gate) with hand-placed straight exit/divert hops (which
        cut across open ground between the runway and the taxiway network), this
        stays ON drawn pavement the whole way after touchdown.

        Two exit modes:
          * `fixed_exit_lonlat` given → EVERY arrival vacates at that one exit taxiway
            ("one exit for all"): roll the centerline to abeam the exit, a short
            turn-off onto it, then taxi to the gate. Deterministic, so with the
            single-arrival runway lock, landings space themselves one-after-another.
          * else → `_rollout` picks a real (~90°) exit by distance (`one_exit_further`
            rolls a heavy one exit further).

        `heading_lonlat` tells the roll which way down the runway the aircraft goes.
        Returns

            (coords[(lat, lon)], node_path, rollout_index)

        where `coords[rollout_index]` is the exit (end of the runway roll, start of
        the taxi) so the caller derives the roll distance in its own path-cumulative
        units. Returns (None, None, 0) if the touchdown isn't near a runway or the
        taxi leg can't be routed (caller keeps the flight SCHEDULED and retries)."""
        self.load()
        if fixed_exit_lonlat is not None:
            roll_pts = self._roll_to_exit(touchdown_lonlat, heading_lonlat, fixed_exit_lonlat)
            if roll_pts is None:
                return None, None, 0
            # Taxi legs on pavement (runway HARD-forbidden). If a `via` waypoint is
            # given, force exit A -> via B -> gate, each leg routed through the taxiway
            # graph (NOT a straight line) — this pins arrivals onto the intended taxi
            # path so they never re-cross the runway. Without a via: A -> gate direct.
            waypoints = ([via_lonlat] if via_lonlat is not None else []) + [gate_lonlat]
            legs, nodes_all = [], []
            prev = fixed_exit_lonlat
            for wp in waypoints:
                gid = gate_id if wp is gate_lonlat else None  # gate lead-in only on the final leg
                leg, leg_nodes = self.route(prev, wp, avoid_runway=True, gate_id=gid,
                                            avoid_edges=avoid_edges, return_nodes=True)
                if leg is None:
                    return None, None, 0
                legs.append(leg)
                nodes_all += (leg_nodes or [])
                prev = wp
            roll_full = [(touchdown_lonlat[1], touchdown_lonlat[0])] + roll_pts
            coords = list(roll_full)
            for leg in legs:
                for p in leg:  # de-dupe seams (exit / via appear at both leg ends)
                    if abs(coords[-1][0] - p[0]) > 1e-9 or abs(coords[-1][1] - p[1]) > 1e-9:
                        coords.append(p)
            # rollout ends where the aircraft leaves the runway onto the exit taxiway.
            rollout_index = len(roll_full)
            return coords, nodes_all, rollout_index
        rolled = self._rollout(touchdown_lonlat, heading_lonlat,
                               one_exit_further=one_exit_further)
        if rolled is None:
            return None, None, 0
        anchor_lonlat, rollout_pts = rolled
        # touchdown + the centerline roll to the exit anchor, all (lat, lon).
        roll_full = [(touchdown_lonlat[1], touchdown_lonlat[0])] + rollout_pts
        # Taxi from the exit anchor (a real taxiway node) to the gate, on pavement
        # (runway edges forbidden). avoid_edges steers around opposing traffic.
        taxi_coords, taxi_nodes = self.route(
            anchor_lonlat, gate_lonlat, avoid_runway=True, gate_id=gate_id,
            avoid_edges=avoid_edges, return_nodes=True)
        if taxi_coords is None:
            return None, None, 0
        # taxi_coords[0] is the anchor again — stitch on the rest, dropping any point
        # that coincides with the previous one so there's no zero-length seam.
        coords = list(roll_full)
        for p in taxi_coords[1:]:
            if abs(coords[-1][0] - p[0]) > 1e-9 or abs(coords[-1][1] - p[1]) > 1e-9:
                coords.append(p)
        rollout_index = len(roll_full) - 1  # anchor = last centerline point
        return coords, taxi_nodes, rollout_index


_graph = TaxiwayGraph()


def route(start_lonlat: tuple[float, float], gate_lonlat: tuple[float, float],
          heading_lonlat: tuple[float, float] | None = None,
          one_exit_further: bool = False,
          avoid_runway: bool = False,
          gate_id: str | None = None,
          avoid_edges: set[tuple[int, int]] | None = None,
          return_nodes: bool = False):
    return _graph.route(start_lonlat, gate_lonlat, heading_lonlat, one_exit_further,
                        avoid_runway, gate_id, avoid_edges, return_nodes)


def arrival_route(touchdown_lonlat: tuple[float, float], heading_lonlat: tuple[float, float],
                  gate_lonlat: tuple[float, float], one_exit_further: bool = False,
                  gate_id: str | None = None,
                  avoid_edges: set[tuple[int, int]] | None = None,
                  fixed_exit_lonlat: tuple[float, float] | None = None,
                  via_lonlat: tuple[float, float] | None = None):
    return _graph.arrival_route(touchdown_lonlat, heading_lonlat, gate_lonlat,
                                one_exit_further, gate_id, avoid_edges, fixed_exit_lonlat,
                                via_lonlat)


# Real runway 34R threshold + far end (lon, lat), given directly — more
# precise than the geometric heuristic this used to derive from hiaWays.json
# (which carries no runway name/ref metadata at all). ~4.3km apart, matching
# a real long-haul-capable runway length. This is the SINGLE surveyed runway
# entry point (takeoff-roll start) shared by BOTH hold-queues below — user-
# supplied, corrected 2026-07-28 after departures were entering at the wrong spot.
_RUNWAY_34R_START = (51.626013, 25.257217)  # threshold — where the takeoff roll begins (shared by both queues)
_RUNWAY_34R_END = (51.610208, 25.293119)    # far end — climb-out direction

# Hold-queue A start point. A departure's gate-to-runway taxi-out is routed here
# (via real taxiway pavement, runway edges forbidden — see route()'s
# `avoid_runway`), it holds here waiting for the runway lock, then makes the
# straight final hop onto the shared threshold. This is BOTH the routing target
# (pre-runway) and the actual hold-short point for queue A — they're the same
# physical spot, so there's no separate intermediate hop.
_PRE_RUNWAY_HOLD = (51.624393, 25.256294)
_HOLDING_SHORT = (51.624393, 25.256294)

# ── West runway (34L) — the parallel runway arrivals land on ────────────────────
# HIA has two parallel 16/34 runways. 34R (east) is the primary departure runway
# (constants above); 34L (west) is where arrivals touch down (see
# seed_runner._ARRIVAL_START_LONLAT = its 34L threshold). To relieve 34R
# congestion, a departure can be assigned 34L when there's a long-enough arrival
# gap (see departures.py). These 34L points are derived by a rigid parallel
# offset from the authoritative 34R points — same runway heading, same ~4.3 km
# length — anchored on the real, surveyed 34L threshold (the arrival touchdown
# point). Offset = (34L_threshold − 34R_threshold), applied to every 34R point:
#   d_lon = 51.605118 − 51.626015 = −0.020897 ; d_lat = 25.256100 − 25.257208 = −0.001108
_RUNWAY_34L_START = (51.605118, 25.256100)   # 34L threshold == arrival touchdown point (authoritative)
_RUNWAY_34L_END = (51.589311, 25.292011)     # far end: 34R_END + offset (parallel, same length/heading)
_PRE_RUNWAY_HOLD_34L = (51.604025, 25.254945)  # 34R pre-runway hold + offset
_HOLDING_SHORT_34L = (51.604730, 25.255374)    # 34R holding-short + offset

# ── Second hold-queue (queue B) — SAME runway, SAME entry, different queue ──────
# To stop departures bunching in a single hold-short line, we split them across
# TWO parallel hold-queues that both feed the ONE shared runway entry
# (_RUNWAY_34R_START above). Departures round-robin between queue A and queue B;
# two shorter lines instead of one pile-up. Both queues share the ONE 34R runway
# lock, so still only one aircraft rolls at a time. Queue B is a user-supplied
# hold-queue start point (corrected 2026-07-28); its threshold + far end are the
# SAME shared runway points as queue A — only the hold/routing point differs.
_PRE_RUNWAY_HOLD_34R_B = (51.624491, 25.254767)
_HOLDING_SHORT_34R_B = (51.624491, 25.254767)
_RUNWAY_34R_B_START = _RUNWAY_34R_START   # both queues enter at the one shared threshold

# Runway identifiers used across the departure code. RUNWAY_34R_B is the SAME
# physical runway as 34R (shares its lock) — just a different hold-queue.
RUNWAY_34R = "34R"
RUNWAY_34R_B = "34R-B"
RUNWAY_34L = "34L"

_RUNWAY_POINTS = {
    RUNWAY_34R:   (_PRE_RUNWAY_HOLD, _HOLDING_SHORT, _RUNWAY_34R_START, _RUNWAY_34R_END),
    RUNWAY_34R_B: (_PRE_RUNWAY_HOLD_34R_B, _HOLDING_SHORT_34R_B, _RUNWAY_34R_B_START, _RUNWAY_34R_END),
    RUNWAY_34L:   (_PRE_RUNWAY_HOLD_34L, _HOLDING_SHORT_34L, _RUNWAY_34L_START, _RUNWAY_34L_END),
}


def departure_pre_runway_lonlat(runway: str = RUNWAY_34R) -> tuple[float, float]:
    """Taxiway holding point just short of the runway threshold — a departure's
    gate-to-runway taxi-out is routed here (forbidding runway edges), then a
    short straight segment covers the hop to the holding-short point. Defaults to
    34R; pass RUNWAY_34L for the west (arrival) runway."""
    return _RUNWAY_POINTS[runway][0]


def departure_holding_short_lonlat(runway: str = RUNWAY_34R) -> tuple[float, float]:
    """Fixed hold-short point a departure waits at (runway.py's lock) before
    crossing onto the threshold itself."""
    return _RUNWAY_POINTS[runway][1]


def departure_threshold_lonlat(runway: str = RUNWAY_34R) -> tuple[float, float]:
    """The runway's threshold — where a departure's taxi-out ends and its
    takeoff roll (see departure_runway_end_lonlat()) begins."""
    return _RUNWAY_POINTS[runway][2]


def departure_runway_end_lonlat(runway: str = RUNWAY_34R) -> tuple[float, float]:
    """The far end of the runway, in the takeoff/climb-out direction. A
    departure's path runs gate -> taxiway -> threshold -> here (a final straight
    segment simulating the takeoff roll) before disappearing."""
    return _RUNWAY_POINTS[runway][3]


def departure_hold_to_threshold_route(runway: str = RUNWAY_34R):
    """Route the hold-short -> runway-threshold leg along REAL pavement, returned
    as [(lat, lon), ...] (excluding the hold-short start, which the caller already
    has). Unlike the gate-to-hold leg, this one MUST be allowed onto runway edges
    (it legitimately joins the runway), so it is routed with avoid_runway=False.
    Returns None if unroutable — caller should fall back to a straight hop."""
    hold = departure_holding_short_lonlat(runway)
    thr = departure_threshold_lonlat(runway)
    path = route(hold, thr, avoid_runway=False)
    if not path or len(path) < 2:
        return None
    return path[1:]  # drop the hold-short start; caller already placed it

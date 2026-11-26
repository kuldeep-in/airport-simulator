"""Live outbound flight schedule + gate-to-runway taxi-out simulation.

The roster (callsign, destination, aircraft type, real gate, and an ABSOLUTE
scheduled departure time) comes from live AeroDataBox data via
server/simulator/live_schedule.py — refreshed hourly. There's no manual
"Schedule Departures" click any more: each flight sits SCHEDULED until
sim_time (real UTC clock) reaches its real departure time, then activates.

Gate: departures use the REAL gate the API reports (departure.gate), claimed
in the shared live registry (gates.claim_gate). If that specific gate is
already occupied, or the API didn't give one, we fall back to a random
capacity-matched gate (gates.reserve_gate) — the taxi-out route is then
computed from whichever gate we got. Route compute happens lazily, at
activation, not for the whole window up front.

Three-stage lifecycle per departure, mirroring the "only what's actually
happening now is live" principle already used for arrivals:
  1. SCHEDULED — metadata only (callsign, destination, gate, scheduled_dep).
     Not yet in `live_positions`, so it doesn't render.
  2. GATE_PARKED — from _GATE_PARK_LEAD_SEC before departure, we claim its gate
     + compute its route, and it starts ticking into `live_positions`
     (visible, parked at its stand), waiting for its real departure time.
  3. ACTIVE — the moment sim_time reaches scheduled_dep it pushes back and
     starts taxiing immediately (no separate boarding hold): progress advances
     from 0 along its route to a fixed holding-short point over the real
     taxiway graph, waits there if another flight is on the runway (runway.py's
     single-occupant lock — HOLDING_SHORT), then crosses the threshold and
     rolls to the far end. On reaching it, one final `phase: INACTIVE` payload
     is broadcast (the frontend deletes on that phase) and it's dropped.
"""

import json
import math
import os
import random
from datetime import datetime, timedelta

from server.simulator import gates
from server.simulator import taxiway_graph
from server.simulator import runway
from server.simulator import flight_store
from server.simulator import traffic
from server.simulator import positions
from server.simulator import route_reservations
from server.simulator import turnarounds
from server.simulator import sim_events

_GATE_LONLAT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gateLonLat.json")
with open(_GATE_LONLAT_PATH) as _f:
    _GATE_LONLAT: dict[str, list[float]] = json.load(_f)

# How long before scheduled departure a flight appears parked at its gate. Each
# flight gets its OWN value, drawn once (in _add_departure) uniformly from this
# range, so aircraft don't all pop onto their stands at an identical 45-min mark.
# Pushback still happens at the real scheduled time regardless — this only varies
# when the plane becomes visible at the gate.
_GATE_PARK_LEAD_MIN_SEC = 30 * 60.0
_GATE_PARK_LEAD_MAX_SEC = 45 * 60.0
_TAXI_KT = 18.0        # steady taxi-out speed, matches arrivals' established taxi speed
_GATE_CREEP_KT = 5.0   # leaving the stand (pushback)
_CREEP_M = 60.0        # distance over which speed ramps from creep -> steady taxi
_TAKEOFF_KT = 180.0    # takeoff-roll speed reached at the runway's far end
KT_TO_MPS = 0.514444
_HOLD_QUEUE_GAP_M = 60.0  # nose-to-tail spacing between departures queued behind the hold-short point (metres); matches traffic.SEP_M so queued planes don't overlap
# Runway roll distance from the threshold to the ROTATION/liftoff point. The
# single-occupant runway lock is freed here (aircraft is airborne) rather than at
# the full ~4297 m far end — a real tower clears the next departure to roll as soon
# as the previous one rotates. This roughly halves runway-lock occupancy time
# (~79s → ~40s per departure), lifting one-runway throughput ~45→~60/hr. The plane
# still keeps rolling/climbing to the far end visually; only the LOCK releases early.
_LIFTOFF_ROLL_M = 1800.0

# ── Dual-runway overflow policy ────────────────────────────────────────────────
# 34R (east) is the primary departure runway; 34L (west) is arrivals' landing
# runway. To relieve 34R congestion, a departure is diverted to 34L ONLY when
# BOTH hold at activation time:
#   1. 34R is congested — at least _OVERFLOW_QUEUE_TRIGGER other departures are
#      already assigned 34R and still taxiing/holding (not yet on the runway).
#   2. There's a long-enough arrival gap — the next arrival's touchdown is at
#      least _OVERFLOW_ARRIVAL_GAP_SEC away (arrivals-first: they land on 34L, so
#      we don't borrow it if one is due soon).
# A 34L departure uses runway.west_dep_* (the arrival lock) so it can never
# conflict with a landing. If the 34L taxi route can't be built, we fall back to
# 34R — worst case is today's single-runway behaviour, never a crash.
_OVERFLOW_QUEUE_TRIGGER = 3        # 34R departures taxiing/holding before overflow kicks in
_OVERFLOW_ARRIVAL_GAP_SEC = 15 * 60.0  # required clear arrival window on 34L (15 min)


# ── In-memory roster (live, refreshed hourly) + live-tick state ─────────────────
_roster: dict[str, dict] = {}          # flight_id -> static metadata
_scheduled_dep: dict[str, datetime] = {}  # flight_id -> absolute real departure time (from AeroDataBox)
_path: dict[str, list] = {}            # flight_id -> [(lat, lon), ...] taxi-out + runway-roll track (built at activation)
_route_nodes: dict[str, list[int]] = {}  # flight_id -> taxi-out Dijkstra node path, reserved at pushback (route_reservations)
_cum_dist: dict[str, list] = {}
_total_dist: dict[str, float] = {}
_taxi_end_dist: dict[str, float] = {}  # cumulative metres where the taxiway portion ends and the runway segment begins
_hold_dist: dict[str, float] = {}       # cumulative metres at the holding-short point (see runway.py)
_runway_entered: dict[str, bool] = {}   # flight_id -> True once granted the runway lock for this run
_runway_released: dict[str, bool] = {}  # flight_id -> True once the lock was freed at rotation (one-shot; the plane keeps rolling to the far end after)
_gate_park_lead: dict[str, float] = {}  # flight_id -> this flight's own gate-appearance lead time (sec before scheduled_dep), drawn once in _add_departure
_state: dict[str, str] = {}            # 'SCHEDULED' | 'GATE_PARKED' | 'ACTIVE' | 'DONE' | 'DEPARTED'
_progress: dict[str, float] = {}       # metres travelled along _path
_runway: dict[str, str] = {}           # flight_id -> assigned takeoff runway ('34R' primary, '34L' overflow); chosen once at _activate_route
_last_heading: dict[str, float] = {}   # flight_id -> last heading (deg) while ACTUALLY moving; frozen when stopped (e.g. HOLDING_SHORT) so a stationary plane keeps its taxi heading instead of a jittery per-tick segment bearing
_api_gate: dict[str, str | None] = {}  # flight_id -> the real HIA gate the API reported (or None)
_events: dict[str, dict[str, datetime]] = {}  # flight_id -> ACTUAL stage times observed this run (pushback/taxi/queue/hold/runway/airborne) — see timelines()
_gate_at: dict[str, datetime] = {}  # flight_id -> when it appeared parked at its stand (GATE_PARKED) — the waterfall's AT GATE start


def _haversine_m(p0, p1) -> float:
    lat0, lat1 = math.radians(p0[0]), math.radians(p1[0])
    dlat = lat1 - lat0
    dlon = math.radians(p1[1] - p0[1])
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat0) * math.cos(lat1) * math.sin(dlon / 2) ** 2)
    return 6_371_000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing(p0, p1) -> float:
    lat0, lat1 = math.radians(p0[0]), math.radians(p1[0])
    dlon = math.radians(p1[1] - p0[1])
    y = math.sin(dlon) * math.cos(lat1)
    x = math.cos(lat0) * math.sin(lat1) - math.sin(lat0) * math.cos(lat1) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _target_speed_kt(fid: str, progress_m: float) -> float:
    taxi_end_m = _taxi_end_dist.get(fid, 0.0)
    if progress_m >= taxi_end_m:
        # On the runway itself (takeoff roll): accelerate from steady taxi
        # speed up to takeoff speed over the remaining distance to the
        # runway's far end — not the flat taxi speed used the rest of the way.
        total_m = _total_dist.get(fid, taxi_end_m)
        runway_len = max(total_m - taxi_end_m, 1.0)
        t = min(1.0, (progress_m - taxi_end_m) / runway_len)
        return _TAXI_KT + t * (_TAKEOFF_KT - _TAXI_KT)
    if progress_m >= _CREEP_M:
        return _TAXI_KT
    t = progress_m / _CREEP_M
    return _GATE_CREEP_KT + t * (_TAXI_KT - _GATE_CREEP_KT)


def _phase_for(fid: str, progress_m: float) -> str:
    """TAXIING_OUT (from the gate up to the holding-short point) -> HOLDING_SHORT
    (waiting at the hold point for runway.py's lock, not yet granted) -> RUNWAY
    (the final straight segment simulating the takeoff roll, threshold to runway
    end — see taxiway_graph.departure_runway_end_lonlat()). Kept as simple,
    named states rather than modeling real accel/rotation/climb physics."""
    taxi_end_m = _taxi_end_dist.get(fid, 0.0)
    hold_m = _hold_dist.get(fid, 0.0)
    if progress_m >= taxi_end_m:
        return "RUNWAY"
    if progress_m >= hold_m and not _runway_entered.get(fid, False):
        return "HOLDING_SHORT"
    return "TAXIING_OUT"


# Consider a departure "in the runway queue" once it's within this far (metres)
# of its hold-short point — far-out taxiing flights don't count toward ordering.
_QUEUE_APPROACH_M = 400.0


def _departures_ahead_in_hold_queue(fid: str, hold_m: float) -> int:
    """How many OTHER active, not-yet-cleared departures are closer to their
    hold-short point than `fid` (i.e. ahead of it in the runway queue). Used to
    stagger waiting planes nose-to-tail and pick the single front-of-line flight.
    Ordered by remaining distance to the hold point (ties broken by flight_id for
    determinism)."""
    my_remaining = hold_m - _progress.get(fid, 0.0)
    ahead = 0
    for other, st in _state.items():
        if other == fid or st != "ACTIVE" or _runway_entered.get(other, False):
            continue
        o_hold = _hold_dist.get(other, 0.0)
        o_remaining = o_hold - _progress.get(other, 0.0)
        if o_remaining > _QUEUE_APPROACH_M:
            continue  # still far out on the taxiway — not queued yet
        if o_remaining < my_remaining or (o_remaining == my_remaining and other < fid):
            ahead += 1
    return ahead


def _position_at(fid: str, progress: float):
    pts = _path[fid]
    cum = _cum_dist[fid]
    if len(pts) < 2:
        p = pts[0] if pts else (0.0, 0.0)
        return p[0], p[1], 0.0
    progress = max(0.0, min(progress, cum[-1]))
    for i in range(len(cum) - 1):
        if progress <= cum[i + 1] or i == len(cum) - 2:
            seg_len = cum[i + 1] - cum[i]
            t = (progress - cum[i]) / seg_len if seg_len > 0 else 0.0
            t = max(0.0, min(1.0, t))
            lat = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t
            lon = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t
            heading = _bearing(pts[i], pts[i + 1])
            return lat, lon, heading
    return pts[-1][0], pts[-1][1], 0.0


_BOARDING_HEADING_MIN_M = 20.0  # look this far down the taxi-out path before trusting its bearing


def _boarding_heading(fid: str) -> float:
    """Nose heading while parked at the gate (GATE_PARKED) — deliberately NOT
    `_bearing(pts[0], pts[1])` (what `_position_at(fid, 0.0)` would give):
    for a gate with a verified real lead-in (taxiway_graph.py's
    `_gate_leadin_node`), pts[1] can now be only a metre or two from pts[0]
    (the lead-in's own gate-side node basically coincides with the gate
    marker), so that bearing is computed from a near-zero-length vector and
    comes out essentially arbitrary — the diagonal, off-lane orientation seen
    in practice. Instead, walk forward until the path is genuinely away from
    the gate (`_BOARDING_HEADING_MIN_M`) and take the bearing back from there
    to the gate — the same "facing the direction it approaches/leaves the
    stand along" convention seed_runner.py's arrivals already freeze into
    PARKED heading, just computed from the outbound route instead of an
    inbound one."""
    pts = _path[fid]
    cum = _cum_dist[fid]
    if len(pts) < 2:
        return 0.0
    for i in range(1, len(pts)):
        if cum[i] >= _BOARDING_HEADING_MIN_M or i == len(pts) - 1:
            return _bearing(pts[i], pts[0])
    return _bearing(pts[1], pts[0])


def _cleanup_after_finish(fid: str) -> None:
    """Drop the (now-irrelevant) live-tick progress for a departed flight, but
    keep its entry in `_roster`/`_scheduled_dep` so sim_overlay() can still report
    it as DEPARTED (and hand the board its real gate) until the next slot refresh
    drops it from the window."""
    _progress.pop(fid, None)
    _last_heading.pop(fid, None)
    _events.pop(fid, None)
    _gate_at.pop(fid, None)


async def load_roster() -> None:
    """Load the live outbound roster (from live_schedule, sourced from
    AeroDataBox) at broadcaster startup. Each flight carries an absolute
    `scheduled_dep` (real departure time) and the real HIA gate the API
    reported; it stays SCHEDULED until sim_time reaches that time, when
    advance() activates it — claiming the gate + computing the taxi-out route
    then (no manual "Schedule Departures" click). Also clears stale
    live_positions for any of these flights."""
    _roster.clear()
    _scheduled_dep.clear()
    _api_gate.clear()
    _gate_park_lead.clear()
    _path.clear()
    _route_nodes.clear()
    route_reservations.clear_all()
    _cum_dist.clear()
    _total_dist.clear()
    _taxi_end_dist.clear()
    _hold_dist.clear()
    _runway_entered.clear()
    _runway_released.clear()
    _state.clear()
    _progress.clear()
    _last_heading.clear()
    _runway.clear()

    # Roster content is NOT loaded here any more — see seed_runner.load_gate_positions.
    # The generator pulls it from `flight_schedule` via broadcaster._pull_roster(), since
    # the fetcher is a separate scheduled job and live_schedule's globals are empty here.
    ids = list(_roster.keys())
    if ids:
        await positions.remove_many(ids)

    print(f"[departures] live outbound roster loaded: {len(_roster)} flights.")


def _add_departure(row: dict) -> None:
    """Register one parsed live-departure row as a SCHEDULED flight."""
    fid = row["callsign"]
    _roster[fid] = {
        "id":            fid,
        "callsign":      fid,
        "airline":       row.get("airline") or "QR",
        "aircraft_type": row["aircraft_type"],
        "origin":        "DOH",
        "destination":   row["destination"],
        "assigned_gate": None,  # claimed at gate-parking (from _api_gate, with fallback)
        # Gate the sim used on a PREVIOUS run (persisted). Reused at activation so a
        # reconstructed departure keeps its stand + taxi route after a restart/switch.
        "sim_gate":      row.get("sim_gate"),
        "api_status":    row.get("api_status"),  # real-world status from AeroDataBox
    }
    _scheduled_dep[fid] = row["scheduled_utc"]
    _api_gate[fid] = row.get("assigned_gate")
    _gate_park_lead[fid] = random.uniform(_GATE_PARK_LEAD_MIN_SEC, _GATE_PARK_LEAD_MAX_SEC)
    _state[fid] = "SCHEDULED"
    _progress[fid] = 0.0
    _path[fid] = []
    _cum_dist[fid] = [0.0]
    _total_dist[fid] = 0.0
    _hold_dist[fid] = 0.0
    _taxi_end_dist[fid] = 0.0
    _runway_entered[fid] = False
    _runway_released[fid] = False


async def refresh_roster(new_departures: list[dict]) -> None:
    """Merge a fresh departure window in (called from broadcaster._pull_roster, which
    reads flight_schedule every 60s):
    departure window in, keyed by callsign. Flights already parked or moving
    (GATE_PARKED/ACTIVE/DONE/DEPARTED) are left untouched; still-SCHEDULED flights
    have their departure time + metadata + API gate updated; new callsigns are
    added; SCHEDULED callsigns no longer in the window are dropped. Never
    disturbs a plane mid-taxi."""
    incoming = {r["callsign"]: r for r in new_departures}

    # Drop flights no longer in the window: still-SCHEDULED ones (never activated)
    # AND already-DEPARTED ones (their whole lifecycle finished — otherwise they'd
    # accumulate in the roster forever and keep showing on the schedule as stale
    # rows from hours ago). GATE_PARKED/ACTIVE/DONE are mid-flight — never dropped.
    dropped = [f for f, st in _state.items()
               if st in ("SCHEDULED", "DEPARTED") and f not in incoming]
    if dropped:
        await positions.remove_many(dropped)
        for fid in dropped:
            _forget_departure(fid)

    for fid, row in incoming.items():
        if fid not in _roster:
            _add_departure(row)  # new upcoming flight
        elif _state.get(fid) == "SCHEDULED":
            _scheduled_dep[fid] = row["scheduled_utc"]
            _api_gate[fid] = row.get("assigned_gate")
            _roster[fid]["aircraft_type"] = row["aircraft_type"]
            _roster[fid]["destination"] = row["destination"]
            _roster[fid]["airline"] = row.get("airline") or "QR"
            _roster[fid]["api_status"] = row.get("api_status")
        else:
            # Already GATE_PARKED/ACTIVE/DONE/DEPARTED — the flight's simulation
            # state (gate/route/schedule) must NOT be disturbed mid-flight, but
            # its real-world api_status should still track the feed (e.g.
            # Boarding → Departed) so the Schedule board doesn't stay frozen.
            _roster[fid]["api_status"] = row.get("api_status")


def _forget_departure(fid: str) -> None:
    """Remove all per-flight state for a flight (dropped from the window)."""
    for d in (_roster, _scheduled_dep, _api_gate, _gate_park_lead, _path, _route_nodes,
              _cum_dist, _total_dist, _taxi_end_dist, _hold_dist, _runway_entered,
              _runway_released, _state, _progress, _last_heading, _runway, _events, _gate_at):
        d.pop(fid, None)
    traffic.clear(fid)
    route_reservations.release(fid)


def _rwy_is_clear(fid: str) -> bool:
    """Is fid's assigned runway lock free? 34L departures share the arrival lock."""
    if _runway.get(fid) == taxiway_graph.RUNWAY_34L:
        return runway.west_dep_is_clear()
    return runway.is_clear()


def _rwy_enter(fid: str) -> None:
    if _runway.get(fid) == taxiway_graph.RUNWAY_34L:
        runway.west_dep_enter(fid)
    else:
        runway.enter(fid)


def _rwy_release(fid: str) -> None:
    """Release whichever runway lock fid holds. Releases BOTH defensively (each
    is a no-op unless fid actually holds it) so a runway reassignment mid-flight
    can never leak a lock."""
    runway.release(fid)
    runway.west_dep_release(fid)


_rr_counter = 0  # round-robin toggle across the two 34R entry points


def _choose_runway(fid: str, now: datetime) -> str:
    """All departures use the 34R runway and enter at its single shared threshold,
    but ROUND-ROBIN between two hold-QUEUES (RUNWAY_34R = queue A, RUNWAY_34R_B =
    queue B — same runway, same entry point, different hold-short start) so they
    form two shorter queues instead of one pile-up. Both queues share the single
    34R runway lock (see _rwy_is_clear/_rwy_enter — only 34L uses a different
    lock), so still only one aircraft rolls at a time.

    The dual-runway "overflow to 34L" policy remains removed; the 34L geometry +
    west_dep_* lock stay dormant so it can be re-enabled later if wanted."""
    global _rr_counter
    _rr_counter += 1
    return taxiway_graph.RUNWAY_34R if _rr_counter % 2 else taxiway_graph.RUNWAY_34R_B


def _build_route_for_runway(fid: str, rwy: str) -> None:
    """Build fid's taxi-out → holding-short → threshold → runway-roll path for
    the given takeoff runway, from its already-assigned gate. Falls back to 34R
    if the chosen runway's hold point is unroutable. Sets _runway[fid]/meta and
    all the per-flight path/distance state."""
    meta = _roster[fid]
    gate_id = meta.get("assigned_gate")
    gate_lonlat = tuple(_GATE_LONLAT[gate_id][:2])  # (lon, lat) — entry is [lon, lat, heading]

    # Taxiway-only route from the gate to the pre-runway holding point — runway
    # edges forbidden, so this leg can't cut across runway pavement. `avoid` steers
    # around taxiway segments an arrival is currently taxiing the opposite way
    # (head-on) where a clear alternative exists — see route_reservations.
    avoid = route_reservations.blocked_for(fid)
    route, route_nodes = taxiway_graph.route(
        gate_lonlat, taxiway_graph.departure_pre_runway_lonlat(rwy),
        avoid_runway=True, avoid_edges=avoid, return_nodes=True)
    if route is None and rwy != taxiway_graph.RUNWAY_34R:
        # Couldn't taxi to the overflow (34L) hold point — fall back to the
        # primary runway (34R) rather than risk a degenerate straight-line path.
        rwy = taxiway_graph.RUNWAY_34R
        route, route_nodes = taxiway_graph.route(
            gate_lonlat, taxiway_graph.departure_pre_runway_lonlat(rwy),
            avoid_runway=True, avoid_edges=avoid, return_nodes=True)

    # Node path of the taxi-out leg, reserved at pushback (not here — a flight
    # sits GATE_PARKED up to ~45 min before it actually moves; reserving that
    # early would block those taxiways the whole time it's still parked).
    _route_nodes[fid] = route_nodes or []
    _runway[fid] = rwy
    meta["runway"] = rwy  # surfaced to the frontend/schedule for visibility

    pre_runway = taxiway_graph.departure_pre_runway_lonlat(rwy)
    holding_short = taxiway_graph.departure_holding_short_lonlat(rwy)
    holding_short_latlon = (holding_short[1], holding_short[0])
    threshold = taxiway_graph.departure_threshold_lonlat(rwy)
    threshold_latlon = (threshold[1], threshold[0])
    runway_end = taxiway_graph.departure_runway_end_lonlat(rwy)
    runway_end_latlon = (runway_end[1], runway_end[0])

    taxi_path = route or [(gate_lonlat[1], gate_lonlat[0]), (pre_runway[1], pre_runway[0])]
    cum = [0.0]
    for j in range(1, len(taxi_path)):
        cum.append(cum[-1] + _haversine_m(taxi_path[j - 1], taxi_path[j]))

    cum.append(cum[-1] + _haversine_m(taxi_path[-1], holding_short_latlon))
    hold_m = cum[-1]  # HOLDING_SHORT phase begins exactly here (see _phase_for)

    # Hold-short -> threshold along REAL pavement (this leg legitimately joins the
    # runway, so it's routed WITH runway edges allowed). Falls back to a single
    # straight hop if unroutable. Ends exactly on the threshold, so taxi_end_m
    # (RUNWAY phase start) stays anchored there.
    hold_to_thr = taxiway_graph.departure_hold_to_threshold_route(rwy) or [threshold_latlon]
    prev = holding_short_latlon
    for pt in hold_to_thr:
        cum.append(cum[-1] + _haversine_m(prev, pt))
        prev = pt
    taxi_end_m = cum[-1]  # RUNWAY phase begins exactly here (see _phase_for)

    path = taxi_path + [holding_short_latlon] + hold_to_thr + [runway_end_latlon]
    cum.append(cum[-1] + _haversine_m(hold_to_thr[-1], runway_end_latlon))

    _path[fid] = path
    _cum_dist[fid] = cum
    _total_dist[fid] = cum[-1]
    _hold_dist[fid] = hold_m
    _taxi_end_dist[fid] = taxi_end_m
    _runway_entered[fid] = False
    _runway_released[fid] = False
    _progress[fid] = 0.0


def _activate_route(fid: str, now: datetime, takeover_gate: str | None = None) -> bool:
    """At GATE_PARK time (~45 min before departure): claim the flight's real API
    gate (fall back to a random capacity-matched gate if it's taken or missing),
    then build a provisional taxi-out route to the PRIMARY runway (34R) so the
    aircraft can sit visibly at its stand. The real runway choice (34R vs 34L
    overflow) is deferred to pushback — see advance()'s GATE_PARKED→ACTIVE branch
    — because congestion/arrival-gap must be judged at departure time, not 45 min
    early. Returns True if a gate was secured, False if none is free (caller
    keeps the flight SCHEDULED and retries)."""
    meta = _roster[fid]
    # Turnaround: the inbound aircraft's stand was just freed for us (see
    # turnarounds.py) — take that exact gate. Otherwise prefer the gate this flight
    # used before (persisted sim_gate) so a reconstructed departure keeps its stand +
    # route; then the real API gate; then any free gate.
    gate_id = gates.claim_gate(takeover_gate, fid, now) if takeover_gate else None
    if gate_id is None:
        gate_id = gates.claim_gate(meta.get("sim_gate"), fid, now)
    if gate_id is None:
        gate_id = gates.claim_gate(_api_gate.get(fid), fid, now)
    if gate_id is None:
        gate_id = gates.reserve_gate(gates.capacity_class_of(meta["aircraft_type"]), fid, now)
    meta["assigned_gate"] = gate_id
    if gate_id is None:
        return False  # every gate occupied — retry next tick

    # Persist the gate assignment (occupancy window = pushback − 45 min → pushback),
    # anchored to the real scheduled departure time. Flushed async next tick.
    # Record the gate the sim actually used onto the flight's flight_schedule row
    # (may differ from api_gate — see the api_gate vs sim_gate distinction). Its
    # occupancy window is derived from scheduled_utc − lead on read. Flushed next tick.
    sched = _scheduled_dep.get(fid, now)
    flight_store.enqueue_sim_gate(fid, "departure", sched, gate_id)

    _build_route_for_runway(fid, taxiway_graph.RUNWAY_34R)  # provisional; re-decided at pushback
    return True


def register_traffic() -> None:
    """PHASE 1 of the per-tick ground-traffic protocol (see traffic.py): register
    every taxiing departure's CURRENT position + occupied edge BEFORE any flight
    moves. A departure ON the runway (already cleared for takeoff) is skipped —
    the runway lock governs it and it shouldn't reserve taxiway edges. Called by
    the broadcaster right after seed_runner.register_traffic()."""
    for fid, st in _state.items():
        if st != "ACTIVE" or _runway_entered.get(fid, False):
            continue
        traffic.register_current(fid, _path.get(fid, []), _cum_dist.get(fid, [0.0]),
                                 _progress.get(fid, 0.0), "departure")


# A departure whose pushback is more than this many seconds in the past is
# RECONSTRUCTED (placed at its computed taxi-out position) rather than started from
# the gate, so a restart / airport switch doesn't teleport it back to its stand.
_RECONSTRUCT_AFTER_SEC = 120.0


def _progress_after_seconds(fid: str, elapsed: float, total: float) -> float:
    """Distance (m) along the taxi-out path after `elapsed` seconds of the departure
    speed profile from progress 0 — used to RECONSTRUCT a departure that pushed back
    in the past. Capped at `total`. (Ignores hold-queue waiting, so it's an upper
    estimate; advance()'s ACTIVE branch re-clamps to the queue.)"""
    if elapsed <= 0 or total <= 0:
        return 0.0
    step_m = 50.0
    p, t = 0.0, 0.0
    while p < total and t < elapsed:
        spd = max(_target_speed_kt(fid, p) * KT_TO_MPS, 0.5)
        dt_seg = step_m / spd
        if t + dt_seg > elapsed:
            p += spd * (elapsed - t)
            break
        p += step_m
        t += dt_seg
    return min(p, total)


def advance(sim_time: datetime, tick_sec: float, speed: int) -> None:
    dt = tick_sec * speed  # tick_sec is the REAL elapsed wall-clock seconds (float)
    # LEADER-FIRST within the departure stream: process flights nearest the runway
    # (least remaining distance to their hold-short) first, so a following flight
    # yields to the one ahead — the ordering traffic.step relies on to stay
    # deadlock-free. Non-ACTIVE states are order-independent (sort trails them).
    def _remaining(fid):
        return _hold_dist.get(fid, 0.0) - _progress.get(fid, 0.0)
    order = sorted(_state.keys(),
                   key=lambda f: _remaining(f) if _state.get(f) == "ACTIVE" else float("inf"))
    for fid in order:
        st = _state[fid]
        if st == "SCHEDULED":
            # Appear parked at the gate _GATE_PARK_LEAD_SEC before departure. At
            # that moment _activate_route claims the flight's REAL API gate if the
            # API gave one we model AND it's free; otherwise it reserves ANY free
            # capacity-matched modelled gate (gates.reserve_gate — a gate the live
            # registry shows unoccupied, so not one currently held by another
            # arrival or departure). This is the fix for the 4-hour refresh window:
            # the airport often assigns a departure's gate only shortly before
            # departure, so between refreshes the API gate is frequently missing —
            # rather than skip the flight, we give it a real free stand so it
            # taxis out normally. (Only a genuinely full airport — every gate
            # occupied — leaves it SCHEDULED, retrying next tick; never crashes.)
            lead = (_scheduled_dep[fid] - sim_time).total_seconds()
            if lead <= _gate_park_lead.get(fid, _GATE_PARK_LEAD_MAX_SEC):
                # Configured turnaround (data/turnarounds.json): wait for the inbound
                # aircraft to reach its stand, then take over THAT stand so one plane
                # is shown, not two.
                action, take_gate, arr_fid = turnarounds.departure_gate_action(fid, sim_time)
                if action == "wait":
                    continue
                if action == "takeover":
                    from server.simulator import seed_runner
                    seed_runner.hand_over_to_departure(arr_fid, fid, sim_time)
                if _activate_route(fid, sim_time, take_gate):
                    _state[fid] = "GATE_PARKED"
                    _gate_at[fid] = sim_time
                    print(f"[departures] {fid} AT GATE {_roster[fid].get('assigned_gate')} "
                          f"(pushback {_scheduled_dep[fid]:%H:%M}Z, in {max(0.0, lead) / 60:.0f} min)")
                    sim_events.emit("AT_GATE", fid, "departure", sim_time, _roster[fid],
                                    scheduled_utc=_scheduled_dep[fid].isoformat(),
                                    turnaround_from=arr_fid if action == "takeover" else None)
            continue
        if st == "GATE_PARKED":
            # Parked at the gate, visible, until its real departure time — then
            # push back and start taxiing immediately (no separate boarding
            # hold; the flight already sat at the stand during GATE_PARKED).
            if sim_time >= _scheduled_dep[fid]:
                # Every departure pushes back at its real scheduled time and waits
                # in the taxi/hold queue — no pushback metering. This is safe now
                # that the ground-traffic break-through (traffic.py) resolves the
                # head-on/crossing conflicts that used to gridlock a fully-loaded
                # queue: offline replay of the full feed drains all departures with
                # a peak of ~20 concurrently taxiing and no permanent stall.
                # Decide the takeoff runway NOW (at pushback), when 34R congestion
                # and the arrival gap are real — not 45 min ago at gate-park.
                rwy = _choose_runway(fid, sim_time)
                # Rebuild the taxi-out route AT PUSHBACK (superseding the provisional
                # gate-park route) so it avoids arrivals taxiing the opposite way
                # RIGHT NOW, then reserve its edges so later-planned routes avoid a
                # head-on into it.
                _build_route_for_runway(fid, rwy)
                # RECONSTRUCT (B2): if pushback is well in the past (a restart/switch
                # dropped this departure mid-taxi), place it at its computed taxi-out
                # position instead of back at the gate. If it would already be airborne,
                # retire it rather than re-taxi it.
                elapsed = (sim_time - _scheduled_dep[fid]).total_seconds()
                total = _total_dist.get(fid, 0.0)
                progress = (_progress_after_seconds(fid, elapsed, total)
                            if elapsed > _RECONSTRUCT_AFTER_SEC else 0.0)
                if total > 0 and progress >= total:
                    gates.release_gate(_roster[fid].get("assigned_gate"), sim_time, fid)
                    route_reservations.release(fid)
                    # DONE, not DEPARTED: tick() then deletes the position row it wrote
                    # while GATE_PARKED (straight to DEPARTED left a ghost aircraft on
                    # its — already re-bookable — stand until the stale-row sweep).
                    _state[fid] = "DONE"
                    print(f"[departures] {fid} retired — pushback {_scheduled_dep[fid]:%H:%M}Z "
                          f"was {elapsed / 60:.0f} min ago, already airborne")
                    continue
                route_reservations.reserve(fid, _route_nodes.get(fid))
                # The aircraft physically leaves the stand at pushback, so FREE the
                # gate in the live occupancy registry NOW — not at takeoff. Owner-guarded
                # so it only frees the stand this flight actually holds. meta[
                # "assigned_gate"] is kept as the DISPLAY value (board still shows the
                # departure gate) — only the registry occupancy is released.
                gates.release_gate(_roster[fid].get("assigned_gate"), sim_time, fid)
                _state[fid] = "ACTIVE"
                _progress[fid] = progress
                # Pushback happens AT the scheduled time in this model, so that's the
                # true pushback even for a reconstructed (mid-taxi) flight.
                _events[fid] = {"pushback": _scheduled_dep[fid]}
                print(f"[departures] {fid} PUSHBACK from {_roster[fid].get('assigned_gate')} → {rwy}, "
                      f"taxi {_hold_dist.get(fid, 0.0):.0f} m"
                      + (f" (resumed {elapsed / 60:.0f} min into taxi)" if progress > 0 else ""))
                if progress <= 0:   # a restart's mid-taxi reconstruction isn't a real pushback
                    sim_events.emit("PUSHBACK", fid, "departure", sim_time, _roster[fid], runway=rwy,
                                    taxi_m=round(_hold_dist.get(fid, 0.0)))
                if progress >= _CREEP_M:
                    _events[fid]["taxi"] = _scheduled_dep[fid] + timedelta(
                        seconds=_travel_sec(fid, 0.0, _CREEP_M))
            continue
        if st == "ACTIVE":
            total = _total_dist.get(fid, 0.0)
            hold_m = _hold_dist.get(fid, 0.0)
            spd_kt = _target_speed_kt(fid, _progress[fid])
            step_m = spd_kt * KT_TO_MPS * dt
            prev_progress = _progress[fid]
            prev_lat, prev_lon, _ = _position_at(fid, prev_progress)

            if _runway_entered.get(fid, False):
                _progress[fid] += step_m  # already cleared onto the runway — keep going
                # Register the footprint (no clamping — the runway lock governs the
                # roll) so a taxiing flight near the threshold still gives way.
                traffic.step(fid, _path[fid], _cum_dist[fid],
                             prev_progress, _progress[fid], "departure", enforce=False)
            else:
                # Queue behind the hold-short point instead of all stacking on it.
                # `ahead` = how many other waiting departures are physically CLOSER
                # to the hold point (smaller remaining distance). This flight's
                # stop line is pushed back one slot-gap per plane ahead, so they
                # line up nose-to-tail. Ordering by real distance (not join time)
                # means the front is always the closest plane — no deadlock from a
                # far-out flight "holding" the front of the line.
                ahead = _departures_ahead_in_hold_queue(fid, hold_m)
                stop_m = max(0.0, hold_m - ahead * _HOLD_QUEUE_GAP_M)

                # Clamp the intended step to the queue slot, then apply full
                # edge-reservation separation (head-on hold + following gap on the
                # shared taxiway). traffic.step never moves the flight backward and
                # yields only to higher-priority / ahead traffic, so it can't
                # deadlock the outbound line (arrivals are advanced first, so a
                # departure gives way to a crossing arrival).
                proposed = min(_progress[fid] + step_m, stop_m)
                _progress[fid] = traffic.step(fid, _path[fid], _cum_dist[fid],
                                              _progress[fid], proposed, "departure")

                if _progress[fid] >= stop_m:
                    _progress[fid] = stop_m   # clamp at this flight's queue slot
                    _events.setdefault(fid, {}).setdefault("queue", sim_time)
                    # Only the front of the queue (nobody ahead) may take the
                    # runway, and only when the assigned runway's lock is clear.
                    # 34L departures share the ARRIVAL lock (west_dep_*), so they
                    # can never conflict with a landing.
                    if ahead == 0 and _rwy_is_clear(fid):
                        _rwy_enter(fid)
                        _runway_entered[fid] = True
                        ev_rw = _events.setdefault(fid, {})
                        ev_rw.setdefault("runway", sim_time)
                        waited = (sim_time - ev_rw["queue"]).total_seconds() if "queue" in ev_rw else 0.0
                        print(f"[departures] {fid} CLEARED onto {_runway.get(fid)} "
                              f"(waited {waited:.0f}s in queue/hold)")
                        sim_events.emit("RUNWAY_ENTER", fid, "departure", sim_time, _roster[fid],
                                        runway=_runway.get(fid), queue_wait_s=round(waited))
                        # Onto the runway now — off the taxiways, so free its taxi
                        # reservation for other flights being routed.
                        route_reservations.release(fid)

            # Heading bookkeeping below uses the committed position.
            clat, clon, seg_heading = _position_at(fid, _progress[fid])
            # Freeze the display heading only while genuinely moving forward. Derive
            # it from the ACTUAL travel direction this tick (prev position → new
            # position), NOT the segment bearing at the stop point — at a sharp
            # taxiway corner the clamp can land on the kink and _position_at returns
            # the incoming segment's bearing, which points backwards (the
            # off-heading plane seen at the bend). A stopped plane keeps its last
            # moving heading; only fall back to the segment bearing before any motion.
            if _progress[fid] > prev_progress + 1e-6:
                moved = _haversine_m((prev_lat, prev_lon), (clat, clon))
                if moved > 0.5:  # enough travel for a reliable bearing
                    _last_heading[fid] = _bearing((prev_lat, prev_lon), (clat, clon))
                elif fid not in _last_heading:
                    _last_heading[fid] = seg_heading
            elif fid not in _last_heading:
                _last_heading[fid] = seg_heading  # first tick, before any motion

            # Stage events for the departure-queue waterfall (first time only).
            ev = _events.setdefault(fid, {})
            if _progress[fid] >= _CREEP_M:
                ev.setdefault("taxi", sim_time)
            if _progress[fid] >= hold_m and "hold" not in ev:
                ev["hold"] = sim_time
                sim_events.emit("HOLD_SHORT", fid, "departure", sim_time, _roster[fid],
                                runway=_runway.get(fid))

            # Free the runway lock at ROTATION (threshold + _LIFTOFF_ROLL_M), one
            # time, so the next queued departure can start its roll while this one
            # climbs out — the actual throughput fix. The plane stays ACTIVE and
            # keeps rolling to the far end below.
            if (_runway_entered.get(fid, False)
                    and not _runway_released.get(fid, False)
                    and _progress[fid] >= _taxi_end_dist.get(fid, 0.0) + _LIFTOFF_ROLL_M):
                _rwy_release(fid)
                _runway_released[fid] = True
                ev_air = _events.setdefault(fid, {})
                ev_air.setdefault("airborne", sim_time)
                push_at = ev_air.get("pushback")
                print(f"[departures] {fid} AIRBORNE from {_runway.get(fid)}"
                      + (f" ({(sim_time - push_at).total_seconds() / 60:.1f} min after pushback)" if push_at else ""))
                sim_events.emit("TAKEOFF", fid, "departure", sim_time, _roster[fid],
                                runway=_runway.get(fid),
                                since_pushback_s=round((sim_time - push_at).total_seconds()) if push_at else None)

            if _progress[fid] >= total:
                _rwy_release(fid)   # defensive no-op if already released at rotation
                traffic.clear(fid)  # off the ground / done taxiing
                route_reservations.release(fid)  # defensive — freed at runway entry
                _state[fid] = "DONE"


def _ramp_sec(a: float, b: float, va: float, vb: float, x0: float, x1: float) -> float:
    """Seconds to travel x0→x1 (metres) inside [a, b], where speed (m/s) rises
    linearly WITH DISTANCE from va at a to vb at b — the shape of every leg of
    the departure speed profile. ∫dx / (va + k(x-a)) = ln(v1/v0) / k."""
    x0, x1 = max(x0, a), min(x1, b)
    if x1 <= x0:
        return 0.0
    if abs(vb - va) < 1e-9:
        return (x1 - x0) / va
    k = (vb - va) / (b - a)
    return math.log((va + k * (x1 - a)) / (va + k * (x0 - a))) / k


def _travel_sec(fid: str, p0: float, p1: float) -> float:
    """Unobstructed travel time (s) from progress p0 to p1 along fid's path, using
    exactly _target_speed_kt's profile: creep ramp to _CREEP_M, steady taxi to the
    threshold, then the takeoff-roll ramp. Ignores traffic yields, so it's the
    optimistic bound the queue chaining in timelines() then pushes back."""
    taxi_end = _taxi_end_dist.get(fid, 0.0)
    rwy_end = taxi_end + max(_total_dist.get(fid, taxi_end) - taxi_end, 1.0)
    creep_end = min(_CREEP_M, taxi_end)
    taxi, creep, takeoff = (_TAXI_KT * KT_TO_MPS, _GATE_CREEP_KT * KT_TO_MPS,
                            _TAKEOFF_KT * KT_TO_MPS)
    return (_ramp_sec(0.0, creep_end, creep, taxi, p0, p1)
            + _ramp_sec(creep_end, taxi_end, taxi, taxi, p0, p1)
            + _ramp_sec(taxi_end, rwy_end, taxi, takeoff, p0, p1))


_STAGES = ("gate", "pushback", "taxi", "queue", "hold", "runway", "airborne")


def timelines(now: datetime) -> dict[str, dict]:
    """Per-departure stage timeline for the Dashboard's departure-queue waterfall,
    published with each position row (sim_positions.timeline).

    Stage boundaries: gate (appeared parked at its stand) → pushback (leave stand) → taxi (past the _CREEP_M pushback
    creep) → queue (first stopped at its slot behind the hold-short line) → hold
    (reached the hold-short line itself) → runway (granted the runway lock) →
    airborne (rotation, lock released).

    Boundaries already passed are the ACTUAL times recorded in advance() (listed
    in `actual`). The rest are PROJECTED: unobstructed travel time over the real
    route (_travel_sec), then chained per runway lock in queue order — a flight
    can't reach the hold line before the one ahead has taken the runway, nor
    take the runway before the one ahead is airborne. Arrivals don't touch the
    34R lock, so they're not in the chain.

    { "t": {stage: ISO}, "actual": [stage, ...], "runway": "34R" }"""
    rows: dict[str, dict] = {}
    waiting: dict[str, list] = {}       # lock -> [(hold_free, fid)]
    lock_free: dict[str, datetime] = {}  # lock -> projected time it frees up
    last_rwy: dict[str, datetime] = {}   # lock -> latest runway-entry time so far
    for fid, st in _state.items():
        if st not in ("GATE_PARKED", "ACTIVE") or fid not in _total_dist:
            continue
        ev = _events.get(fid, {}) if st == "ACTIVE" else {}
        p = _progress.get(fid, 0.0) if st == "ACTIVE" else 0.0
        hold_m = _hold_dist.get(fid, 0.0)
        air_m = min(_taxi_end_dist.get(fid, 0.0) + _LIFTOFF_ROLL_M, _total_dist[fid])
        push = ev.get("pushback") or _scheduled_dep[fid]
        t0 = now if st == "ACTIVE" else max(now, push)
        at = lambda m: t0 + timedelta(seconds=_travel_sec(fid, p, m))  # noqa: E731
        # A gate time after pushback means it was re-parked by a restart mid-taxi —
        # its real stand time is unknown, so report it as such (not `actual`).
        gate_at = _gate_at.get(fid)
        gate_known = gate_at is not None and gate_at <= push
        t = {"gate": gate_at if gate_known else push, "pushback": push,
             "taxi": ev.get("taxi") or at(_CREEP_M)}
        lock = "W" if _runway.get(fid) == taxiway_graph.RUNWAY_34L else "E"
        if _runway_entered.get(fid, False):
            t["runway"] = ev.get("runway") or now
            t["hold"] = ev.get("hold") or t["runway"]
            t["queue"] = ev.get("queue") or t["hold"]
            t["airborne"] = ev.get("airborne") or at(air_m)
            if "airborne" not in ev:
                lock_free[lock] = max(lock_free.get(lock, now), t["airborne"])
            last_rwy[lock] = max(last_rwy.get(lock, t["runway"]), t["runway"])
        else:
            ahead = _departures_ahead_in_hold_queue(fid, hold_m) if st == "ACTIVE" else 0
            t["queue"] = ev.get("queue") or at(max(p, hold_m - ahead * _HOLD_QUEUE_GAP_M))
            hold_free = ev.get("hold") or at(hold_m)
            waiting.setdefault(lock, []).append((hold_free, fid))
            t["_hold_free"] = hold_free
        rows[fid] = {"t": t, "actual": [k for k in _STAGES if k in ev or (k == "gate" and gate_known)],
                     "runway": _runway.get(fid)}

    for lock, queue in waiting.items():
        free, prev = lock_free.get(lock), last_rwy.get(lock)
        for hold_free, fid in sorted(queue):
            t = rows[fid]["t"]
            del t["_hold_free"]
            hold = _events.get(fid, {}).get("hold") or max(hold_free, prev or hold_free)
            rwy = max(hold, free or hold, now)
            t["hold"], t["runway"] = hold, rwy
            t["queue"] = min(t["queue"], hold)
            t["airborne"] = rwy + timedelta(seconds=_travel_sec(
                fid, _hold_dist.get(fid, 0.0),
                min(_taxi_end_dist.get(fid, 0.0) + _LIFTOFF_ROLL_M, _total_dist[fid])))
            free, prev = t["airborne"], rwy

    for row in rows.values():
        row["t"] = {k: row["t"][k].replace(microsecond=0).isoformat() for k in _STAGES}
    return rows


async def _tick_one(fid: str, timeline: dict | None = None) -> dict:
    meta = _roster[fid]
    if _state[fid] == "GATE_PARKED":
        # Parked at the gate (progress 0 == the gate itself — route()'s first
        # point is always the exact start coordinate), visible, not moving,
        # waiting for its departure time. Heading is the gate's authoritative
        # nose-in heading (consistent for every plane at this stand), falling
        # back to the taxi-path-derived _boarding_heading() only if the gate
        # has no stored heading.
        lat, lon = _path[fid][0]
        heading = gates.gate_heading(meta.get("assigned_gate"))
        if heading is None:
            heading = _boarding_heading(fid)
        speed_kt = 0.0
        phase = "GATE_PARKED"
    else:
        lat, lon, seg_heading = _position_at(fid, _progress[fid])
        speed_kt = round(_target_speed_kt(fid, _progress[fid]), 1)
        phase = _phase_for(fid, _progress[fid])
        # Use the heading frozen in advance() while last actually moving; a
        # stopped plane (e.g. HOLDING_SHORT) keeps its taxi heading rather than a
        # jittery per-tick segment bearing. Fall back to the segment bearing only
        # before it's ever moved.
        heading = _last_heading.get(fid, seg_heading)

    # Published to `sim_positions` — the generator → app contract (positions.py).
    # Descriptive fields (scheduled_dep, runway, api_status) travel WITH the position:
    # the app holds no simulation state, so it can't ask us for them.
    return await positions.upsert(
        fid, meta["callsign"], meta["aircraft_type"], meta["airline"],
        meta["origin"], meta["destination"], "departure", meta["assigned_gate"],
        meta.get("aircraft_reg"), lat, lon, heading, phase, speed_kt,
        _scheduled_dep.get(fid), None, meta.get("runway"),
        meta.get("api_status"), timeline,
    )


async def _finish_one(fid: str, now: datetime) -> dict:
    meta = _roster[fid]
    gates.release_gate(meta.get("assigned_gate"), now, fid)
    _rwy_release(fid)  # already released in advance() at finish — defensive no-op otherwise
    await positions.remove(fid)
    return {"id": fid, "callsign": meta["callsign"], "phase": "INACTIVE", "source": "lakebase"}


async def tick(now: datetime) -> tuple[list[str], list[dict]]:
    """Write this tick's departure positions to Lakebase.

    Returns `(written_flight_ids, removal_payloads)` — same contract as
    seed_runner.tick(). Position payloads are built by the broadcaster from the rows
    written here (see broadcaster._tick), so the map renders the DATABASE's contents.

    This also removes an old inconsistency: the snapshot path used to build departures
    from memory while arrivals were read from the DB, so the two halves of one feature
    had different sources. Both now go through positions.py."""
    written, removed = [], []
    tls = timelines(now)
    for fid, st in list(_state.items()):
        if st in ("GATE_PARKED", "ACTIVE"):
            written.append(await _tick_one(fid, tls.get(fid)))
        elif st == "DONE":
            removed.append(await _finish_one(fid, now))
            _state[fid] = "DEPARTED"
            _cleanup_after_finish(fid)
    return written, removed


def payload_extras(fid: str) -> dict:
    """Departure fields that aren't position telemetry and so aren't in
    `live_positions`: the scheduled departure time, the real-world api_status, and the
    assigned runway. Merged onto the DB-sourced payload by the broadcaster.

    Returns {} for a flight this module doesn't own."""
    meta = _roster.get(fid)
    if meta is None:
        return {}
    out: dict = {"api_status": meta.get("api_status"), "runway": meta.get("runway")}
    sched = _scheduled_dep.get(fid)
    if sched:
        out["scheduled_dep"] = sched.isoformat()
    return out


def sim_overlay() -> dict[str, dict]:
    """Live SIM state per departure callsign, for the Schedule board to left-join
    onto its Lakebase query (see app.py's /api/schedule).

    The board's ROW SET now comes from the flight_schedule dataset, not this
    roster — so this supplies only what the dataset can't know: the simulated
    phase, the state-machine status, the gate the sim actually placed the aircraft
    at, and the assigned runway. A flight absent from here simply isn't being
    simulated (too far out, or already dropped from the roster); the board shows
    it with no phase.

    Replaces the old schedule_info(), which owned both the row set and the
    filtering. Its 15-minute past-window filter is gone — flight_store.board_window
    now applies the single `now - 10 min` rule for both directions."""
    out: dict[str, dict] = {}
    for fid, meta in _roster.items():
        st = _state.get(fid, "SCHEDULED")
        if st == "GATE_PARKED":
            phase = "GATE_PARKED"
        elif st == "ACTIVE":
            phase = _phase_for(fid, _progress[fid])
        else:
            phase = None
        out[fid] = {
            "status": st,
            "phase": phase,
            "assigned_gate": meta.get("assigned_gate"),
            "runway": meta.get("runway"),
        }
    return out

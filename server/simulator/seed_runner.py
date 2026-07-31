"""
Lakebase seed-based position runner.

Architecture:
  live_positions  →  the only per-flight live table. A flight has a row here
                     EXACTLY while it's active on the map (written on activation,
                     DELETEd when it finishes). Its presence alone marks an active
                     plane — the old plane_status table was retired (redundant).

A flight sits idle (no row in live_positions, so it's absent from the map) until
its scheduled time hits and advance() activates it.

The raw seed_positions rows are hand-authored and inconsistent: the recorded
speed_kt label frequently doesn't match the actual distance between
consecutive lat/lon points (sometimes off by 3-4x, occasionally two
consecutive rows are literally identical), and different flights were
authored at very different paces — so playing the rows back 1-per-second
made planes move at wildly different, unrealistic speeds. Worse, the
lat/lon polyline itself was never derived from (or checked against) the
real taxiway/runway pavement — it's an independent, hand-picked dataset, so
several flights visibly cut across grass/apron instead of following the
drawn taxiway lines.

So the ARRIVING polyline is no longer taken from seed_positions at all.
Instead every flight starts from the same fixed touchdown point,
`_ARRIVAL_START_LONLAT` — a single real coordinate given directly (not
authored per-flight). A plane can't leave the runway right at touchdown, so
a straight landing-roll segment first takes it to one of two fixed exit
points (`_ARRIVAL_EXIT_WIDE_LONLAT` / `_ARRIVAL_EXIT_NARROW_LONLAT`, chosen
by capacity class — heavier aircraft roll further); from the exit, the rest
of the route is real taxiway pavement (runway edges forbidden —
`route(..., avoid_runway=True)`) to its (dynamically reserved, see below)
gate stand position, over the real taxiway/runway graph built from
hiaWays.json (taxiway_graph.py) — the same data the 3D scene draws pavement
from. That guarantees the plane stays on drawn pavement for the whole taxi
(after the landing roll), with a short final stub off the taxiway
centerline into the actual gate stand (real gates sit ~90-140 m back from
the centerline in the ramp area — expected, not an error).

The flight *identity* roster (callsign, origin, aircraft type, and an
ABSOLUTE scheduled touchdown time) comes live from AeroDataBox via
server/simulator/live_schedule.py — seed_positions is no longer read at all
for arrivals. Each flight sits SCHEDULED until sim_time (real UTC clock)
reaches its scheduled_arr, when advance() auto-activates it (assigns a gate +
route then); there's no manual bulk-start any more.

A shared, realistic landing-roll→taxi speed profile (_target_speed_kt())
then advances a distance-along-path progress value every tick: fast,
decelerating from touchdown (~150kt) to the runway exit (~30kt) over the
fixed, real touchdown-to-exit distance, then a steady ~18kt taxi (matching
departures) the rest of the way, slowing on final approach to the gate.
Routed distance varies a lot by gate (4-9 km observed), so total taxi
duration is not forced to a constant. `scheduled_arr` is the real TOUCHDOWN
time (from the live feed), not a committed PARKED time — a flight parks
whenever its own real (variable) taxi actually finishes after touching down.

Once a flight reaches the gate it sits PARKED for _GROUND_TIME_SEC (30 min),
then disappears entirely — no depart taxi, no outgoing flight, just gone —
and its gate is released back to the live registry (server/simulator/gates.py).
The Play button on /simulate re-enables once a flight is PARKED, and clicking
it again restarts that flight's ARRIVING run from the beginning, with a
freshly (randomly) reserved gate — assigned_gate is no longer read from
seed_positions' hand-authored column, only origin/callsign/aircraft_type are.
"""

import json
import math
import os
from datetime import datetime, timedelta

from server.db import db
from server.simulator import gates
from server.simulator import taxiway_graph
from server.simulator import runway
from server.simulator import live_schedule
from server.simulator import flight_store
from server.simulator import traffic

# Gate stand positions — synced copy of frontend/src/data/gateData.js's
# GATE_LONLAT (the actual source of truth for where gate markers are drawn).
# seed_positions' own PARKED lat/lon rows are hand-authored and stale relative
# to it (some gates were repositioned since via the Gate Editor), badly enough
# that two different gates' seed coordinates can end up within meters of each
# other — e.g. C6 and C8 parked on top of one another. Parking at the real
# gate position instead guarantees the plane lands exactly on its marker.
_GATE_LONLAT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gateLonLat.json")
with open(_GATE_LONLAT_PATH) as _f:
    _GATE_LONLAT: dict[str, list[float]] = json.load(_f)

# The arrival flight-identity roster is now live, sourced from AeroDataBox via
# server/simulator/live_schedule.py (get_arrivals()) and loaded in
# load_gate_positions(); the old static data/arrivalRoster.json is no longer read.

# The single fixed point every arrival's taxi-in starts from — given
# directly by the user, replacing the old per-flight touchdown anchors
# cycled from seed_positions (see module docstring). (lon, lat).
_ARRIVAL_START_LONLAT: tuple[float, float] = (51.605118, 25.256100)

# A plane can't leave the runway right at touchdown — it needs to roll down
# the runway to a real exit first. These are the two fixed exit points every
# arrival rolls to (straight line) from _ARRIVAL_START_LONLAT, chosen by
# capacity class (heavier aircraft need a longer roll, hence a different
# exit); from the exit, the rest of the route is real taxiway pavement only
# (route(..., avoid_runway=True) — see _assign_gate_and_route()). (lon, lat).
_ARRIVAL_EXIT_WIDE_LONLAT: tuple[float, float] = (51.594952, 25.279237)
_ARRIVAL_EXIT_NARROW_LONLAT: tuple[float, float] = (51.596822, 25.274857)

# Wide-body/A380 traffic needs two more fixed waypoints, in order, straight
# after its exit point before diverting onto the taxiway network into the
# gate; without them the shortest taxiway path off that exit runs straight
# back alongside runway pavement, visually reading as "using the runway as a
# taxiway" — one extra waypoint wasn't enough to steer clear of it either.
# Narrow-body traffic doesn't need this, its exit diverts onto the taxiway
# network cleanly. (lon, lat).
_ARRIVAL_EXIT_WIDE_DIVERT_LONLAT: tuple[float, float] = (51.595334, 25.280843)
_ARRIVAL_EXIT_WIDE_DIVERT2_LONLAT: tuple[float, float] = (51.596477, 25.280344)

# Ground-speed profile: fast, decelerating landing roll from touchdown to
# the (capacity-class) exit point, then a steady taxi speed the rest of the
# way, slowing on final approach to the gate. Distances vary a lot by gate
# (4-9 km observed), so — unlike an earlier fixed-duration simplification —
# duration is no longer forced to a constant; it's whatever this profile
# integrates to (see _remaining_arrival_seconds(), used both for the live ETA
# and to solve start_all_arrivals()'s schedule).
_ROLLOUT_START_KT = 150.0  # touchdown speed
_ROLLOUT_EXIT_KT = 30.0    # speed by the time it reaches the runway exit point
_TAXI_KT = 18.0            # steady taxi speed once past the exit — matches departures
_GATE_CREEP_M = 180.0      # start slowing this many meters before the gate
_GATE_CREEP_MIN_KT = 3.0
KT_TO_MPS = 0.514444

_rollout_dist: dict[str, float] = {}  # flight_id -> touchdown-to-exit distance (m), fixed per capacity class


def _target_speed_kt(fid: str, progress_m: float, total_m: float) -> float:
    rollout_m = _rollout_dist.get(fid, 0.0)
    if rollout_m > 0 and progress_m < rollout_m:
        t = progress_m / rollout_m
        return _ROLLOUT_START_KT + t * (_ROLLOUT_EXIT_KT - _ROLLOUT_START_KT)

    creep_m = min(_GATE_CREEP_M, max(total_m - rollout_m, 0.0) * 0.5)
    remaining = total_m - progress_m
    if creep_m > 0 and remaining <= creep_m:
        t = remaining / creep_m
        return _GATE_CREEP_MIN_KT + t * (_TAXI_KT - _GATE_CREEP_MIN_KT)
    return _TAXI_KT


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


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


_GROUND_TIME_SEC = 1800.0  # 30 min at the gate, then the flight disappears (see "GONE" state)

_gate_pos: dict[str, tuple[float, float]] = {}   # flight_id -> (lat, lon) at its CURRENTLY assigned gate
_roster: dict[str, dict] = {}                    # flight_id -> static metadata (assigned_gate mutated per bulk-start)
_path: dict[str, list] = {}                      # flight_id -> [(lat, lon), ...] ARRIVING waypoints (recomputed per bulk-start)
_cum_dist: dict[str, list] = {}                  # flight_id -> cumulative meters, aligned with _path
_total_dist: dict[str, float] = {}               # flight_id -> total ARRIVING arc length (m)

_started: dict[str, bool] = {}                   # flight_id -> bool
_state: dict[str, str] = {}                      # flight_id -> 'ARRIVING' | 'PARKED' | 'GONE'
_progress: dict[str, float] = {}                 # flight_id -> distance traveled (m) along _path
_park_heading: dict[str, float] = {}             # flight_id -> heading frozen on parking
_pending_delay: dict[str, float] = {}            # flight_id -> seconds left before it starts moving (bulk-start stagger)
_scheduled_arr: dict[str, datetime] = {}         # flight_id -> fixed, pre-committed TOUCHDOWN time (set by start_all_arrivals)
_ground_timer: dict[str, float] = {}             # flight_id -> seconds left at the gate before it disappears
_gap_min: dict[str, float] = {}                  # flight_id -> minutes after the PREVIOUS roster entry it should touch down
_gate_arrival_at: dict[str, datetime] = {}       # flight_id -> real sim_time it actually reached PARKED (set once, in advance())


async def load_gate_positions() -> None:
    """Load the live arrival roster (from live_schedule, sourced from
    AeroDataBox) and reset play state on startup. Each flight carries an
    absolute `scheduled_arr` (real touchdown time); it stays SCHEDULED until
    sim_time reaches that moment, when advance() auto-activates it — assigning
    a gate + taxi-in route then (no bulk "Schedule" click any more). Every
    flight taxis in from the same fixed _ARRIVAL_START_LONLAT, so there's no
    per-flight touchdown/heading data to load."""
    global _roster, _gate_pos, _path, _cum_dist, _total_dist, _gap_min
    global _started, _state, _progress, _park_heading, _pending_delay, _scheduled_arr, _ground_timer
    global _gate_arrival_at

    _roster = {}
    _gap_min = {}
    _started = {}
    _state = {}
    _progress = {}
    _park_heading = {}
    _pending_delay = {}
    _scheduled_arr = {}
    _ground_timer = {}
    _gate_arrival_at = {}
    _gate_pos = {}
    _path = {}
    _cum_dist = {}
    _total_dist = {}

    for row in live_schedule.get_arrivals():
        _add_arrival(row)

    print(f"[seed_runner] live arrival roster loaded: {len(_roster)} flights.")

    # Clear stale positions from a previous run so nothing shows on the map until
    # a flight's scheduled time hits and it re-activates. (Previously this reset
    # plane_status to INACTIVE and full_state JOINed against it; plane_status is
    # now retired, so we clear live_positions directly — the row's presence alone
    # marks an active plane. departures.load_roster clears its own rows right after
    # this, so a blanket clear here is safe given the startup order.)
    await db.execute("DELETE FROM live_positions")
    print("[seed_runner] live_positions cleared on startup")


def _add_arrival(row: dict) -> None:
    """Register one parsed live-arrival row as a SCHEDULED flight."""
    fid = row["callsign"]
    _roster[fid] = {
        "id":            fid,
        "callsign":      fid,
        "aircraft_type": row["aircraft_type"],
        "airline":       row.get("airline") or "QR",
        "origin":        row["origin"],
        "destination":   "DOH",
        "assigned_gate": None,  # arrivals reserve a simulated gate at activation
        "api_status":    row.get("api_status"),  # real-world status from AeroDataBox
    }
    _scheduled_arr[fid] = row["scheduled_utc"]
    _started[fid] = False
    _state[fid] = "SCHEDULED"
    _progress[fid] = 0.0
    _park_heading[fid] = 0.0
    _pending_delay[fid] = 0.0


async def refresh_roster(new_arrivals: list[dict], now: datetime) -> list[str]:
    """Hourly live refresh (called from live_schedule.run_loop): merge a fresh
    arrival window in, keyed by callsign.

    - SCHEDULED flights: touchdown time + metadata updated in place.
    - New callsigns: added.
    - SCHEDULED callsigns no longer in the window: dropped.
    - ARRIVING flights whose FRESH touchdown time (which honours the API's
      revised/delayed time — see live_schedule._parse_utc) is now still in the
      FUTURE: they jumped the gun on a stale, pre-delay time, so revert them to
      SCHEDULED at the corrected time (release gate/runway, clear their live
      rows) — they'll re-land at the right delayed time. This is how a delay
      published between hourly refreshes gets honoured for a flight that already
      started on the old time.
    - PARKED/GONE (already at the gate in the sim): left untouched — re-timing a
      plane that's already parked back into the air would look wrong.

    Returns the list of flight_ids that were reverted/dropped, so the caller can
    tell open browser tabs to remove them from the map.
    """
    incoming = {r["callsign"]: r for r in new_arrivals}
    removed: list[str] = []

    # Drop SCHEDULED flights that fell out of the window (never activated).
    for fid in [f for f, st in _state.items() if st == "SCHEDULED" and f not in incoming]:
        _forget_arrival(fid)
        removed.append(fid)

    for fid, row in incoming.items():
        if fid not in _roster:
            _add_arrival(row)  # new upcoming flight
        elif _state.get(fid) == "SCHEDULED":
            # Not yet activated — safe to update its schedule/metadata in place.
            _scheduled_arr[fid] = row["scheduled_utc"]
            _roster[fid]["aircraft_type"] = row["aircraft_type"]
            _roster[fid]["origin"] = row["origin"]
            _roster[fid]["airline"] = row.get("airline") or "QR"
            _roster[fid]["api_status"] = row.get("api_status")
        elif _state.get(fid) == "ARRIVING" and row["scheduled_utc"] > now:
            # Activated early on a now-superseded (earlier) time; the fresh
            # delayed touchdown is still in the future → put it back to SCHEDULED
            # so it re-lands at the corrected time.
            await _revert_arrival_to_scheduled(fid, row, now)
            removed.append(fid)
        else:
            # PARKED/GONE, or ARRIVING with a fresh time already in the past — the
            # flight's simulation state (position/gate/schedule) must NOT be
            # disturbed, but its real-world api_status should still track the feed
            # (e.g. Expected → Arrived) so the Schedule board doesn't stay frozen
            # on the pre-landing status. Only the display-only fields are updated.
            if fid in _roster:
                _roster[fid]["api_status"] = row.get("api_status")

    return removed


async def _revert_arrival_to_scheduled(fid: str, row: dict, now: datetime) -> None:
    """Undo an early activation: release its gate + arrival-runway lock, clear
    its live DB rows, and reset it to SCHEDULED at the fresh (delayed) time."""
    gates.release_gate(_roster[fid].get("assigned_gate"), now, fid)
    runway.arr_release(fid)
    await db.execute("DELETE FROM live_positions WHERE flight_id=$1", fid)
    _roster[fid]["assigned_gate"] = None
    _roster[fid]["aircraft_type"] = row["aircraft_type"]
    _roster[fid]["origin"] = row["origin"]
    _roster[fid]["airline"] = row.get("airline") or "QR"
    _roster[fid]["api_status"] = row.get("api_status")
    _scheduled_arr[fid] = row["scheduled_utc"]
    _started[fid] = False
    _state[fid] = "SCHEDULED"
    _progress[fid] = 0.0
    _park_heading[fid] = 0.0
    _pending_delay[fid] = 0.0
    _gate_pos.pop(fid, None)
    _path.pop(fid, None)
    _cum_dist.pop(fid, None)
    _total_dist.pop(fid, None)
    _gate_arrival_at.pop(fid, None)
    _ground_timer.pop(fid, None)


def _forget_arrival(fid: str) -> None:
    """Remove all per-flight state for a flight (dropped from the window)."""
    for d in (_roster, _scheduled_arr, _started, _state, _progress, _park_heading,
              _pending_delay, _ground_timer, _gate_arrival_at, _gate_pos,
              _path, _cum_dist, _total_dist, _gap_min):
        d.pop(fid, None)
    traffic.clear(fid)


def list_flights() -> list:
    """Static roster for the /simulate page, including flights never started.

    `phase` is None until first played; once played it's 'ARRIVING' (in
    transit — Play stays disabled) or 'PARKED' (finished — Play re-enables
    and restarts the run from the beginning).
    """
    return [
        {
            **meta,
            "mode": "seeded",
            "started": _started[fid],
            "phase": _state[fid] if _started[fid] else None,
        }
        for fid, meta in _roster.items()
    ]


def _remaining_arrival_seconds(fid: str, progress: float, total: float) -> float:
    """Integrate the landing-roll->taxi speed profile from `progress` to
    `total`, rather than dividing remaining distance by the *current* speed
    — that single-speed shortcut is badly wrong early in a flight's run,
    since speed drops sharply from a ~150kt touchdown roll to ~30kt at the
    runway exit before settling at an 18kt taxi."""
    step_m = 50.0
    p, t = progress, 0.0
    while p < total:
        seg = min(step_m, total - p)
        spd_mps = max(_target_speed_kt(fid, p, total) * KT_TO_MPS, 0.5)
        t += seg / spd_mps
        p += seg
    return t


def seconds_to_next_arrival(now: datetime) -> float:
    """Seconds until the next arrival is due to touch down on the west (landing)
    runway, considering flights that will still USE the runway:
      - SCHEDULED flights whose touchdown time is in the future, and
      - ARRIVING flights that haven't yet passed their runway exit (still on the
        landing roll — they're on/over the runway right now, so gap = 0).
    Returns a large sentinel (float('inf')) if no arrival is pending. Used by
    departures.py to decide whether the west runway has a long-enough gap to
    borrow for a takeoff."""
    best = float("inf")
    for fid, st in _state.items():
        if st == "SCHEDULED":
            sa = _scheduled_arr.get(fid)
            if sa is not None:
                gap = (sa - now).total_seconds()
                if gap >= 0:
                    best = min(best, gap)
        elif st == "ARRIVING":
            # Still on or approaching the landing roll → runway is in use now.
            if _progress.get(fid, 0.0) < _rollout_dist.get(fid, 0.0):
                return 0.0
    return best


def arrivals_schedule_info() -> list:
    """Per-arrival status + fixed schedule for the Schedule page, sorted by
    `scheduled_arr` — the real TOUCHDOWN time from the live feed (not live
    progress), so the list order matches the schedule shown and doesn't
    reshuffle as flights move. `eta_seconds` is a
    separate, live ETA to PARKED (queued delay + remaining taxi time, the
    latter integrated from the real speed profile) — it keeps ticking down
    after touchdown, unlike `scheduled_arr` which stays fixed. `gate_arrival`
    is the real sim_time the flight actually reached PARKED (set once, in
    advance()) — None until then; the frontend shows an estimate (touchdown
    + 5 min) before that and switches to this real value once parked."""
    out = []
    for fid, meta in _roster.items():
        if _state[fid] == "GONE":
            continue  # 30 min at the gate elapsed — disappears from the schedule entirely
        started = _started[fid]
        state = _state[fid]
        pending = _pending_delay.get(fid, 0.0)
        queued = started and state == "ARRIVING" and pending > 0.0
        eta_seconds = None
        if started:
            if state == "PARKED":
                eta_seconds = 0.0
            else:
                total = _total_dist.get(fid, 0.0)
                eta_seconds = max(0.0, pending) + _remaining_arrival_seconds(fid, _progress[fid], total)
        scheduled_arr = _scheduled_arr.get(fid)
        gate_arrival = _gate_arrival_at.get(fid)
        out.append({
            **meta,
            "started": started,
            "queued": queued,
            "phase": state if started else None,
            "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
            "scheduled_arr": scheduled_arr.isoformat() if scheduled_arr else None,
            "gate_arrival": gate_arrival.isoformat() if gate_arrival else None,
        })
    return sorted(
        out,
        key=lambda f: f["scheduled_arr"] if f["scheduled_arr"] is not None else "9999",
    )


def _position_at(fid: str, progress: float):
    """Interpolate (lat, lon, heading_deg) at `progress` meters along fid's ARRIVING path."""
    pts = _path[fid]
    cum = _cum_dist[fid]
    if len(pts) < 2:
        p = pts[0] if pts else _gate_pos.get(fid, (0.0, 0.0))
        return p[0], p[1], 0.0
    progress = max(0.0, min(progress, cum[-1]))
    # Linear scan is fine — paths are ~150-250 points.
    for i in range(len(cum) - 1):
        if progress <= cum[i + 1] or i == len(cum) - 2:
            seg_len = cum[i + 1] - cum[i]
            t = (progress - cum[i]) / seg_len if seg_len > 0 else 0.0
            t = max(0.0, min(1.0, t))
            lat = _lerp(pts[i][0], pts[i + 1][0], t)
            lon = _lerp(pts[i][1], pts[i + 1][1], t)
            heading = _bearing(pts[i], pts[i + 1])
            return lat, lon, heading
    return pts[-1][0], pts[-1][1], 0.0


def _assign_gate_and_route(fid: str, now: datetime) -> None:
    """Reserve a random, capacity-matched gate for `fid` from the live gate
    registry (releasing whatever gate it held before, if any), and recompute
    its taxi-in path/distance: a straight landing-roll from the fixed
    _ARRIVAL_START_LONLAT to the capacity-class exit point, then real
    taxiway pavement (runway edges forbidden) from there to the gate. Gate
    assignment is decided fresh every (re)start. `now` (sim time) is passed
    straight through to gates.py for its occupancy history log."""
    meta = _roster[fid]
    gates.release_gate(meta.get("assigned_gate"), now, fid)
    gate_id = gates.reserve_gate(gates.capacity_class_of(meta["aircraft_type"]), fid, now)
    meta["assigned_gate"] = gate_id
    if gate_id is None:
        # Every gate is occupied right now — leave the flight un-routed and
        # un-positioned; advance() sees assigned_gate is None, keeps it
        # SCHEDULED, and retries a later tick (no crash, no degenerate PARK).
        _gate_pos.pop(fid, None)
        return

    # Record the gate the sim actually used onto the flight's flight_schedule row
    # (its occupancy window for the Gates page is derived from scheduled_utc + the
    # fixed dwell — see flight_store.recent_gate_history). Flushed async next tick.
    sched = _scheduled_arr.get(fid, now)
    flight_store.enqueue_sim_gate(fid, "arrival", sched, gate_id)

    lonlat = _GATE_LONLAT.get(gate_id)
    gate_latlon = (lonlat[1], lonlat[0]) if lonlat else None  # (lat, lon)
    _gate_pos[fid] = gate_latlon

    start = _ARRIVAL_START_LONLAT
    is_wide = gates.capacity_class_of(meta["aircraft_type"]) in ("WIDE", "A380")
    exit_lonlat = _ARRIVAL_EXIT_WIDE_LONLAT if is_wide else _ARRIVAL_EXIT_NARROW_LONLAT
    # Wide-body traffic gets two extra fixed waypoints, in order, straight
    # after the exit before diverting onto the taxiway network — see
    # _ARRIVAL_EXIT_WIDE_DIVERT_LONLAT's comment.
    divert_lonlats = ([_ARRIVAL_EXIT_WIDE_DIVERT_LONLAT, _ARRIVAL_EXIT_WIDE_DIVERT2_LONLAT]
                       if is_wide else [])
    # Fixed, real touchdown-to-exit distance — the landing-roll deceleration
    # (see _target_speed_kt()) plays out over exactly this many metres.
    _rollout_dist[fid] = _haversine_m((start[1], start[0]), (exit_lonlat[1], exit_lonlat[0]))
    routed = None
    if gate_latlon:
        gate_lonlat = (gate_latlon[1], gate_latlon[0])  # (lon, lat) — what route() expects
        taxi_from = divert_lonlats[-1] if divert_lonlats else exit_lonlat
        try:
            taxi_routed = taxiway_graph.route(taxi_from, gate_lonlat, avoid_runway=True, gate_id=gate_id)
        except Exception as e:  # noqa: BLE001
            print(f"[seed_runner] taxiway routing failed for {fid}: {e}")
            taxi_routed = None
        if taxi_routed:
            rollout = [(start[1], start[0]), (exit_lonlat[1], exit_lonlat[0])]
            rollout += [(d[1], d[0]) for d in divert_lonlats]
            routed = rollout[:-1] + taxi_routed  # rollout(s) up to taxi_from, then taxiway to gate
    if routed:
        _path[fid] = routed
    else:
        _path[fid] = [(start[1], start[0])]  # degenerate single-point path — won't move, won't crash

    pts = _path[fid]
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + _haversine_m(pts[i - 1], pts[i]))
    _cum_dist[fid] = cum
    _total_dist[fid] = cum[-1] if cum else 0.0


async def start_flight(flight_id: str, now: datetime):
    """(Re)start a seeded flight from the beginning of its ARRIVING track,
    with a freshly (randomly) assigned gate.

    Safe to call again on an already-PARKED flight — that's how the Play
    button on /simulate replays it.
    """
    if flight_id not in _roster:
        return None
    _assign_gate_and_route(flight_id, now)
    if _roster[flight_id].get("assigned_gate") is None:
        return None  # no gate free right now — caller can retry
    runway.arr_enter(flight_id)  # manual replay claims the landing roll (released at the exit)
    _started[flight_id] = True
    _state[flight_id] = "ARRIVING"
    _progress[flight_id] = 0.0
    _pending_delay[flight_id] = 0.0
    _gate_arrival_at.pop(flight_id, None)
    return await _tick_one(flight_id)


def register_traffic() -> None:
    """PHASE 1 of the per-tick ground-traffic protocol (see traffic.py): register
    every taxiing arrival's CURRENT position + occupied edge BEFORE any flight
    moves, so head-on resolution doesn't depend on phase-2 processing order.
    Called by the broadcaster right after traffic.begin_tick()."""
    for fid, state in _state.items():
        if state != "ARRIVING":
            continue
        traffic.register_current(fid, _path.get(fid, []), _cum_dist.get(fid, [0.0]),
                                 _progress.get(fid, 0.0), "arrival")


def advance(now: datetime, tick_sec: float, speed: int) -> None:
    dt = tick_sec * speed  # tick_sec is the REAL elapsed wall-clock seconds (float)
    # LEADER-FIRST within the arrival stream: process flights nearest their gate
    # (most progress remaining consumed) first, so a following flight yields to the
    # one ahead of it, never the reverse — the ordering traffic.step relies on to
    # stay deadlock-free. Non-ARRIVING states are order-independent; sort is a
    # no-op for them (their remaining distance falls back to +inf so they trail).
    def _remaining(fid):
        return _total_dist.get(fid, 0.0) - _progress.get(fid, 0.0)
    order = sorted(_state.keys(),
                   key=lambda f: _remaining(f) if _state.get(f) == "ARRIVING" else float("inf"))
    for fid in order:
        state = _state[fid]
        if state == "SCHEDULED":
            # Auto-activate at the real touchdown time. Two gates to clear
            # first, in this order:
            #   1. The arrival runway must be free (only one aircraft on the
            #      landing-roll segment at a time) — otherwise HOLD (stay
            #      SCHEDULED, delay touchdown) and retry next tick. Checked
            #      BEFORE reserving a gate so a waiting flight doesn't tie up a
            #      stand while holding.
            #   2. A capacity-matched gate must be free (else retry next tick).
            if now >= _scheduled_arr.get(fid, now) and not _started.get(fid, False):
                if not runway.arr_is_clear():
                    continue  # another arrival is on the runway — hold, retry next tick
                _assign_gate_and_route(fid, now)
                if _roster[fid].get("assigned_gate") is None:
                    continue  # no gate available yet — try again next tick
                runway.arr_enter(fid)  # claim the landing-roll segment
                _started[fid] = True
                _state[fid] = "ARRIVING"
                _progress[fid] = 0.0
                _pending_delay[fid] = 0.0
                _gate_arrival_at.pop(fid, None)
            continue
        if state == "ARRIVING":
            total = _total_dist.get(fid, 0.0)
            spd_kt = _target_speed_kt(fid, _progress[fid], total)
            step_m = spd_kt * KT_TO_MPS * dt
            rollout = _rollout_dist.get(fid, 0.0)
            cur = _progress[fid]
            proposed = cur + step_m
            # Edge-reservation separation on the TAXIWAY portion. The touchdown->exit
            # landing roll (progress < rollout) is high speed and already
            # deconflicted by the single-occupant arrival-runway lock, so it is NOT
            # clamped (enforce=False) — but its footprint is still registered so
            # taxiing traffic near the runway exit gives way. Once past the exit,
            # full head-on + following clamping applies (arrivals are advanced
            # before departures — see broadcaster — so a departure yields to us).
            enforce = proposed >= rollout
            _progress[fid] = traffic.step(fid, _path[fid], _cum_dist[fid],
                                          cur, proposed, "arrival", enforce=enforce)
            # Free the arrival runway the moment this flight clears its exit
            # point (end of the touchdown->exit roll) onto the taxiway, so the
            # next arrival can touch down. Idempotent: a no-op once released.
            if _progress[fid] >= rollout:
                runway.arr_release(fid)
            if _progress[fid] >= total:
                pts = _path[fid]
                _park_heading[fid] = _bearing(pts[-2], pts[-1]) if len(pts) >= 2 else 0.0
                _state[fid] = "PARKED"
                _ground_timer[fid] = _GROUND_TIME_SEC
                _gate_arrival_at[fid] = now  # real time it actually reached the gate
                # Parked at the stand (off the main lanes) — stop reserving
                # taxiway space so traffic can flow past the terminal.
                traffic.clear(fid)
        elif state == "PARKED":
            # Go GONE once _GROUND_TIME_SEC of REAL time has elapsed since the
            # flight parked. Measured from _gate_arrival_at (the real sim_time it
            # reached the gate), NOT by subtracting `dt` each tick: sim_time
            # tracks the wall clock, but a tick can take well over 1s under load
            # (100+ live flights each upserting), so a tick-counted timer runs far
            # slower than real time and flights linger at the gate long past 30 min.
            parked_at = _gate_arrival_at.get(fid, now)
            if (now - parked_at).total_seconds() >= _GROUND_TIME_SEC:
                _state[fid] = "GONE"
        # "GONE" flights: nothing left to advance — tick() sends the one-shot removal.


async def _tick_one(fid: str):
    """Compute fid's current position from its state machine, upsert live tables."""
    meta = _roster[fid]

    if _state[fid] == "PARKED":
        lat, lon = _gate_pos.get(fid) or (0.0, 0.0)
        # Nose-in gate heading (authoritative, consistent for every plane at
        # this stand); fall back to the taxi-path heading only if the gate has
        # no stored heading.
        heading = gates.gate_heading(meta.get("assigned_gate"))
        if heading is None:
            heading = _park_heading[fid]
        speed_kt = 0.0
        phase = "PARKED"
    else:
        total = _total_dist.get(fid, 0.0)
        lat, lon, heading = _position_at(fid, _progress[fid])
        speed_kt = round(_target_speed_kt(fid, _progress[fid], total), 1)
        phase = "ARRIVING"

    await db.execute(
        """INSERT INTO live_positions
             (flight_id, callsign, aircraft_type, airline, origin, destination,
              assigned_gate, lat, lon, heading_deg, phase, speed_kt, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12, now())
           ON CONFLICT (flight_id) DO UPDATE SET
             lat=EXCLUDED.lat, lon=EXCLUDED.lon,
             heading_deg=EXCLUDED.heading_deg, phase=EXCLUDED.phase,
             speed_kt=EXCLUDED.speed_kt, updated_at=now()""",
        fid, meta["callsign"], meta["aircraft_type"], meta["airline"],
        meta["origin"], meta["destination"], meta["assigned_gate"],
        lat, lon, heading, phase, speed_kt,
    )

    return {
        "id":            fid,
        "callsign":      meta["callsign"],
        "aircraft_type": meta["aircraft_type"],
        "airline":       meta["airline"],
        "origin":        meta["origin"],
        "destination":   meta["destination"],
        "assigned_gate": meta["assigned_gate"],
        "pax_count":     0,
        "status":        "ACTIVE",
        "phase":         phase,
        "lat":           lat,
        "lon":           lon,
        "heading":       heading,
        "ground_speed":  speed_kt,
        "source":        "lakebase",
        "started":       True,
        "scheduled_arr": _scheduled_arr[fid].isoformat() if fid in _scheduled_arr else None,
        "gate_arrival":  _gate_arrival_at[fid].isoformat() if _gate_arrival_at.get(fid) else None,
    }


async def _finish_arrival(fid: str, now: datetime) -> dict:
    """30 minutes at the gate elapsed — the flight disappears entirely (no
    depart taxi, no outgoing flight) and its gate is released back to the
    live registry."""
    meta = _roster[fid]
    gates.release_gate(meta.get("assigned_gate"), now, fid)
    runway.arr_release(fid)  # defensive — normally released at the runway exit already
    await db.execute("DELETE FROM live_positions WHERE flight_id=$1", fid)
    return {"id": fid, "callsign": meta["callsign"], "phase": "INACTIVE", "source": "lakebase"}


async def tick(now: datetime) -> list:
    """Return broadcast-ready payloads for every currently-started flight."""
    payloads = []
    for fid, started in list(_started.items()):
        if not started:
            continue
        if _pending_delay.get(fid, 0.0) > 0.0:
            continue  # queued (bulk-start stagger) — stays off the map until its turn
        if _state[fid] == "GONE":
            payloads.append(await _finish_arrival(fid, now))
            _started[fid] = False
            continue
        payloads.append(await _tick_one(fid))
    return payloads


async def full_state() -> list:
    """Current snapshot for new WebSocket clients — only active planes."""
    # A flight has a live_positions row EXACTLY while it's active on the map — the
    # row is written on activation and DELETEd when it finishes/goes inactive (see
    # _finish_arrival / departures._finish_one). So the row's presence alone marks
    # an active plane; the old JOIN against plane_status was redundant (that table
    # is retired).
    rows = await db.fetch(
        """SELECT flight_id, callsign, aircraft_type, airline,
                  origin, destination, assigned_gate,
                  lat, lon, heading_deg, phase, speed_kt
           FROM live_positions"""
    )
    result = []
    for r in rows:
        result.append({
            "id":            r["flight_id"],
            "callsign":      r["callsign"],
            "aircraft_type": r["aircraft_type"],
            "airline":       r["airline"],
            "origin":        r["origin"],
            "destination":   r["destination"],
            "assigned_gate": r["assigned_gate"],
            "pax_count":     0,
            "status":        "ACTIVE",
            "phase":         r["phase"],
            "lat":           float(r["lat"]),
            "lon":           float(r["lon"]),
            "heading":       float(r["heading_deg"]),
            "ground_speed":  float(r["speed_kt"]),
            "source":        "lakebase",
            "started":       True,
            "scheduled_arr": _scheduled_arr[r["flight_id"]].isoformat() if r["flight_id"] in _scheduled_arr else None,
            "gate_arrival":  _gate_arrival_at[r["flight_id"]].isoformat() if _gate_arrival_at.get(r["flight_id"]) else None,
        })
    return result

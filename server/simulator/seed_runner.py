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
authored per-flight). From there the whole path is built ON the drawn
pavement by `taxiway_graph.arrival_route()`: `_rollout` traces the REAL
runway centerline from touchdown to a genuine (~90°) rapid-exit taxiway node
(heavier aircraft roll one exit further via `one_exit_further`), and the taxi
leg then routes from that exit node — a real taxiway junction — to the
(dynamically reserved, see below) gate stand, with runway edges forbidden,
over the same hiaWays.json graph the 3D scene draws pavement from. That keeps
the plane on drawn pavement for the entire landing roll AND taxi (an earlier
version hand-placed straight exit/divert waypoints, which cut a diagonal
across open ground between runway and taxiway — replaced by this), with only a
short final stub off the taxiway centerline into the actual gate stand (real
gates sit ~90-140 m back from the centerline in the ramp area — expected, not
an error). `arrival_route` returns the exit-anchor index so the roll distance
(`_rollout_dist`, driving the deceleration profile + the arr-runway release
boundary) is measured along the real centerline, not a straight hop.

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

Once a flight reaches the gate it sits PARKED for _ground_time_sec() (25–55 min),
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
from datetime import datetime, timedelta, timezone

from server.simulator import gates
from server.simulator import taxiway_graph
from server.simulator import runway
from server.simulator import flight_store
from server.simulator import traffic
from server.simulator import positions
from server.simulator import route_reservations
from server.simulator import turnarounds
from server.simulator import sim_events

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

# A down-runway point (lon, lat) in the direction of travel from touchdown. It is
# ONLY a direction hint for taxiway_graph.arrival_route()/_rollout() — which then
# traces the REAL runway centerline to a genuine (~90°) rapid-exit taxiway node and
# routes onto pavement from there. Superseded the old hand-placed straight exit +
# divert hops (which cut across open ground); those are no longer used.
_ARRIVAL_HEADING_LONLAT: tuple[float, float] = (51.596822, 25.274857)

# The ONE exit taxiway every arrival vacates the runway at (A, user-chosen). All
# arrivals roll the runway centerline to abeam this point, turn off onto it, then
# taxi to their gate — a single, deterministic exit. Combined with the single-
# arrival runway lock, landings space themselves one-after-another at this exit.
# A sits on the EXIT taxiway (~14 m off the runway edge), not the near-runway
# taxiway — otherwise route(A→B) dips back toward the runway before climbing out.
# (lon, lat). Set to None to fall back to the per-flight distance-based exit search.
_ARRIVAL_FIXED_EXIT_LONLAT: tuple[float, float] | None = (51.595206, 25.278918)

# Forced waypoint (B) the taxi passes through after the exit (A): route is
# exit -> via -> gate, each leg on the taxiway graph. Pins arrivals onto the intended
# path so they never re-cross the runway. None → exit -> gate directly. (lon, lat).
_ARRIVAL_VIA_LONLAT: tuple[float, float] | None = (51.596220, 25.280890)


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

# Gates that turned out to have NO taxiway route from the runway exit (their
# coordinate in data/gateLonLat.json isn't connected to the routable graph built
# from hiaWays.json — e.g. E6). Discovered lazily on the first failed routing
# attempt and then permanently skipped by reserve_gate(), so no later arrival can
# draw the same dead stand again. Process-lifetime memo; deliberately NOT reset on
# roster refresh, since the geometry can't change while the app runs.
_UNROUTABLE_GATES: set[str] = set()


def unroutable_gates() -> list[str]:
    """Gates excluded from arrival assignment because they proved unroutable.
    Surfaced by /api/live-debug so a bad stand coordinate is visible rather than
    silently degrading capacity."""
    return sorted(_UNROUTABLE_GATES)


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


def _ground_time_sec(fid: str) -> float:
    """How long this arrival sits at its gate before it disappears (GONE) and the
    gate is released: 25–55 min, fixed per flight. Uses the SAME deterministic
    per-flight dwell as the Gates page history (flight_store._dwell_seconds), so
    the map and the history agree, and a restart keeps the same value."""
    return flight_store._dwell_seconds(fid, "arrival", _scheduled_arr.get(fid) or datetime.now(timezone.utc))

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

    # Roster content is NOT loaded here any more. The generator pulls it from
    # `flight_schedule` via broadcaster._pull_roster() → refresh_roster(), because the
    # fetcher is now a separate scheduled job and live_schedule's in-process globals are
    # empty here. This function only resets per-flight state + gate geometry.
    print("[seed_runner] arrival state reset; roster arrives via refresh_roster().")

    # Clear stale positions from a previous run so nothing shows on the map until
    # a flight's scheduled time hits and it re-activates. (Previously this reset
    # plane_status to INACTIVE and full_state JOINed against it; plane_status is
    # now retired, so we clear live_positions directly — the row's presence alone
    # marks an active plane. departures.load_roster clears its own rows right after
    # this, so a blanket clear here is safe given the startup order.)
    route_reservations.clear_all()  # no taxi reservations survive a restart
    await positions.clear_all()
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
        # Gate the sim used for this flight on a PREVIOUS run (from flight_schedule).
        # Reused at (re)activation so a reconstructed flight lands at the same stand
        # + route — no jump after a restart/airport switch. None for never-seen flights.
        "sim_gate":      row.get("sim_gate"),
        "api_status":    row.get("api_status"),  # real-world status from AeroDataBox
    }
    _scheduled_arr[fid] = row["scheduled_utc"]
    _started[fid] = False
    _state[fid] = "SCHEDULED"
    _progress[fid] = 0.0
    _park_heading[fid] = 0.0
    _pending_delay[fid] = 0.0


async def refresh_roster(new_arrivals: list[dict], now: datetime) -> list[str]:
    """Merge a fresh arrival window in (called from broadcaster._pull_roster, which
    reads flight_schedule every 60s):
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

    # Drop flights that fell out of the (widened) window: SCHEDULED ones that never
    # activated, and GONE ones (finished / reconstructed-as-departed) — so neither
    # accumulates in memory forever now that the window reaches ~50 min back.
    for fid in [f for f, st in _state.items() if st in ("SCHEDULED", "GONE") and f not in incoming]:
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
    route_reservations.release(fid)
    await positions.remove(fid)
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
    route_reservations.release(fid)


# A flight whose scheduled touchdown is more than this many seconds in the past is
# RECONSTRUCTED (placed at its computed current position — mid-taxi or parked)
# rather than flown live from touchdown. The threshold sits comfortably past the
# ~50-60 s landing roll so a reconstructed flight is always off the runway and never
# needs the single-occupant arrival-runway lock. Flights newer than this are treated
# as genuine live landings (progress 0, claim the lock — the original path).
_RECONSTRUCT_AFTER_SEC = 120.0


def _progress_after_seconds(fid: str, elapsed: float, total: float) -> float:
    """Distance-along-path (m) reached after `elapsed` seconds of the landing-roll→
    taxi speed profile, starting from progress 0. The inverse of
    _remaining_arrival_seconds — used to RECONSTRUCT where a flight that touched down
    `elapsed` seconds ago should be right now. Capped at `total`."""
    if elapsed <= 0 or total <= 0:
        return 0.0
    step_m = 50.0
    p, t = 0.0, 0.0
    while p < total and t < elapsed:
        spd = max(_target_speed_kt(fid, p, total) * KT_TO_MPS, 0.5)
        dt_seg = step_m / spd
        if t + dt_seg > elapsed:            # finish the final partial step in time
            p += spd * (elapsed - t)
            break
        p += step_m
        t += dt_seg
    return min(p, total)


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


def sim_overlay() -> dict[str, dict]:
    """Live SIM state per arrival callsign, for the Schedule board to left-join
    onto its Lakebase query (see app.py's /api/schedule).

    The board's ROW SET now comes from the flight_schedule dataset, not this
    roster — so this supplies only what the dataset can't know: the simulated
    phase, the gate the sim reserved, and `gate_arrival` (the real sim_time the
    flight actually reached PARKED, set once in advance()).

    GONE flights are INCLUDED here, unlike the old arrivals_schedule_info() which
    dropped them. The dataset upsert only refreshes api_status for FUTURE flights,
    so a landed arrival's raw status can still read 'Expected'; the frontend turns
    any flight with a `phase` into LANDED, so keeping GONE in the overlay is what
    stops a flight that landed 40 min ago from showing EXPECTED on the board.

    Replaces arrivals_schedule_info(). `eta_seconds`/`queued`/`started` are not
    reproduced: nothing in the frontend reads them (it derives its own ETA from
    scheduled_arr/gate_arrival), and eta_seconds ran a 50-metre-step integration
    loop per flight — too costly to recompute for every row on every request."""
    out: dict[str, dict] = {}
    for fid, meta in _roster.items():
        state = _state[fid]
        gate_arrival = _gate_arrival_at.get(fid)
        out[fid] = {
            "phase": state if _started[fid] else None,
            "assigned_gate": meta.get("assigned_gate"),
            "gate_arrival": gate_arrival.isoformat() if gate_arrival else None,
        }
    return out


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


def _assign_gate_and_route(fid: str, now: datetime) -> bool:
    """Reserve a random, capacity-matched gate for `fid` from the live gate
    registry (releasing whatever gate it held before, if any), and recompute
    its taxi-in path/distance: a straight landing-roll from the fixed
    _ARRIVAL_START_LONLAT to the capacity-class exit point, then real
    taxiway pavement (runway edges forbidden) from there to the gate. Gate
    assignment is decided fresh every (re)start. `now` (sim time) is passed
    straight through to gates.py for its occupancy history log.

    Returns True only if the flight ended up with BOTH a gate and a real routed
    path, i.e. it's safe to activate. False means the caller must NOT activate it
    (leave it SCHEDULED and retry a later tick) — activating on a failed route
    used to leave a zero-length path that instantly "parked" while still holding
    the single arrival-runway lock, which then blocked every later arrival for the
    whole ground time. See the release guard in advance()."""
    meta = _roster[fid]
    gates.release_gate(meta.get("assigned_gate"), now, fid)
    # Reuse the gate this flight used before (persisted sim_gate) if it's free and
    # routable, so a reconstructed arrival keeps its original stand + route; else
    # fall back to a fresh random capacity-matched gate (first-time flights).
    preferred = meta.get("sim_gate")
    gate_id = None
    if preferred and preferred not in _UNROUTABLE_GATES:
        gate_id = gates.claim_gate(preferred, fid, now)
    if gate_id is None:
        gate_id = gates.reserve_gate(gates.capacity_class_of(meta["aircraft_type"]), fid, now,
                                     exclude=_UNROUTABLE_GATES)
    meta["assigned_gate"] = gate_id
    if gate_id is None:
        # Every gate is occupied right now — leave the flight un-routed and
        # un-positioned; advance() sees assigned_gate is None, keeps it
        # SCHEDULED, and retries a later tick (no crash, no degenerate PARK).
        _gate_pos.pop(fid, None)
        return False

    # Record the gate the sim actually used onto the flight's flight_schedule row
    # (its occupancy window for the Gates page is derived from scheduled_utc + the
    # fixed dwell — see flight_store.recent_gate_history). Flushed async next tick.
    sched = _scheduled_arr.get(fid, now)
    flight_store.enqueue_sim_gate(fid, "arrival", sched, gate_id)

    lonlat = _GATE_LONLAT.get(gate_id)
    gate_latlon = (lonlat[1], lonlat[0]) if lonlat else None  # (lat, lon)
    _gate_pos[fid] = gate_latlon

    routed = None
    taxi_nodes = None
    rollout_index = 0
    if gate_latlon:
        gate_lonlat = (gate_latlon[1], gate_latlon[0])  # (lon, lat) — what route() expects
        try:
            # Land on the REAL runway and stay on drawn pavement the whole way: a
            # centerline landing-roll to a genuine (~90°) rapid-exit taxiway node,
            # then taxiway routing to the gate — no hand-placed straight hops cutting
            # across open ground. `one_exit_further=True` for ALL arrivals: the next
            # square exit past the mid-field one sits at ~3/4 of the runway (~3.5 km
            # roll), which reads as a realistic long rollout rather than turning off
            # in the middle (there is no 90° exit between the two, so this is the
            # nearest to 3/4). avoid_edges steers clear of opposing departures.
            routed, taxi_nodes, rollout_index = taxiway_graph.arrival_route(
                _ARRIVAL_START_LONLAT, _ARRIVAL_HEADING_LONLAT, gate_lonlat,
                one_exit_further=True, gate_id=gate_id,
                avoid_edges=route_reservations.blocked_for(fid),
                fixed_exit_lonlat=_ARRIVAL_FIXED_EXIT_LONLAT,
                via_lonlat=_ARRIVAL_VIA_LONLAT)
        except Exception as e:  # noqa: BLE001
            print(f"[seed_runner] arrival routing failed for {fid}: {e}")
            routed, taxi_nodes, rollout_index = None, None, 0
    if not routed:
        # No taxiway route from the runway exit to this stand (e.g. a gate whose
        # coordinate isn't connected to the routable graph). Do NOT fabricate a
        # degenerate single-point path: that used to read as _total_dist == 0, so
        # the flight "arrived" the instant it activated and skipped the
        # rollout-based arr_release(), stranding the arrival-runway lock. Instead
        # remember the gate as unroutable, hand it back, and report failure so the
        # flight stays SCHEDULED and retries with a different stand next tick.
        print(f"[seed_runner] no taxi route to gate {gate_id} for {fid} — "
              f"excluding that gate and retrying next tick")
        _UNROUTABLE_GATES.add(gate_id)
        gates.release_gate(gate_id, now, fid)
        route_reservations.release(fid)
        meta["assigned_gate"] = None
        _gate_pos.pop(fid, None)
        _path.pop(fid, None)
        _cum_dist.pop(fid, None)
        _total_dist.pop(fid, None)
        return False

    _path[fid] = routed
    pts = _path[fid]
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + _haversine_m(pts[i - 1], pts[i]))
    _cum_dist[fid] = cum
    _total_dist[fid] = cum[-1] if cum else 0.0
    # Landing-roll distance in the SAME units as `cum`/progress: the arc length from
    # touchdown to the runway-exit anchor (coords[rollout_index], from arrival_route).
    # Drives the roll deceleration profile (_target_speed_kt) and the enforce /
    # arr_release boundary in advance(); the touchdown→exit portion is a runway roll,
    # everything after it is taxiway.
    _rollout_dist[fid] = cum[rollout_index] if 0 <= rollout_index < len(cum) else 0.0
    # A routed path must have real length — otherwise the same instant-park /
    # stranded-lock failure mode applies. Treat it exactly like a routing failure.
    if _total_dist[fid] <= 0.0:
        print(f"[seed_runner] zero-length route to gate {gate_id} for {fid} — "
              f"excluding that gate and retrying next tick")
        _UNROUTABLE_GATES.add(gate_id)
        gates.release_gate(gate_id, now, fid)
        route_reservations.release(fid)
        meta["assigned_gate"] = None
        _gate_pos.pop(fid, None)
        _path.pop(fid, None)
        _cum_dist.pop(fid, None)
        _total_dist.pop(fid, None)
        return False
    # Reserve the taxi-in edges so a departure planned while this arrival taxis
    # avoids being routed head-on into it. Released when it parks (off the lanes).
    route_reservations.reserve(fid, taxi_nodes)
    return True


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


def _watchdog_arrival_lock() -> None:
    """Free the arrival landing-roll lock if its holder can't possibly still be on
    the runway. The lock is a single global gate on EVERY arrival activation, so one
    stranded holder silently empties the whole arrivals board — the failure this
    guards. A holder is stale if it's no longer in the roster, isn't ARRIVING, or is
    already past its rollout distance. Cheap (one dict lookup) and runs before any
    activation each tick, so recovery takes one tick instead of 30 minutes."""
    occ = runway.arr_occupant()
    if occ is None:
        return
    state = _state.get(occ)
    if state != "ARRIVING":
        # Gone from the roster, or PARKED/GONE — either way it isn't rolling.
        print(f"[seed_runner] watchdog: arrival runway lock held by {occ} "
              f"(state={state}) — releasing")
        runway.arr_release(occ)
        return
    rollout = _rollout_dist.get(occ, 0.0)
    if rollout <= 0.0 or _progress.get(occ, 0.0) >= rollout:
        print(f"[seed_runner] watchdog: arrival runway lock held by {occ} past its "
              f"rollout — releasing")
        runway.arr_release(occ)


def advance(now: datetime, tick_sec: float, speed: int) -> None:
    dt = tick_sec * speed  # tick_sec is the REAL elapsed wall-clock seconds (float)
    _watchdog_arrival_lock()
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
            sa = _scheduled_arr.get(fid, now)
            if now < sa or _started.get(fid, False):
                continue  # not due yet
            elapsed = (now - sa).total_seconds()

            if elapsed <= _RECONSTRUCT_AFTER_SEC:
                # LIVE touchdown — a genuine landing right now. Original path: the
                # single-occupant arrival runway must be clear (else HOLD, retry),
                # claim it, and start the roll from progress 0. Runway is checked
                # BEFORE reserving a gate so a waiting flight doesn't tie up a stand.
                if not runway.arr_is_clear():
                    continue
                if not _assign_gate_and_route(fid, now):
                    continue  # no gate / no route yet — try again next tick
                runway.arr_enter(fid)  # claim the landing-roll segment
                print(f"[arrivals] {fid} TOUCHDOWN → gate {_roster[fid].get('assigned_gate')}")
                sim_events.emit("TOUCHDOWN", fid, "arrival", now, _roster[fid],
                                scheduled_utc=sa.isoformat(), late_s=round(elapsed))
                _started[fid] = True
                _state[fid] = "ARRIVING"
                _progress[fid] = 0.0
                _pending_delay[fid] = 0.0
                _gate_arrival_at.pop(fid, None)
                continue

            # RECONSTRUCT (B2): this flight touched down well in the past — a restart
            # or an airport switch dropped it, or it first appeared already late.
            # Place it where it should be NOW (mid-taxi or parked, reusing its
            # persisted sim_gate for the same stand + route) instead of re-flying the
            # whole approach. It's past its landing roll by definition, so it never
            # takes the runway lock.
            if not _assign_gate_and_route(fid, now):
                continue  # no gate / route yet — retry next tick
            total = _total_dist.get(fid, 0.0)
            progress = _progress_after_seconds(fid, elapsed, total)
            if progress >= total:
                # Would already be at the gate. When did it park, and is any of its
                # dwell left?
                taxi_dur = _remaining_arrival_seconds(fid, 0.0, total)
                gate_arr = sa + timedelta(seconds=taxi_dur)
                if ((now - gate_arr).total_seconds() >= _ground_time_sec(fid)
                        and not turnarounds.arrival_should_hold(fid)):
                    # Its whole ground stay is over — it's GONE. Hand the gate back and
                    # mark it GONE so it's neither placed nor reconstructed again.
                    gates.release_gate(_roster[fid].get("assigned_gate"), now, fid)
                    _roster[fid]["assigned_gate"] = None
                    route_reservations.release(fid)
                    traffic.clear(fid)
                    _started[fid] = False
                    _state[fid] = "GONE"
                    print(f"[arrivals] {fid} skipped — landed {sa:%H:%M}Z, ground stay already over")
                    continue
                pts = _path[fid]
                _park_heading[fid] = _bearing(pts[-2], pts[-1]) if len(pts) >= 2 else 0.0
                _started[fid] = True
                _state[fid] = "PARKED"
                _progress[fid] = total
                _gate_arrival_at[fid] = gate_arr
                _ground_timer[fid] = _ground_time_sec(fid)
                _pending_delay[fid] = 0.0
                route_reservations.release(fid)  # parked — off the taxiways
                traffic.clear(fid)
                print(f"[arrivals] {fid} placed PARKED at {_roster[fid].get('assigned_gate')} "
                      f"(restart; at gate since {gate_arr:%H:%M}Z)")
                continue

            # Reconstruct mid-taxi. Past the rollout already, so no runway lock; keep
            # the taxi reservation from _assign_gate_and_route (it's on the taxiways).
            print(f"[arrivals] {fid} placed mid-taxi → gate {_roster[fid].get('assigned_gate')} "
                  f"(restart; landed {sa:%H:%M}Z)")
            _started[fid] = True
            _state[fid] = "ARRIVING"
            _progress[fid] = progress
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
            # `rollout <= 0` would mean no landing-roll segment at all, in which
            # case this condition must still fire — otherwise the lock leaks.
            if _progress[fid] >= rollout or rollout <= 0.0:
                runway.arr_release(fid)
            if _progress[fid] >= total:
                pts = _path[fid]
                _park_heading[fid] = _bearing(pts[-2], pts[-1]) if len(pts) >= 2 else 0.0
                _state[fid] = "PARKED"
                # Belt-and-braces: a flight that reaches its stand is definitively
                # off the runway, so the landing-roll lock MUST be free by now. If
                # any path ever parks without passing the rollout check above, this
                # stops one flight stranding the lock and starving every subsequent
                # arrival (the arrivals-board collapse this guards against).
                runway.arr_release(fid)
                _ground_timer[fid] = _ground_time_sec(fid)
                _gate_arrival_at[fid] = now  # real time it actually reached the gate
                print(f"[arrivals] {fid} PARKED at {_roster[fid].get('assigned_gate')}")
                sim_events.emit("GATE_IN", fid, "arrival", now, _roster[fid])
                # Parked at the stand (off the main lanes) — stop reserving
                # taxiway space so traffic can flow past the terminal.
                traffic.clear(fid)
                route_reservations.release(fid)  # no longer on the taxiways
        elif state == "PARKED":
            # Go GONE once _ground_time_sec(fid) of REAL time has elapsed since the
            # flight parked. Measured from _gate_arrival_at (the real sim_time it
            # reached the gate), NOT by subtracting `dt` each tick: sim_time
            # tracks the wall clock, but a tick can take well over 1s under load
            # (100+ live flights each upserting), so a tick-counted timer runs far
            # slower than real time and flights linger at the gate long past 30 min.
            parked_at = _gate_arrival_at.get(fid, now)
            # A configured turnaround (data/turnarounds.json) keeps the stand past its
            # dwell until its departure takes the aircraft over (hand_over_to_departure).
            if turnarounds.arrival_should_hold(fid):
                continue
            if (now - parked_at).total_seconds() >= _ground_time_sec(fid):
                _state[fid] = "GONE"
                print(f"[arrivals] {fid} GONE from {_roster[fid].get('assigned_gate')} "
                      f"after {(now - parked_at).total_seconds() / 60:.0f} min at gate")
                sim_events.emit("GATE_OUT", fid, "arrival", now, _roster[fid],
                                dwell_s=round((now - parked_at).total_seconds()))
        # "GONE" flights: nothing left to advance — tick() sends the one-shot removal.


def hand_over_to_departure(fid: str, dep_fid: str, now: datetime) -> None:
    """Turnaround handoff: the linked departure is taking this parked aircraft over.
    Free the stand (so the departure can claim the SAME gate this tick) and mark the
    arrival GONE — tick() then sends its one-shot removal, so the map never shows
    two planes. Called from departures.advance(), which runs after advance() here
    and before either module's tick(), so the swap lands within a single tick."""
    gates.release_gate(_roster[fid].get("assigned_gate"), now, fid)
    _state[fid] = "GONE"
    parked_at = _gate_arrival_at.get(fid, now)
    print(f"[arrivals] {fid} TURNAROUND → {dep_fid} at {_roster[fid].get('assigned_gate')} "
          f"after {(now - parked_at).total_seconds() / 60:.0f} min at gate")
    sim_events.emit("TURNAROUND_HANDOVER", fid, "arrival", now, _roster[fid],
                    to_flight=dep_fid, dwell_s=round((now - parked_at).total_seconds()))


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

    # Published to `sim_positions` — the generator → app contract (positions.py).
    # The DESCRIPTIVE fields (scheduled time, gate_arrival, api_status) travel WITH the
    # position because the app holds no simulation state and so cannot ask us for them.
    # `aircraft_reg` is the device identity: nullable, never a key.
    return await positions.upsert(
        fid, meta["callsign"], meta["aircraft_type"], meta["airline"],
        meta["origin"], meta["destination"], "arrival", meta["assigned_gate"],
        meta.get("aircraft_reg"), lat, lon, heading, phase, speed_kt,
        _scheduled_arr.get(fid), _gate_arrival_at.get(fid), None,
        meta.get("api_status"),
    )


async def _finish_arrival(fid: str, now: datetime) -> dict:
    """Its gate time (25–55 min) elapsed — the flight disappears entirely (no
    depart taxi, no outgoing flight) and its gate is released back to the
    live registry."""
    meta = _roster[fid]
    gates.release_gate(meta.get("assigned_gate"), now, fid)
    runway.arr_release(fid)  # defensive — normally released at the runway exit already
    route_reservations.release(fid)  # defensive — normally released at PARK already
    await positions.remove(fid)
    return {"id": fid, "callsign": meta["callsign"], "phase": "INACTIVE", "source": "lakebase"}


async def tick(now: datetime) -> tuple[list[str], list[dict]]:
    """Write this tick's arrival positions to Lakebase.

    Returns `(written_flight_ids, removal_payloads)`. It no longer returns position
    payloads: the broadcaster READS the rows back from `live_positions` to build what
    it broadcasts (see broadcaster._tick), so the map renders what the database holds
    rather than what these dicts hold. Removals are still returned directly — they're
    the one-shot "this flight is gone" signal, and by definition its row no longer
    exists to be read."""
    written, removed = [], []
    for fid, started in list(_started.items()):
        if not started:
            continue
        if _pending_delay.get(fid, 0.0) > 0.0:
            continue  # queued (bulk-start stagger) — stays off the map until its turn
        if _state[fid] == "GONE":
            removed.append(await _finish_arrival(fid, now))
            _started[fid] = False
            continue
        written.append(await _tick_one(fid))
    return written, removed


def payload_extras(fid: str) -> dict:
    """Arrival fields that are NOT position telemetry and so aren't in
    `live_positions`: the scheduled touchdown time and the real gate-arrival time.
    Merged onto the DB-sourced payload by the broadcaster.

    Returns {} for a flight this module doesn't own, so the broadcaster can ask both
    state machines without checking which one owns the flight."""
    if fid not in _roster:
        return {}
    out: dict = {}
    sched = _scheduled_arr.get(fid)
    if sched:
        out["scheduled_arr"] = sched.isoformat()
    ga = _gate_arrival_at.get(fid)
    if ga:
        out["gate_arrival"] = ga.isoformat()
    return out

"""Shared ground-traffic separation across the whole taxiway network — a
per-tick, priority-ordered SWEPT-POINT reservation.

Both arrivals (seed_runner.py) and departures (departures.py) move along
interpolated lat/lon paths over the SAME physical pavement. runway.py only
deconflicts the runway rolls; nothing on the taxiways stopped two aircraft
(a) bunching nose-to-tail in the same direction, or (b) an inbound and an
outbound driving through each other where their paths meet head-on or cross.

Model
-----
Each tick the broadcaster runs a two-phase protocol:

    traffic.begin_tick()                 # clear the registry
    seed_runner.register_traffic()       # PHASE 1: every taxiing flight's …
    departures.register_traffic()        #          … CURRENT footprint, both streams
    seed_runner.advance(...)             # PHASE 2: move arrivals   (each calls step)
    departures.advance(...)              # PHASE 2: move departures (each calls step)

Phase 1 registers where every taxiing aircraft currently sits BEFORE anyone
moves, so a flight never advances on top of a stationary one it happens to be
processed before. Phase 2 advances flights in PRIORITY order and, as each moves,
`step()` clamps its progress so it keeps clear of everything registered so far.

Priority / deadlock-freedom
---------------------------
  * Arrivals are advanced (phase 2) fully BEFORE departures → a departure always
    gives way to an arrival on shared pavement, never the reverse.
  * Within a stream flights advance LEADER-FIRST (nearest their destination
    first), so a follower yields to the leader ahead, never the reverse.
  * A flight yields (holds short) only to a footprint that is AHEAD of it or
    abeam — never one BEHIND. Combined with the processing order this keeps the
    highest-priority flight always moving (it never waits on lower-priority
    traffic), so there is no mutual-wait deadlock and no oscillation.

Two separation tiers:
  * SEP_M   — comfortable following / crossing gap. A flight yields to this
    distance from a leader ahead or a higher-priority flight already advanced
    this tick (crossing an intersection first, etc.).
  * HARD_M  — a hard anti-overlap minimum enforced against EVERY other flight
    ahead, including lower-priority ones still stationary this tick. This is what
    stops a higher-priority flight clipping through a plane in front of it in a
    true head-on: instead of passing through, it stops HARD_M short. (A genuine
    single-lane head-on then ends with the two stopped ~HARD_M apart rather than
    overlapping — rare with sane routing, and far better than the old clip.)

Parked / gate-parked aircraft are deliberately NOT registered: gates sit off the
main lanes and adjacent stands can be closer than these radii, which would
wrongly freeze traffic taxiing past an occupied terminal.
"""

import math

# Comfortable following / crossing separation (centre-to-centre). ~90 m clears a
# wide-body's ~70 m length nose-to-tail and reads as real spacing in a queue.
SEP_M = 90.0
# Hard anti-overlap minimum enforced against every flight ahead (see module doc).
HARD_M = 45.0
# Sample step (m) when sweeping a flight's proposed advance for conflicts.
_SWEEP_M = 15.0
# Anti-deadlock: on a genuine single-lane HEAD-ON, holding both flights forever is
# a permanent freeze (worse than the old occasional clip). If a flight is blocked
# purely by an OPPOSING (head-on) flight for this many consecutive ticks, it
# breaks through this tick — accepting a transient close pass so traffic always
# eventually flows. Same-direction following NEVER triggers this (holding behind a
# stopped leader is correct), so a queue still waits properly.
_HEADON_STALL_TICKS = 8

# fid -> (lat, lon, kind, heading_deg) registered this tick (phase 1 current,
# updated to the committed position by each phase-2 step()).
_positions: dict[str, tuple[float, float, str, float]] = {}
# fids that have already been ADVANCED (step()) this tick = higher priority. A
# flight keeps the full SEP_M gap from these; from not-yet-advanced flights it
# only respects the HARD_M anti-overlap floor, so the higher-priority flight
# passes as far as it can and the lower-priority one yields on its own turn.
_committed: set[str] = set()
# fid -> consecutive ticks it has been blocked purely by an OPPOSING flight.
# Persists across ticks (NOT cleared in begin_tick) to drive head-on break-through.
_headon_stall: dict[str, int] = {}


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


def _position_at(path, cum, progress):
    """(lat, lon, heading_deg) at `progress` metres along the path — mirrors the
    movers' own interpolation so registries agree with what gets drawn."""
    if len(path) < 2:
        p = path[0] if path else (0.0, 0.0)
        return p[0], p[1], 0.0
    progress = max(0.0, min(progress, cum[-1]))
    for i in range(len(cum) - 1):
        if progress <= cum[i + 1] or i == len(cum) - 2:
            seg = cum[i + 1] - cum[i]
            t = (progress - cum[i]) / seg if seg > 0 else 0.0
            t = max(0.0, min(1.0, t))
            lat = path[i][0] + (path[i + 1][0] - path[i][0]) * t
            lon = path[i][1] + (path[i + 1][1] - path[i][1]) * t
            return lat, lon, _bearing(path[i], path[i + 1])
    return path[-1][0], path[-1][1], 0.0


def begin_tick() -> None:
    """Clear last tick's registrations. Called once by the broadcaster before
    phase 1 (register) and phase 2 (advance)."""
    _positions.clear()
    _committed.clear()


def register_current(fid, path, cum, progress, kind) -> None:
    """PHASE 1: register a taxiing flight's CURRENT position before any flight
    moves this tick, so higher-priority flights advanced first don't drive onto a
    stationary one they're processed before."""
    if len(path) < 2:
        return
    lat, lon, hdg = _position_at(path, cum, progress)
    _positions[fid] = (lat, lon, kind, hdg)


def _forward_offset(lat, lon, hdg, olat, olon):
    """Signed forward distance (m) of point (olat,olon) relative to a flight at
    (lat,lon) heading `hdg`: >0 ahead, <0 behind, ~0 abeam."""
    hr = math.radians(hdg)
    fx, fy = math.sin(hr), math.cos(hr)               # east, north unit fwd
    dnorth = (olat - lat) * 111320.0
    deast = (olon - lon) * 111320.0 * math.cos(math.radians(lat))
    return fx * deast + fy * dnorth


def _clear_at(fid, lat, lon, hdg, ignore_headon=False):
    """Check candidate point (lat,lon,hdg) against every other registered flight.

    Returns (ok, blocked_by_headon):
      * ok               — True if the point keeps required separation.
      * blocked_by_headon — True if the ONLY thing blocking it is an OPPOSING
        (head-on, heading diff > 120°) flight. Same-direction/crossing blocks set
        this False. Drives the anti-deadlock break-through in step().

    Required gap depends on priority:
      * vs a flight ALREADY ADVANCED this tick (higher priority, in `_committed`):
        full SEP_M if it's ahead/abeam, else the HARD_M floor just-behind.
      * vs a NOT-yet-advanced flight (lower priority): only the HARD_M anti-overlap
        floor, and only if it's ahead/abeam.

    `ignore_headon=True` drops opposing-flight constraints entirely (the
    break-through path) so a stalled flight can pass a head-on."""
    only_headon = True
    ok = True
    for other, (olat, olon, _ok, ohdg) in _positions.items():
        if other == fid:
            continue
        fwd = _forward_offset(lat, lon, hdg, olat, olon)
        dist = _haversine_m((lat, lon), (olat, olon))
        opposing = abs((hdg - ohdg + 180) % 360 - 180) > 120
        if opposing and ignore_headon:
            continue  # break-through: don't let a head-on flight block me
        if other in _committed:
            violated = (fwd >= -1.0 and dist < SEP_M) or (-SEP_M <= fwd < -1.0 and dist < HARD_M)
        else:
            violated = fwd >= -1.0 and dist < HARD_M
        if violated:
            ok = False
            if not opposing:
                only_headon = False  # a non-head-on block => don't break through
    return ok, (not ok and only_headon)


def step(fid, path, cum, cur, proposed, kind, enforce=True) -> float:
    """PHASE 2: return the progress `fid` may actually advance to this tick,
    clamped so its swept path stays clear of everything registered so far, then
    re-register the flight at the committed position.

    Sweeps cur -> proposed in ~_SWEEP_M steps and stops at the last sample that is
    still clear (see _clear_at). Never moves backward (>= cur). `enforce=False`
    skips the clamp (an arrival's high-speed landing roll / a departure's takeoff
    roll — the single-occupant runway lock already deconflicts those) but still
    registers the footprint so taxiing traffic near the runway gives way."""
    if len(path) < 2 or not enforce or proposed <= cur:
        committed = proposed
        _headon_stall.pop(fid, None)
    else:
        # If this flight has been stalled purely by a head-on for too many ticks,
        # break through this tick (ignore opposing flights) so a single-lane
        # head-on can never freeze traffic permanently.
        breakthrough = _headon_stall.get(fid, 0) >= _HEADON_STALL_TICKS
        committed = cur
        n = max(1, int((proposed - cur) / _SWEEP_M))
        step_len = (proposed - cur) / n
        stalled_headon = False
        for k in range(1, n + 1):
            cand = cur + step_len * k
            lat, lon, hdg = _position_at(path, cum, cand)
            ok, headon = _clear_at(fid, lat, lon, hdg, ignore_headon=breakthrough)
            if ok:
                committed = cand
            else:
                stalled_headon = headon
                break  # blocked — hold at the last clear sample
        # track consecutive head-on stalls (reset once it actually moves freely)
        if committed <= cur + 1e-6 and stalled_headon and not breakthrough:
            _headon_stall[fid] = _headon_stall.get(fid, 0) + 1
        else:
            _headon_stall.pop(fid, None)
    lat, lon, hdg = _position_at(path, cum, committed)
    _positions[fid] = (lat, lon, kind, hdg)
    _committed.add(fid)  # now higher-priority than any flight not yet advanced
    return committed


def clear(fid: str) -> None:
    """Drop a flight's registration (e.g. when it leaves the ground/roster)."""
    _positions.pop(fid, None)
    _committed.discard(fid)
    _headon_stall.pop(fid, None)

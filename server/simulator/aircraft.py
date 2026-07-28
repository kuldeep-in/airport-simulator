"""Aircraft ground-movement state machine.

States advance based on elapsed sim time relative to each flight's scheduled
arrival / departure. During TAXIING phases we interpolate lat/lon along a
simplified path from the runway to the assigned gate (and back), producing
visibly different positions each tick. Heading is derived from the direction
of travel.

Coordinate layout (must stay consistent with data/hia_*.geojson):
  - Airport centre: lon 51.608, lat 25.273
  - Two parallel N-S runways to the west of the terminal
  - Terminal / gates in the centre-east
"""

import math
from datetime import datetime, timedelta

CENTER_LON = 51.608
CENTER_LAT = 25.273

# Runway threshold points (approx). Runways run roughly N-S (16/34).
RWY_16L_THRESHOLD = (51.590, 25.300)   # north end of west runway
RWY_16L_TOUCHDOWN = (51.590, 25.255)   # touchdown zone (south) for arrivals
RWY_16R_THRESHOLD = (51.600, 25.300)   # inner runway north end
RWY_16R_TOUCHDOWN = (51.600, 25.255)

# Common taxiway node between runways and terminal apron
TWY_ALPHA = (51.6035, 25.273)   # junction west of terminal
APRON_HOLD = (51.6065, 25.273)  # apron entry near terminal

# Phase durations in seconds of sim time
DUR = {
    "ARRIVING": 6 * 60,       # short final -> touchdown
    "TAXIING_IN": 8 * 60,
    "GROUND_SERVICE": 25 * 60,
    "BOARDING": 30 * 60,
    "PUSHBACK": 5 * 60,
    "TAXIING_OUT": 9 * 60,
}

PHASE_SPEED = {  # ground speed knots (for display)
    "ARRIVING": 140,
    "TAXIING_IN": 15,
    "PARKED": 0,
    "GROUND_SERVICE": 0,
    "BOARDING": 0,
    "PUSHBACK": 4,
    "TAXIING_OUT": 15,
    "DEPARTED": 250,
    "SCHEDULED": 0,
}


def _lerp(a, b, t):
    return a + (b - a) * t


def _interp(p0, p1, t):
    return (_lerp(p0[0], p1[0], t), _lerp(p0[1], p1[1], t))


def _bearing(p0, p1):
    """Compass bearing from p0 to p1 in degrees (0=N)."""
    lon0, lat0 = math.radians(p0[0]), math.radians(p0[1])
    lon1, lat1 = math.radians(p1[0]), math.radians(p1[1])
    dlon = lon1 - lon0
    y = math.sin(dlon) * math.cos(lat1)
    x = math.cos(lat0) * math.sin(lat1) - math.sin(lat0) * math.cos(lat1) * math.cos(dlon)
    brg = math.degrees(math.atan2(y, x))
    return int((brg + 360) % 360)


def _path_position(path, t):
    """Position along a multi-segment path, t in [0,1]. Returns (pos, heading)."""
    if len(path) == 1:
        return path[0], 0
    n = len(path) - 1
    scaled = t * n
    seg = min(int(scaled), n - 1)
    local_t = scaled - seg
    p0, p1 = path[seg], path[seg + 1]
    return _interp(p0, p1, local_t), _bearing(p0, p1)


def _taxi_in_path(gate_lonlat):
    return [RWY_16R_TOUCHDOWN, TWY_ALPHA, APRON_HOLD, gate_lonlat]


def _taxi_out_path(gate_lonlat):
    return [gate_lonlat, APRON_HOLD, TWY_ALPHA, RWY_16L_THRESHOLD]


def compute_state(flight, gate_lonlat, sim_time: datetime):
    """Compute the current phase and position for a flight at sim_time.

    Returns dict: {status, phase, lat, lon, heading, ground_speed,
                   actual_arr, actual_dep, gate_status}
    gate_status is one of AVAILABLE/OCCUPIED/BOARDING/CLOSED (or None if no gate).
    """
    sched_arr = flight["scheduled_arr"]
    sched_dep = flight["scheduled_dep"]

    # Timeline (seconds from scheduled arrival):
    #   [-INF, 0)         SCHEDULED / ARRIVING (approach in last DUR window)
    #   [0, tin)          TAXIING_IN
    #   [tin, ...]        PARKED -> GROUND_SERVICE -> BOARDING (until dep)
    #   at dep:           PUSHBACK -> TAXIING_OUT -> DEPARTED
    t_arr = (sim_time - sched_arr).total_seconds()
    t_dep = (sim_time - sched_dep).total_seconds()

    gate = flight.get("assigned_gate")
    result = {
        "status": "SCHEDULED",
        "phase": "SCHEDULED",
        "lat": gate_lonlat[1] if gate_lonlat else CENTER_LAT,
        "lon": gate_lonlat[0] if gate_lonlat else CENTER_LON,
        "heading": 0,
        "ground_speed": 0,
        "actual_arr": None,
        "actual_dep": None,
        "gate_status": "AVAILABLE" if gate else None,
    }

    if gate_lonlat is None:
        return result

    tin = DUR["TAXIING_IN"]

    if t_arr < -DUR["ARRIVING"]:
        # Still en route / scheduled. Park it off-screen conceptually at the gate.
        result["status"] = "SCHEDULED"
        result["phase"] = "AIRBORNE"
        # place at approach corridor far north so it is hidden by the layer
        result["lat"] = RWY_16R_THRESHOLD[1] + 0.05
        result["lon"] = RWY_16R_THRESHOLD[0]
        result["heading"] = 180
        result["ground_speed"] = PHASE_SPEED["ARRIVING"]
        result["gate_status"] = "AVAILABLE"
        return result

    if t_arr < 0:
        # ARRIVING: short final along approach to touchdown
        t = (t_arr + DUR["ARRIVING"]) / DUR["ARRIVING"]  # 0..1
        approach = [(RWY_16R_THRESHOLD[0], RWY_16R_THRESHOLD[1] + 0.03), RWY_16R_TOUCHDOWN]
        pos, hdg = _path_position(approach, max(0.0, min(1.0, t)))
        result.update(status="ARRIVING", phase="ARRIVING",
                      lon=pos[0], lat=pos[1], heading=hdg,
                      ground_speed=PHASE_SPEED["ARRIVING"], gate_status="AVAILABLE")
        return result

    if t_arr < tin:
        # TAXIING_IN
        t = t_arr / tin
        path = _taxi_in_path(gate_lonlat)
        pos, hdg = _path_position(path, min(1.0, t))
        result.update(status="TAXIING_IN", phase="TAXIING_IN",
                      lon=pos[0], lat=pos[1], heading=hdg,
                      ground_speed=PHASE_SPEED["TAXIING_IN"],
                      actual_arr=sched_arr, gate_status="OCCUPIED")
        return result

    # After arrival at gate. Determine phase relative to departure.
    if t_dep < 0:
        # On stand between arrival and departure
        secs_since_park = t_arr - tin
        secs_to_dep = -t_dep
        result["actual_arr"] = sched_arr
        result["lon"], result["lat"] = gate_lonlat
        result["heading"] = 90
        result["ground_speed"] = 0
        if secs_to_dep <= DUR["BOARDING"]:
            result.update(status="BOARDING", phase="BOARDING", gate_status="BOARDING")
        elif secs_since_park <= DUR["GROUND_SERVICE"]:
            result.update(status="GROUND_SERVICE", phase="GROUND_SERVICE", gate_status="OCCUPIED")
        else:
            result.update(status="PARKED", phase="PARKED", gate_status="OCCUPIED")
        return result

    # Departure sequence
    if t_dep < DUR["PUSHBACK"]:
        t = t_dep / DUR["PUSHBACK"]
        push_path = [gate_lonlat, APRON_HOLD]
        pos, hdg = _path_position(push_path, min(1.0, t))
        result.update(status="PUSHBACK", phase="PUSHBACK",
                      lon=pos[0], lat=pos[1], heading=hdg,
                      ground_speed=PHASE_SPEED["PUSHBACK"],
                      actual_arr=sched_arr, gate_status="OCCUPIED")
        return result

    t_out = t_dep - DUR["PUSHBACK"]
    if t_out < DUR["TAXIING_OUT"]:
        t = t_out / DUR["TAXIING_OUT"]
        path = _taxi_out_path(gate_lonlat)
        pos, hdg = _path_position(path, min(1.0, t))
        result.update(status="TAXIING_OUT", phase="TAXIING_OUT",
                      lon=pos[0], lat=pos[1], heading=hdg,
                      ground_speed=PHASE_SPEED["TAXIING_OUT"],
                      actual_arr=sched_arr, actual_dep=sched_dep,
                      gate_status="AVAILABLE")
        return result

    # DEPARTED
    result.update(status="DEPARTED", phase="AIRBORNE",
                  lon=RWY_16L_THRESHOLD[0], lat=RWY_16L_THRESHOLD[1] + 0.05,
                  heading=0, ground_speed=PHASE_SPEED["DEPARTED"],
                  actual_arr=sched_arr, actual_dep=sched_dep,
                  gate_status="AVAILABLE")
    return result

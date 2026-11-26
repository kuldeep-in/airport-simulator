"""Live gate-occupancy registry — the single source of truth for which gate
is occupied by which flight, replacing the old hardcoded "every gate is
AVAILABLE" placeholder in broadcaster.py.

Loaded once at startup from the same two static data files seed_runner.py
and departures.py already use: `data/gateLonLat.json` (61 gates — the real
position/routing source of truth) and `data/hia_gates.geojson` (only 42 of
those 61 carry a `capacity_class`; the other 19 default to NARROW, the most
conservative class, since we have no real data for them).

Both seed_runner.py (arrivals) and departures.py (departures) reserve a gate
here immediately before a flight needs one (bulk-start / schedule-generate
time) and release it once that flight is done with it — an arrival on its
10-minute ground timer, a departure once it starts its taxi-out. Assignment
is random among capacity-compatible free gates; if none of the right class
are free, any available gate is used instead (never blocks/queues).

Every reserve/release also logs to an in-memory occupancy history (per gate,
oldest to newest) — flight_id, occupied_from, occupied_until — feeding the
Gates page (GET /api/gates). `now` is always the sim clock (SIM["sim_time"]),
passed in by the caller, never wall-clock time, so history timestamps stay
consistent with everything else in the sim. History is unbounded in memory
for the process lifetime — fine for a demo session, would need capping/
persistence for a long-running deployment.
"""

import json
import os
import random
import re
from datetime import datetime

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")

with open(os.path.join(_DATA_DIR, "gateLonLat.json")) as _f:
    _GATE_LONLAT: dict[str, list[float]] = json.load(_f)

with open(os.path.join(_DATA_DIR, "hia_gates.geojson")) as _f:
    _CAPACITY_BY_GATE: dict[str, str] = {
        feat["properties"]["id"]: feat["properties"]["capacity_class"]
        for feat in json.load(_f)["features"]
    }

# A gate's capacity class limits which aircraft can widen their search to it,
# not which they're restricted to — e.g. a NARROW-body can also take a WIDE
# or A380 gate if nothing NARROW is free, but not vice versa.
_WIDEN_ORDER = {
    "NARROW": ["NARROW", "WIDE", "A380"],
    "WIDE": ["WIDE", "A380"],
    "A380": ["A380"],
}

_gate_status: dict[str, str] = {}          # gate_id -> "AVAILABLE" | "OCCUPIED"
_gate_capacity: dict[str, str] = {}        # gate_id -> "NARROW" | "WIDE" | "A380"
_occupied_by: dict[str, str] = {}          # gate_id -> flight_id
_occupied_since: dict[str, datetime] = {}  # gate_id -> when the CURRENT occupant started
_history: list[dict] = []                  # completed stays: {gate_id, flight_id, occupied_from, occupied_until} (iso strings)


def gate_heading(gate_id: str | None) -> float | None:
    """The authoritative nose-in parking heading (degrees, 0=N) for a gate,
    stored as the optional 3rd element of its data/gateLonLat.json entry
    ([lon, lat, heading]). None if the gate is unknown or has no heading — the
    caller then falls back to the taxi-path-derived heading. This is what makes
    every parked aircraft face the terminal consistently instead of inheriting
    whatever direction its taxi-in path happened to approach from."""
    v = _GATE_LONLAT.get(gate_id)
    if v and len(v) >= 3 and v[2] is not None:
        return float(v[2])
    return None


def capacity_class_of(aircraft_type: str) -> str:
    """Classify a raw aircraft-type string (A320, A380, B77W, B77L, A35K,
    etc.) into a gate capacity class. Mirrors the frontend's own model-size
    classification (Aircraft3D.jsx's resolveModel()) so a flight's rendered
    size and its gate-eligibility agree."""
    t = (aircraft_type or "").upper()
    if re.search(r"A38[08]", t):
        return "A380"
    if re.search(r"B77|A35[09]|A35K|B78[789]|A33[023]", t):
        return "WIDE"
    return "NARROW"


def load() -> None:
    """(Re)initialize every gate to AVAILABLE. Called once at broadcaster
    startup, before anything can reserve a gate. Also clears occupancy
    history — a fresh process run starts a fresh history log."""
    _gate_status.clear()
    _gate_capacity.clear()
    _occupied_by.clear()
    _occupied_since.clear()
    _history.clear()
    for gate_id in _GATE_LONLAT:
        _gate_status[gate_id] = "AVAILABLE"
        _gate_capacity[gate_id] = _CAPACITY_BY_GATE.get(gate_id, "NARROW")


def reserve_gate(capacity_class: str, flight_id: str, now: datetime,
                 exclude: set[str] | None = None) -> str | None:
    """Randomly pick a free gate for `flight_id`, preferring one matching
    `capacity_class` (widening to a larger class if none match exactly), and
    falling back to ANY free gate regardless of class if the whole pool of
    matching classes is exhausted. Returns None only if literally every gate
    is occupied (61 gates vs. at most ~30 flights in flight at once — should
    not happen in practice, but callers must handle it). `now` (sim time)
    marks the start of this occupancy for history purposes.

    `exclude` is an optional set of gate ids to never assign — used by arrivals to
    skip stands that proved to have no taxiway route (see
    seed_runner._UNROUTABLE_GATES), so a known-dead stand isn't handed out again."""
    skip = exclude or frozenset()
    for cls in _WIDEN_ORDER.get(capacity_class, [capacity_class]):
        candidates = [g for g, c in _gate_capacity.items()
                      if c == cls and _gate_status[g] == "AVAILABLE" and g not in skip]
        if candidates:
            return _occupy(random.choice(candidates), flight_id, now)

    any_available = [g for g, s in _gate_status.items()
                     if s == "AVAILABLE" and g not in skip]
    if any_available:
        return _occupy(random.choice(any_available), flight_id, now)
    return None


def claim_gate(gate_id: str | None, flight_id: str, now: datetime) -> str | None:
    """Occupy a SPECIFIC gate by id — used for the real, API-provided departure
    gate (data/gateLonLat.json). Counterpart to the random reserve_gate().
    Returns the gate_id if it exists and was AVAILABLE (now claimed), else None
    so the caller can fall back to reserve_gate(). `now` (sim time) marks the
    start of this occupancy for history purposes."""
    if not gate_id or gate_id not in _gate_status:
        return None
    if _gate_status[gate_id] != "AVAILABLE":
        return None
    return _occupy(gate_id, flight_id, now)


def _occupy(gate_id: str, flight_id: str, now: datetime) -> str:
    _gate_status[gate_id] = "OCCUPIED"
    _occupied_by[gate_id] = flight_id
    _occupied_since[gate_id] = now
    return gate_id


def release_gate(gate_id: str | None, now: datetime, flight_id: str | None = None) -> None:
    """Free `gate_id`. If `flight_id` is given, the release is OWNER-GUARDED: it's
    a no-op unless that flight is the gate's current occupant. This prevents a
    flight that holds a STALE `assigned_gate` value (e.g. from a prior assignment)
    from evicting a DIFFERENT flight that legitimately occupies the same gate now —
    the cause of two arrivals appearing on one stand. Mirrors runway.release()'s
    owner check. Callers that genuinely want an unconditional free (none today)
    can omit flight_id."""
    if not gate_id or gate_id not in _gate_status:
        return
    if flight_id is not None and _occupied_by.get(gate_id) != flight_id:
        return  # this flight isn't the current occupant — don't evict whoever is
    occupant = _occupied_by.pop(gate_id, None)
    since = _occupied_since.pop(gate_id, None)
    if occupant and since:
        _history.append({
            "gate_id": gate_id,
            "flight_id": occupant,
            "occupied_from": since.isoformat(),
            "occupied_until": now.isoformat(),
        })
    _gate_status[gate_id] = "AVAILABLE"


def snapshot() -> list[dict]:
    """Real occupancy for every gate — published to Lakebase `gate_occupancy` each tick
    by the generator (see gate_state.publish), which is how the APP gets gate state now
    that it runs in a separate process and can't read this registry directly.

    Includes `capacity_class` and `occupied_since` as well as the current occupant,
    because the app's Gates page needs all four and has no other source for them."""
    return [
        {
            "id":             gate_id,
            "capacity_class": _gate_capacity.get(gate_id),
            "status":         status,
            "occupied_by":    _occupied_by.get(gate_id),
            "occupied_since": _occupied_since.get(gate_id),
        }
        for gate_id, status in _gate_status.items()
    ]


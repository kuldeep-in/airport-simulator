"""Single-occupant runway locks — one for departures' takeoff roll, one for
arrivals' landing roll (the two are separate physical runways in this sim's
geometry, ~2 km apart per Doha's real parallel-runway spacing, so each gets
its own independent lock).

DEPARTURE lock (is_clear/enter/release):
Only one flight may be in the RUNWAY phase (threshold -> far end) at a time. A
departure that reaches the holding-short point checks in here before crossing
onto the runway; if another flight holds it, it waits at HOLDING_SHORT and
retries every tick until the runway is free.

ARRIVAL lock (arr_is_clear/arr_enter/arr_release):
Only one arrival may be on the landing-roll segment (the shared fixed
touchdown point -> its capacity-class exit) at a time. A scheduled arrival
whose time has come only starts its roll once this lock is free; otherwise it
holds (its touchdown is delayed) and retries every tick. The lock is released
the moment the active arrival passes its runway exit onto the taxiway. Without
this, many arrivals falling due within seconds of each other all touch down on
the same point and visually pile up (see seed_runner.advance()).
"""

_occupied_by: str | None = None      # departure takeoff-roll lock
_arr_occupied_by: str | None = None  # arrival landing-roll lock


# ── Departures ──────────────────────────────────────────────────────────────
def is_clear() -> bool:
    return _occupied_by is None


def enter(flight_id: str) -> None:
    global _occupied_by
    _occupied_by = flight_id


def release(flight_id: str) -> None:
    """No-op if `flight_id` doesn't actually hold the lock — safe to call
    defensively (e.g. on reschedule, or a second release at finish)."""
    global _occupied_by
    if _occupied_by == flight_id:
        _occupied_by = None


def occupant() -> str | None:
    return _occupied_by


# ── Arrivals ────────────────────────────────────────────────────────────────
def arr_is_clear() -> bool:
    return _arr_occupied_by is None


def arr_enter(flight_id: str) -> None:
    global _arr_occupied_by
    _arr_occupied_by = flight_id


def arr_release(flight_id: str) -> None:
    """No-op if `flight_id` doesn't hold the arrival lock — safe to call
    defensively (on exit, finish, or roster refresh)."""
    global _arr_occupied_by
    if _arr_occupied_by == flight_id:
        _arr_occupied_by = None


def arr_occupant() -> str | None:
    return _arr_occupied_by


# ── West-runway (34L) departures share the ARRIVAL lock ──────────────────────
# When a departure is diverted to the west runway (34L) to relieve 34R
# congestion, it takes off from the SAME physical runway arrivals land on, so it
# must be mutually exclusive with landings. Rather than a third lock, a west
# departure simply holds the arrival lock for its takeoff roll: arr_is_clear()
# gates it against an active landing, arr_enter() claims it, arr_release() frees
# it. (A west departure is only ever assigned when there's a long arrival gap —
# see departures.py — so this contention is rare by design; arrivals still win
# because seed_runner.advance() runs before departures.advance() each tick.)
def west_dep_is_clear() -> bool:
    return arr_is_clear()


def west_dep_enter(flight_id: str) -> None:
    arr_enter(flight_id)


def west_dep_release(flight_id: str) -> None:
    arr_release(flight_id)

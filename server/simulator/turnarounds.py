"""Configured turnarounds — an inbound and an outbound leg flown by the SAME aircraft.

Without this, an arrival and its return departure are two unrelated flights: the
arrival parks for its dwell and vanishes, while the departure pops onto a DIFFERENT
stand 30-45 min before pushback — so a short turnaround shows two planes at once.

`data/turnarounds.json` lists the pairs by hand (e.g. TK780 -> TK781). For a listed
pair, the map shows ONE aircraft:

  1. The arrival lands and parks as normal, but does NOT go GONE when its dwell
     expires while its departure is still waiting — it holds the stand.
  2. At the departure's normal gate-park time (or the moment the arrival parks, if
     the turnaround is shorter than that lead), the departure TAKES OVER the
     arrival's stand: the arrival is retired and the departure appears parked on the
     same gate (same gate heading), then pushes back at its scheduled time.
  3. If the arrival still hasn't parked by the departure's scheduled time (big
     delay / bad data), the pair is unlinked for that day and the departure runs
     independently, exactly as before — nothing ever waits forever.

A pair only links when the departure is scheduled AFTER the arrival and within
`max_gap_min` of it, so a stale instance of the same callsign from another day in
the [now-4h, now+8h] window never pairs up.

The coordination reads the two state machines' module state directly; imports are
lazy so neither seed_runner nor departures has to import the other.
"""
import json
import os
import re
from datetime import datetime

_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "turnarounds.json")

_DEFAULT_MAX_GAP_MIN = 300.0


def _norm(cs) -> str:
    return re.sub(r"\s+", "", str(cs or "")).upper()


def _load() -> tuple[dict[str, str], dict[str, str], float]:
    try:
        with open(_PATH) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return {}, {}, _DEFAULT_MAX_GAP_MIN
    except Exception as e:  # noqa: BLE001 — a bad config must not stop the generator
        print(f"[turnarounds] could not read {_PATH}: {e} — no turnarounds linked")
        return {}, {}, _DEFAULT_MAX_GAP_MIN
    arr_to_dep: dict[str, str] = {}
    dep_to_arr: dict[str, str] = {}
    for p in cfg.get("pairs") or []:
        a, d = _norm(p.get("arrival")), _norm(p.get("departure"))
        if not a or not d:
            continue
        if a in arr_to_dep or d in dep_to_arr:
            print(f"[turnarounds] skipping duplicate pair {a}->{d} (each flight may be linked once)")
            continue
        arr_to_dep[a] = d
        dep_to_arr[d] = a
    gap = float(cfg.get("max_gap_min") or _DEFAULT_MAX_GAP_MIN)
    print(f"[turnarounds] {len(arr_to_dep)} pair(s) loaded, max gap {gap:.0f} min")
    return arr_to_dep, dep_to_arr, gap


_ARR_TO_DEP, _DEP_TO_ARR, _MAX_GAP_SEC = (lambda r: (r[0], r[1], r[2] * 60.0))(_load())
# Pairs dropped for ONE instance (arrival too late), keyed with the departure's
# scheduled time so tomorrow's TK780/TK781 still links.
_unlinked: set[tuple[str, str, datetime]] = set()


def linked_departure(arr_fid: str) -> str | None:
    return _ARR_TO_DEP.get(arr_fid)


def linked_arrival(dep_fid: str) -> str | None:
    return _DEP_TO_ARR.get(dep_fid)


def _active_pair(arr_fid: str, dep_fid: str) -> bool:
    """Both legs are in the current rosters, in the right order, close enough
    together, and the pair hasn't been unlinked for being late."""
    from server.simulator import seed_runner, departures
    ta = seed_runner._scheduled_arr.get(arr_fid)
    td = departures._scheduled_dep.get(dep_fid)
    if ta is None or td is None or (arr_fid, dep_fid, td) in _unlinked:
        return False
    gap = (td - ta).total_seconds()
    return 0.0 < gap <= _MAX_GAP_SEC


def arrival_should_hold(arr_fid: str) -> bool:
    """True while a PARKED arrival must keep its stand (skip GONE) because its
    linked departure hasn't taken the aircraft over yet."""
    dep = linked_departure(arr_fid)
    if not dep:
        return False
    from server.simulator import departures
    if departures._state.get(dep) != "SCHEDULED":
        return False  # departure already taken over / gone — nothing to wait for
    return _active_pair(arr_fid, dep)


def departure_gate_action(dep_fid: str, now: datetime) -> tuple[str, str | None, str | None]:
    """What a SCHEDULED departure should do at its gate-park moment:
      ('normal',   None, None)   — not a (live) turnaround; behave as before
      ('wait',     None, arr)    — its aircraft is still inbound; stay SCHEDULED
      ('takeover', gate, arr)    — its aircraft is parked at `gate`; take it over
    """
    arr = linked_arrival(dep_fid)
    if not arr or not _active_pair(arr, dep_fid):
        return "normal", None, None
    from server.simulator import seed_runner, departures
    st = seed_runner._state.get(arr)
    if st == "PARKED":
        gate = (seed_runner._roster.get(arr) or {}).get("assigned_gate")
        if gate:
            return "takeover", gate, arr
        return "normal", None, None
    if st in ("SCHEDULED", "ARRIVING"):
        sched_dep = departures._scheduled_dep.get(dep_fid)
        if sched_dep is not None and now >= sched_dep:
            # Pushback time reached and the inbound aircraft still isn't on stand —
            # unlink so the departure runs on its own instead of waiting forever.
            _unlinked.add((arr, dep_fid, sched_dep))
            print(f"[turnarounds] {arr} not parked by {dep_fid}'s departure time — "
                  f"unlinked, {dep_fid} runs independently")
            return "normal", None, None
        return "wait", None, arr
    return "normal", None, None   # GONE / unknown — aircraft already left the stand

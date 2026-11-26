"""Focused unit tests for the arrival-runway-lock guards (bug fixed 2026-08-04).

Each test isolates ONE guard so a future regression points at the exact defence that
broke, rather than only failing the big end-to-end repro (adhoc/repro_arrivals.py).

The bug: an unroutable gate (E6) produced a zero-length path, yet the flight was
activated and took the single global arrival-runway lock; it then skipped the
rollout-based release and parked instantly, stranding the lock and starving every
subsequent arrival.

Run:  python3 adhoc/test_arrival_lock.py
"""
import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _mod in ("asyncpg", "requests"):
    if _mod not in sys.modules:
        try:
            __import__(_mod)
        except ImportError:
            sys.modules[_mod] = types.ModuleType(_mod)
if "databricks" not in sys.modules:
    try:
        import databricks  # noqa: F401
    except ImportError:
        _dbx = types.ModuleType("databricks")
        _sdk = types.ModuleType("databricks.sdk")
        _sdk.WorkspaceClient = object
        _dbx.sdk = _sdk
        sys.modules["databricks"] = _dbx
        sys.modules["databricks.sdk"] = _sdk

import server.db as dbmod


class _FakeDB:
    is_demo_mode = True

    async def execute(self, *a, **k):
        return None

    async def executemany(self, *a, **k):
        return None

    async def fetch(self, *a, **k):
        return []


dbmod.db = _FakeDB()

from server.simulator import seed_runner, gates, runway, traffic  # noqa: E402

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
_results = []


def check(name, cond, detail=""):
    _results.append((name, cond, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def _reset():
    """Fresh registry + empty arrival state between tests."""
    gates.load()
    runway.arr_release(runway.arr_occupant() or "")
    traffic.begin_tick()
    for d in (seed_runner._roster, seed_runner._scheduled_arr, seed_runner._started,
              seed_runner._state, seed_runner._progress, seed_runner._park_heading,
              seed_runner._pending_delay, seed_runner._ground_timer,
              seed_runner._gate_arrival_at, seed_runner._gate_pos, seed_runner._path,
              seed_runner._cum_dist, seed_runner._total_dist, seed_runner._rollout_dist):
        d.clear()
    seed_runner._UNROUTABLE_GATES.clear()


def _add(fid, sched, actype="A320"):
    seed_runner._add_arrival({
        "callsign": fid, "aircraft_type": actype, "airline": "QR",
        "origin": "LHR", "scheduled_utc": sched, "api_status": "Expected",
    })


def test_unroutable_gate_is_excluded_not_activated():
    """E6 has no taxiway route. Assigning it must FAIL (return False), hand the gate
    back, memoize it as unroutable, and leave the flight SCHEDULED."""
    print("\ntest_unroutable_gate_is_excluded_not_activated")
    _reset()
    _add("QRTEST", NOW)
    # Force the only free gate to be the unroutable one.
    for g in list(gates._gate_status):
        if g != "E6":
            gates._gate_status[g] = "OCCUPIED"
    ok = seed_runner._assign_gate_and_route("QRTEST", NOW)
    check("routing failure returns False", ok is False, f"got {ok}")
    check("E6 memoized as unroutable", "E6" in seed_runner._UNROUTABLE_GATES,
          str(seed_runner.unroutable_gates()))
    check("gate handed back to the registry", gates._gate_status.get("E6") == "AVAILABLE",
          gates._gate_status.get("E6"))
    check("no assigned_gate left on the flight",
          seed_runner._roster["QRTEST"].get("assigned_gate") is None)
    check("no zero-length path left behind", "QRTEST" not in seed_runner._total_dist)


def test_advance_does_not_strand_lock_on_unroutable_gate():
    """The real failure: advance() must not take the arrival lock when the only free
    gate is unroutable — pre-fix this stranded the lock and emptied the board."""
    print("\ntest_advance_does_not_strand_lock_on_unroutable_gate")
    _reset()
    _add("QRA", NOW)
    for g in list(gates._gate_status):
        if g != "E6":
            gates._gate_status[g] = "OCCUPIED"
    traffic.begin_tick()
    seed_runner.advance(NOW, 1.0, 1)
    check("flight stays SCHEDULED", seed_runner._state["QRA"] == "SCHEDULED",
          seed_runner._state["QRA"])
    check("arrival runway lock NOT taken", runway.arr_occupant() is None,
          str(runway.arr_occupant()))
    check("lock is clear for the next arrival", runway.arr_is_clear() is True)


def test_later_arrival_still_activates_after_a_bad_gate():
    """Once the bad stand is excluded and a good one frees up, a subsequent arrival
    must activate normally — i.e. the failure is transient, not sticky."""
    print("\ntest_later_arrival_still_activates_after_a_bad_gate")
    _reset()
    _add("QRA", NOW)
    for g in list(gates._gate_status):
        if g != "E6":
            gates._gate_status[g] = "OCCUPIED"
    traffic.begin_tick()
    seed_runner.advance(NOW, 1.0, 1)                      # blocked on E6
    for g in list(gates._gate_status):                    # apron frees up
        gates._gate_status[g] = "AVAILABLE"
    traffic.begin_tick()
    seed_runner.advance(NOW + timedelta(seconds=1), 1.0, 1)
    st = seed_runner._state["QRA"]
    check("flight now ARRIVING", st == "ARRIVING", st)
    check("has a real routed path", seed_runner._total_dist.get("QRA", 0.0) > 0.0,
          f"{seed_runner._total_dist.get('QRA', 0.0):.0f} m")
    check("gate assigned is not the unroutable one",
          seed_runner._roster["QRA"].get("assigned_gate") != "E6",
          str(seed_runner._roster["QRA"].get("assigned_gate")))
    check("lock held by this flight while rolling", runway.arr_occupant() == "QRA",
          str(runway.arr_occupant()))


def test_watchdog_frees_a_stranded_lock():
    """Defence in depth: if anything ever leaves the lock held by a flight that
    isn't rolling, the watchdog must free it within one tick."""
    print("\ntest_watchdog_frees_a_stranded_lock")
    _reset()
    _add("GHOST", NOW)
    runway.arr_enter("GHOST")                 # lock held...
    seed_runner._state["GHOST"] = "PARKED"    # ...by a flight that's already parked
    check("precondition: lock held", runway.arr_occupant() == "GHOST")
    seed_runner._watchdog_arrival_lock()
    check("watchdog released the lock", runway.arr_occupant() is None,
          str(runway.arr_occupant()))

    # Also: a holder that vanished from the roster entirely.
    _reset()
    runway.arr_enter("VANISHED")
    seed_runner._watchdog_arrival_lock()
    check("watchdog released a lock held by an unknown flight",
          runway.arr_occupant() is None, str(runway.arr_occupant()))


def test_starvation_scenario_end_to_end():
    """Pre-fix shape of the reported bug: one flight draws the bad gate, then many
    later arrivals must still all get airborne rather than being starved."""
    print("\ntest_starvation_scenario_end_to_end")
    _reset()
    for i in range(12):
        _add(f"QR{100+i}", NOW + timedelta(minutes=3 * i))
    activated = set()
    for tick in range(3 * 60 * 12 + 600):
        now = NOW + timedelta(seconds=tick)
        traffic.begin_tick()
        seed_runner.register_traffic()
        seed_runner.advance(now, 1.0, 1)
        for fid, st in seed_runner._state.items():
            if st != "SCHEDULED":
                activated.add(fid)
    check("every arrival activated", len(activated) == 12, f"{len(activated)}/12")
    stuck = [f for f, s in seed_runner._state.items() if s == "SCHEDULED"]
    check("none left stuck SCHEDULED", not stuck, str(stuck))


async def main():
    test_unroutable_gate_is_excluded_not_activated()
    test_advance_does_not_strand_lock_on_unroutable_gate()
    test_later_arrival_still_activates_after_a_bad_gate()
    test_watchdog_frees_a_stranded_lock()
    test_starvation_scenario_end_to_end()
    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

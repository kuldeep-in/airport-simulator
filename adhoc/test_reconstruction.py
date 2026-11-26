"""B2 reconstruction tests: an arrival whose touchdown is in the PAST is placed at
its computed CURRENT position (mid-taxi / parked / gone) instead of re-flown from
touchdown — the behaviour that lets the ramp survive a restart or airport switch.

Run:  python3 adhoc/test_reconstruction.py
"""
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _m in ("asyncpg", "requests"):
    if _m not in sys.modules:
        try:
            __import__(_m)
        except ImportError:
            sys.modules[_m] = types.ModuleType(_m)
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

NOW = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)
_results = []


def check(name, cond, detail=""):
    _results.append((name, cond, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def _reset():
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
    seed_runner.route_reservations.clear_all()


def _add(fid, sched, actype="A320"):
    seed_runner._add_arrival({
        "callsign": fid, "aircraft_type": actype, "airline": "QR",
        "origin": "LHR", "scheduled_utc": sched, "api_status": "Expected",
    })


def _advance():
    traffic.begin_tick()
    seed_runner.advance(NOW, 1.0, 1)


def test_recent_arrival_reconstructs_parked():
    print("\ntest_recent_arrival_reconstructs_parked (touchdown 20 min ago)")
    _reset()
    _add("QRP", NOW - timedelta(minutes=20))
    _advance()
    st = seed_runner._state.get("QRP")
    check("state is PARKED", st == "PARKED", str(st))
    check("has a gate", bool(seed_runner._roster["QRP"].get("assigned_gate")))
    ga = seed_runner._gate_arrival_at.get("QRP")
    check("gate_arrival set in the past", ga is not None and ga < NOW, str(ga))
    check("runway lock NOT held (past the roll)", runway.arr_is_clear(),
          str(runway.arr_occupant()))
    if ga:
        left = seed_runner._ground_time_sec("QRP") - (NOW - ga).total_seconds()
        check("dwell time still remaining", left > 0, f"{left:.0f}s left")


def test_old_arrival_is_gone_not_placed():
    print("\ntest_old_arrival_is_gone_not_placed (touchdown 55 min ago)")
    _reset()
    _add("QRG", NOW - timedelta(minutes=55))
    _advance()
    st = seed_runner._state.get("QRG")
    check("state is GONE", st == "GONE", str(st))
    check("not started (never placed on the map)", seed_runner._started.get("QRG") is False,
          str(seed_runner._started.get("QRG")))
    check("gate handed back", seed_runner._roster["QRG"].get("assigned_gate") is None)


def test_midtaxi_reconstructs_arriving():
    print("\ntest_midtaxi_reconstructs_arriving (touchdown 3 min ago)")
    _reset()
    _add("QRM", NOW - timedelta(minutes=3))
    _advance()
    st = seed_runner._state.get("QRM")
    check("state is ARRIVING", st == "ARRIVING", str(st))
    prog = seed_runner._progress.get("QRM", 0.0)
    total = seed_runner._total_dist.get("QRM", 0.0)
    check("progress is mid-taxi (0 < p < total)", 0 < prog < total, f"{prog:.0f}/{total:.0f} m")
    check("past rollout → runway lock NOT held", runway.arr_is_clear(),
          str(runway.arr_occupant()))


def test_live_arrival_uses_lock_from_zero():
    print("\ntest_live_arrival_uses_lock_from_zero (touchdown now)")
    _reset()
    _add("QRL", NOW)
    _advance()
    st = seed_runner._state.get("QRL")
    check("state is ARRIVING", st == "ARRIVING", str(st))
    check("progress ~0 (flown live from touchdown)",
          seed_runner._progress.get("QRL", 0.0) < 5.0,
          f"{seed_runner._progress.get('QRL', 0.0):.1f} m")
    check("runway lock held by this flight", runway.arr_occupant() == "QRL",
          str(runway.arr_occupant()))


for t in (test_recent_arrival_reconstructs_parked, test_old_arrival_is_gone_not_placed,
          test_midtaxi_reconstructs_arriving, test_live_arrival_uses_lock_from_zero):
    t()

passed = sum(1 for _, c, _ in _results if c)
print(f"\n{passed}/{len(_results)} checks passed")
sys.exit(0 if passed == len(_results) else 1)

"""Deterministic offline reproduction of the departure taxi-queue freeze.

Loads the saved AeroDataBox sample, builds the departures roster (no API/db),
pins sim_time so a large batch of departures is active at once, and steps
departures.advance() tick-by-tick — logging how many actually move each tick and
which flights are pinned. Pure in-memory; no live app, no database.

Run:  python3 adhoc/repro_jam.py
"""
import sys, json, asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Stub optional third-party deps the sim imports but this harness never exercises
# (no DB, no HTTP), so it runs on a bare interpreter.
import types
for _mod in ("asyncpg", "requests"):
    if _mod not in sys.modules:
        try:
            __import__(_mod)
        except ImportError:
            sys.modules[_mod] = types.ModuleType(_mod)
# databricks.sdk.WorkspaceClient is imported by server.config but never called here.
if "databricks" not in sys.modules:
    try:
        import databricks  # noqa: F401
    except ImportError:
        dbx = types.ModuleType("databricks")
        sdk = types.ModuleType("databricks.sdk")
        sdk.WorkspaceClient = object
        dbx.sdk = sdk
        sys.modules["databricks"] = dbx
        sys.modules["databricks.sdk"] = sdk

# ── Stub the DB so departures/* never touches Lakebase ──────────────────────────
import server.db as dbmod
class _FakeDB:
    is_demo_mode = True
    async def execute(self, *a, **k): return None
    async def get_pool(self): return None
    async def close(self): return None
dbmod.db = _FakeDB()

from server.simulator import departures, live_schedule, gates, traffic, taxiway_graph, runway

SAMPLE = ROOT / "adhoc" / "aerodatabox_DOH_sample.json"


def _haversine_m(p0, p1):
    import math
    lat0, lat1 = math.radians(p0[0]), math.radians(p1[0])
    dlat = lat1 - lat0
    dlon = math.radians(p1[1] - p0[1])
    a = math.sin(dlat/2)**2 + math.cos(lat0)*math.cos(lat1)*math.sin(dlon/2)**2
    return 6_371_000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


async def main():
    payload = json.load(open(SAMPLE))
    rows = live_schedule.parse_departures(payload)
    print(f"parsed departures: {len(rows)}")

    # Load gates (needed for gate assignment / capacity classes).
    gates.load()

    # Seed the departures roster directly (bypass live_schedule.get_departures / db).
    for r in rows:
        departures._add_departure(r)
    print(f"roster loaded: {len(departures._roster)}")

    # Pin sim_time so EVERY departure is already past its scheduled time → they all
    # want to be ACTIVE at once (recreates the mass-activation load that jams).
    scheds = [departures._scheduled_dep[f] for f in departures._roster]
    base = max(scheds) + timedelta(minutes=1)   # after the last one → all due
    print(f"sim_time pinned at {base.isoformat()} (all {len(scheds)} departures due)")

    def counts():
        from collections import Counter
        return dict(Counter(departures._state.values()))

    prev_pos = {}
    dt = 1.0
    STALL_REPORTED = False
    for tick in range(0, 7200):
        sim_time = base + timedelta(seconds=tick * dt)
        traffic.begin_tick()
        departures.register_traffic()
        departures.advance(sim_time, dt, 1)

        # measure movement of ACTIVE flights
        moved = 0
        active = [f for f, s in departures._state.items() if s == "ACTIVE"]
        cur_pos = {}
        for fid in active:
            prog = departures._progress.get(fid, 0.0)
            cur_pos[fid] = prog
            if fid in prev_pos and abs(prog - prev_pos[fid]) > 0.01:
                moved += 1
        prev_pos = cur_pos

        if tick % 60 == 0 or (active and moved == 0 and tick > 5):
            print(f"t={tick:4d}s  states={counts()}  active={len(active)}  moved_this_tick={moved}")

        # Report the freeze picture once, but KEEP RUNNING to see if it drains.
        if len(active) >= 10 and moved == 0 and tick > 30 and not STALL_REPORTED:
            print(f"\n=== near-zero movement at t={tick}s === (continuing to test drainage)")
            _report_freeze(active)
            STALL_REPORTED = True

    from collections import Counter as _C
    print(f"\nfinal @ t={tick}s: states={dict(_C(departures._state.values()))}")
    done = sum(1 for s in departures._state.values() if s in ('DONE','DEPARTED'))
    print(f"departures completed in {tick}s: {done} of {len(departures._roster)}  "
          f"→ ~{done/(tick/60):.1f}/min throughput")


def _report_freeze(active):
    """For each stuck ACTIVE flight, print what's pinning it: queue slot vs
    neighbour separation."""
    import math
    print(f"\n{len(active)} ACTIVE flights frozen. Per-flight pin analysis (first 12):")
    # positions
    pos = {}
    for fid in active:
        prog = departures._progress.get(fid, 0.0)
        lat, lon, hdg = departures._position_at(fid, prog)
        pos[fid] = (lat, lon, hdg, prog)

    for fid in sorted(active, key=lambda f: departures._hold_dist.get(f,0)-departures._progress.get(f,0))[:12]:
        prog = departures._progress.get(fid, 0.0)
        hold = departures._hold_dist.get(fid, 0.0)
        ahead = departures._departures_ahead_in_hold_queue(fid, hold)
        stop_m = max(0.0, hold - ahead * departures._HOLD_QUEUE_GAP_M)
        entered = departures._runway_entered.get(fid, False)
        rwy = departures._runway.get(fid)
        # nearest neighbour
        la, lo, hd, _ = pos[fid]
        nn = min(((_haversine_m((la,lo),(pos[g][0],pos[g][1])), g) for g in active if g != fid),
                 default=(9999, None))
        remaining_to_hold = hold - prog
        pin = ("slot: stop_m<=progress" if stop_m <= prog + 0.5
               else "blocked by traffic.step (neighbour ahead)")
        print(f"  {fid:8s} prog={prog:7.1f} hold={hold:7.1f} rem={remaining_to_hold:7.1f} "
              f"ahead={ahead:2d} stop_m={stop_m:7.1f} entered={entered} rwy={rwy} "
              f"nn={nn[0]:5.1f}m→{nn[1]}  PIN: {pin}")

    print(f"\nrunway locks: dep={runway.occupant()} arr={runway.arr_occupant()}")
    # how many are at hold point vs still short of it
    at_hold = sum(1 for f in active if departures._hold_dist.get(f,0)-departures._progress.get(f,0) < 5)
    print(f"flights within 5m of their hold point: {at_hold} / {len(active)}")


if __name__ == "__main__":
    asyncio.run(main())

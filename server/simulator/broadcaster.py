"""SYSTEM A — the position GENERATOR. Runs as a continuous Databricks Job, not in the app.

This is the write side of a two-system architecture (see positions.py for the contract):

    SYSTEM A  this module                       SYSTEM B  app.py
    ─────────────────────────────               ────────────────────────────
    reads  flight_schedule (the feed)           reads  sim_positions
    picks  flights due now                             gate_occupancy
    assigns gates, routes, runway locks                sim_heartbeat
    computes positions every second              ──▶   broadcasts over WebSocket
    WRITES sim_positions + gate_occupancy              serves the 2D map / 3D scene

The two share ONLY Lakebase tables. Neither imports the other, and the app holds no
simulation state — which is what makes them independently deployable and restartable.

**This loop does not broadcast.** It has no WebSocket clients: its only output is
Lakebase. That's the whole point — when a Kafka/IoT feed eventually replaces this
generator, the app is untouched because it was never talking to this process.

Flight sources (both from AeroDataBox via the ingest job → synced table, auto-activating at their
real scheduled times):
  - seed_runner.py — arrivals: activate at touchdown time IF the arrival runway is
    clear and a capacity-matched gate is free; taxi in; park; released after 30 min.
  - departures.py — departures: appear at the stand 30-45 min before departure, push
    back at the real time, taxi out, hold short, take off.

SINGLE INSTANCE ONLY. Gate and runway state live in Python dicts (gates.py /
runway.py), so two generators would each believe the whole airport is free and both
assign the same stand. Enforced by deployment (one continuous job), not by the schema.
"""

import asyncio
import time
from collections import Counter
from datetime import datetime, timezone

from server.db import db
from server import airports
from server.simulator import gates
from server.simulator import seed_runner
from server.simulator import departures
from server.simulator import traffic
from server.simulator import flight_store
from server.simulator import sim_events
from server.simulator import positions
from server.simulator import gate_state

TICK_WALL_SECONDS = 1

# How often the generator re-reads its roster from `flight_schedule`. The fetcher
# (System C) writes that table on a 4-hour schedule and cannot push into this process,
# so we pull. 60 s is a deliberate choice: cheap (one indexed query), and new flights or
# published delays surface within a minute instead of waiting for a 4-hour boundary —
# which is what the old in-process refresh loop did.
ROSTER_PULL_TICKS = 60

# ── Module-level simulation state ─────────────────────────────────────────────
SIM = {
    "speed": 1,
    "paused": False,
    "sim_time": datetime.now(timezone.utc),
}

# Caches populated on start

_metric_counter = 0
_tick_count = 0     # monotonic tick counter, published in the heartbeat


def get_sim_state():
    return {
        "speed": SIM["speed"],
        "paused": SIM["paused"],
        "sim_time": SIM["sim_time"].isoformat(),
    }


async def _load_world():

    # Load the AeroDataBox window for the current 4-hour slot BEFORE loading
    # rosters. This is CACHE-FIRST: if Lakebase already has this slot's payload
    # (e.g. from an earlier run before a redeploy), it's reused with NO API call —
    # so deploys, tests and validation don't burn API quota. A real fetch only
    # happens when we cross into a new 4h slot with nothing cached. The refresh
    # loop then re-runs at each 00:00/04:00/08:00/… UTC boundary.
    await flight_store.ensure_table()   # persistent flight dataset (source of truth)
    # The generator OWNS the contract tables (it's the writer), so it creates them.
    await positions.ensure_tables()
    await gate_state.ensure_table()
    # Any pre-existing position row belongs to a DEAD run: route state (_path,
    # _progress, Dijkstra results) is in-memory only and cannot be resumed, so those
    # aircraft could never advance again. Clearing here is what stops a crashed run's
    # aircraft from sitting frozen on the app's map.
    await positions.clear_all()
    # NO API CALL HERE. Fetching is System C's job (fetcher.py, scheduled at the 4-hour
    # boundaries); the generator only ever READS what that job stored. So a generator
    # restart costs zero API quota by construction rather than by an env-var switch.
    gates.load()
    await seed_runner.load_gate_positions()
    await departures.load_roster()
    # Load the roster from flight_schedule immediately, then every ROSTER_PULL_TICKS.
    await _pull_roster()
    # Publish the initial all-AVAILABLE gate state so the app has something to read
    # before the first tick completes.
    await gate_state.publish(gates.snapshot())

    print(f"[generator] airport={airports.IATA}, {len(gates.snapshot())} gates, live-flight roster ready.")


async def _pull_roster() -> None:
    """Re-read the flight window from `flight_schedule` and merge it into the state
    machines' rosters.

    This REPLACES the old push model, where the in-process fetch loop called
    seed_runner/departures.refresh_roster directly after each fetch. That only worked
    because the fetch and the simulation shared a process; now the fetcher is a separate
    scheduled job, so the generator pulls.

    Errors are caught: a transient read failure must leave the existing roster intact
    rather than emptying the airport."""
    try:
        arrivals, departures_rows = await flight_store.load_window(SIM["sim_time"], airports.IATA)
        if not arrivals and not departures_rows:
            return  # nothing in the window — keep what we have rather than wiping it
        await seed_runner.refresh_roster(arrivals, SIM["sim_time"])
        await departures.refresh_roster(departures_rows)
    except Exception as e:  # noqa: BLE001
        print(f"[generator] roster pull failed (keeping current roster): {e}")


async def _tick():
    """One generator tick: advance the world, publish positions + gate state.

    NOTE: this no longer broadcasts anything. Since the generator (System A) and the
    app (System B) are separate processes, the generator's only output is Lakebase —
    `sim_positions`, `gate_occupancy`, and a heartbeat. The app reads those and does
    its own broadcasting. See positions.py for the contract."""
    global _metric_counter, _tick_count
    now = SIM["sim_time"]

    # State machines publish their own rows via positions.upsert(); removals delete
    # them. Row presence/absence IS the app's view of what's on the map.
    arr_written, arr_removed = await seed_runner.tick(now)
    dep_written, dep_removed = await departures.tick(now)
    live_count = len(arr_written) + len(dep_written)

    # Gate occupancy has to cross the process boundary too — the app's Gates page and
    # the map's gate markers used to read gates.py's in-memory registry directly.
    await gate_state.publish(gates.snapshot())

    # Persist sim_gate onto flight_schedule rows, enqueued by the sync
    # advance()/tick() gate-assignment paths above.
    await flight_store.flush_sim_gates(airports.IATA)
    # Lifecycle events emitted during advance() → one JSONL file per 15-min window (history).
    await sim_events.flush(now)

    # (zone_metrics is no longer persisted: nothing read it — the app computes zone
    # values itself from compute_zone_metrics, a pure function of time — and sensor
    # history now lives in sensor_readings / Delta.)
    _metric_counter += 1

    # Liveness — lets the app tell "nothing is moving" apart from "the generator is
    # dead", which position rows alone cannot express.
    _tick_count += 1
    await positions.write_heartbeat(_tick_count, live_count,
                                    detail=f"arr={len(arr_written)} dep={len(dep_written)}")

    # PULL the roster from flight_schedule on a short timer. The fetcher (System C, a
    # scheduled job) only writes that table — it can't push into this process's memory
    # the way the old in-process refresh loop did. Pulling every ROSTER_PULL_SECONDS
    # instead of at 4-hour boundaries means a newly-fetched flight (or a published
    # delay) is picked up within a minute.
    if _metric_counter % ROSTER_PULL_TICKS == 0:
        await _pull_roster()

    # Safety net: drop rows the generator stopped updating (e.g. orphaned by a crash
    # in a previous run) so a dead flight can't sit on the app's map forever.
    if _metric_counter % 60 == 0:
        swept = await positions.sweep_stale()
        if swept:
            print(f"[generator] swept {swept} stale position row(s)")


_SUMMARY_EVERY_TICKS = 60  # ~once a minute


def _log_summary(tick_ms: list[float]) -> None:
    """One status line per minute: flights per state machine state + tick timing
    (a tick creeping toward/over 1000 ms means ground motion is lagging real time)."""
    arr = Counter(st for st in seed_runner._state.values() if st not in ("SCHEDULED", "GONE"))
    dep = Counter(st for st in departures._state.values() if st not in ("SCHEDULED", "DEPARTED"))
    fmt = lambda c: ", ".join(f"{k.lower()} {v}" for k, v in sorted(c.items())) or "none"  # noqa: E731
    avg = sum(tick_ms) / len(tick_ms) if tick_ms else 0.0
    print(f"[generator] tick {_tick_count} | arrivals: {fmt(arr)} | departures: {fmt(dep)} "
          f"| gates occupied {sum(1 for g in gates.snapshot() if g.get('status') == 'OCCUPIED')} "
          f"| tick avg {avg:.0f} ms, max {max(tick_ms, default=0.0):.0f} ms")


async def run_loop():
    """The GENERATOR loop (System A) — run by the continuous Databricks Job, NOT by
    the app. Writes to Lakebase only; never broadcasts."""
    try:
        await _load_world()
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"[generator] _load_world FAILED: {e}")
        traceback.print_exc()
        raise
    prev_tick_time = None  # real wall-clock time of the previous advance
    tick_ms: list[float] = []
    while True:
        try:
            await db.maybe_refresh_token()
            if not SIM["paused"]:
                # Track the real UTC wall clock so flights activate at their
                # real AeroDataBox scheduled times (no drift). Speed is locked
                # at 1x.
                SIM["sim_time"] = datetime.now(timezone.utc)
                # Step the sim by the REAL elapsed wall-clock time since the last
                # advance, NOT a fixed TICK_WALL_SECONDS. Under load a tick can
                # take several real seconds (100+ flights each upserting), so a
                # fixed dt=1 makes taxiing/rollout run far slower than real time —
                # planes then linger on the map long past their real gate time
                # (the GONE timer is real-time based, but taxi progress wasn't).
                # Measuring the delta keeps ground motion in step with the clock.
                # First tick (or after a pause/gap) falls back to the nominal tick
                # length and is clamped so a long stall can't teleport a plane.
                if prev_tick_time is None:
                    dt_real = TICK_WALL_SECONDS
                else:
                    dt_real = (SIM["sim_time"] - prev_tick_time).total_seconds()
                    dt_real = max(0.0, min(dt_real, 30.0))  # clamp runaway gaps
                prev_tick_time = SIM["sim_time"]
                t_start = time.monotonic()
                # Ground-traffic edge reservation is a two-phase protocol per tick
                # (see traffic.py): begin_tick() clears the registry, PHASE 1 both
                # streams register their CURRENT occupancy (so head-on resolution
                # is order-independent), then PHASE 2 advances arrivals BEFORE
                # departures so departures give way to arrivals. Order matters.
                traffic.begin_tick()
                seed_runner.register_traffic()      # phase 1
                departures.register_traffic()       # phase 1
                seed_runner.advance(SIM["sim_time"], dt_real, SIM["speed"])   # phase 2
                departures.advance(SIM["sim_time"], dt_real, SIM["speed"])    # phase 2
                await _tick()
                tick_ms.append((time.monotonic() - t_start) * 1000)
                if _tick_count % _SUMMARY_EVERY_TICKS == 0:
                    _log_summary(tick_ms)
                    tick_ms.clear()
            else:
                prev_tick_time = None  # reset so resume doesn't jump by paused time
        except Exception as e:  # noqa: BLE001
            print(f"[generator] tick error: {e}")
        await asyncio.sleep(TICK_WALL_SECONDS)

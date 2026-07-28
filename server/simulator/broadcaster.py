"""Asyncio tick loop: advance the world, persist to Lakebase, broadcast diffs.

Two independent flight sources, both writing into the same live_positions table
so the map renders them uniformly, and both drawing gate assignments from the
same live occupancy registry (gates.py). Both rosters come live from AeroDataBox
(server/simulator/live_schedule.py) and auto-activate at each flight's real
scheduled time:
  - seed_runner.py — arrivals; reserves a random, capacity-matched gate on
    activation and releases it when the flight disappears after parking.
  - departures.py — departures; claims the real API-reported gate on
    activation (random capacity-matched fallback) and releases it once its
    taxi-out finishes.
Gate status shown to clients (Live Gates board, gate markers) reflects this
real registry, not a hardcoded "every gate is AVAILABLE" placeholder.

Sim state (speed / paused / sim_time) lives in module-level vars. sim_time is
set to the real UTC clock every tick so flights activate at their real
AeroDataBox times; each tick we tick both flight sources, recompute zone
metrics, and broadcast only what changed.
"""

import asyncio
from datetime import datetime, timezone

from server.db import db
from server.websocket_manager import manager
from server.simulator.zones import compute_zone_metrics
from server.simulator import seed as seed_mod
from server.simulator import gates
from server.simulator import seed_runner
from server.simulator import departures
from server.simulator import sensor_gen
from server.simulator import live_schedule
from server.simulator import traffic
from server.simulator import flight_store

TICK_WALL_SECONDS = 1

# ── Module-level simulation state ─────────────────────────────────────────────
SIM = {
    "speed": 1,
    "paused": False,
    "sim_time": datetime.now(timezone.utc),
}

# Caches populated on start
_gate_lonlat: dict = {}       # gate_id -> (lon, lat)
_zones: list = []

_metric_counter = 0


def get_sim_state():
    return {
        "speed": SIM["speed"],
        "paused": SIM["paused"],
        "sim_time": SIM["sim_time"].isoformat(),
    }


def set_sim_state(speed=None, paused=None):
    # Speed is locked at 1x — the UI to change it is hidden (see
    # SimControls.jsx's SHOW_SPEED_CONTROLS), and arrival/departure
    # scheduling assumes sim_time tracks real wall-clock time (a sim
    # running faster/slower silently drifts the two apart — see project
    # notes). `speed` is accepted but ignored rather than removed from the
    # API, so this is easy to re-enable later.
    SIM["speed"] = 1
    if paused is not None:
        SIM["paused"] = bool(paused)
    return get_sim_state()


async def _load_world():
    global _gate_lonlat, _zones
    gate_rows = seed_mod.load_gates_geojson()
    _gate_lonlat = {g["id"]: g["lonlat"] for g in gate_rows}
    _zones = seed_mod.load_zones_geojson()

    gates.load()
    # Load the AeroDataBox window for the current 4-hour slot BEFORE loading
    # rosters. This is CACHE-FIRST: if Lakebase already has this slot's payload
    # (e.g. from an earlier run before a redeploy), it's reused with NO API call —
    # so deploys, tests and validation don't burn API quota. A real fetch only
    # happens when we cross into a new 4h slot with nothing cached. The refresh
    # loop then re-runs at each 00:00/04:00/08:00/… UTC boundary.
    await live_schedule.ensure_cache_table()
    await flight_store.ensure_table()   # persistent flight dataset (source of truth)
    await live_schedule.refresh(SIM["sim_time"])
    await seed_runner.load_gate_positions()
    await departures.load_roster()

    print(f"[broadcaster] {len(_gate_lonlat)} gates, live-flight roster ready.")


async def _tick():
    global _metric_counter
    now = SIM["sim_time"]

    # ── Lakebase seed-driven flights ─────────────────────────────────────────
    changed_flights = await seed_runner.tick(now)
    changed_flights += await departures.tick(now)

    # Persist sim_gate onto flight_schedule rows, enqueued by the sync
    # advance()/tick() gate-assignment paths above.
    await flight_store.flush_sim_gates()

    # Zone metrics (recompute every tick; persist every 6 ticks)
    _metric_counter += 1
    zmetrics = compute_zone_metrics(_zones, now)
    zone_payload: dict = {}
    for m in zmetrics:
        zone_payload.setdefault(m["zone_id"], {})[m["metric_type"]] = m["value"]

    if not db.is_demo_mode and _metric_counter % 6 == 0:
        await db.executemany(
            """INSERT INTO zone_metrics (zone_id, metric_type, value, unit, recorded_at)
               VALUES ($1,$2,$3,$4, now())
               ON CONFLICT (zone_id, metric_type, recorded_at) DO NOTHING""",
            [(m["zone_id"], m["metric_type"], m["value"], m["unit"]) for m in zmetrics],
        )
        await db.execute(
            "DELETE FROM zone_metrics WHERE recorded_at < now() - interval '2 hours'"
        )

    # Broadcast diffs
    if changed_flights:
        await manager.broadcast({"type": "aircraft_update", "flights": changed_flights})
    await manager.broadcast({"type": "zone_metric", "zones": zone_payload})
    await manager.broadcast({"type": "sim_tick", **get_sim_state()})


async def build_full_state() -> dict:
    """Full snapshot for new WebSocket clients and /api/state."""
    now = SIM["sim_time"]

    flights_out = list(await seed_runner.full_state()) + list(await departures.full_state())
    positions_out = {
        f["id"]: {
            "flight_id":    f["id"],
            "lat":          f["lat"],
            "lon":          f["lon"],
            "heading":      f["heading"],
            "ground_speed": f["ground_speed"],
            "phase":        f["phase"],
        }
        for f in flights_out
    }

    gates_out = gates.snapshot()

    zmetrics = compute_zone_metrics(_zones, now)
    zone_payload: dict = {}
    for m in zmetrics:
        zone_payload.setdefault(m["zone_id"], {})[m["metric_type"]] = m["value"]

    return {
        "type":      "full_state",
        "flights":   flights_out,
        "gates":     gates_out,
        "positions": positions_out,
        "zones":     zone_payload,
        "sensors":   await sensor_gen.get_current(),
        **get_sim_state(),
    }


async def run_loop():
    """Background task started on FastAPI startup."""
    try:
        await _load_world()
    except Exception as e:  # noqa: BLE001
        import traceback
        print(f"[broadcaster] _load_world FAILED: {e}")
        traceback.print_exc()
        raise
    prev_tick_time = None  # real wall-clock time of the previous advance
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
            else:
                prev_tick_time = None  # reset so resume doesn't jump by paused time
        except Exception as e:  # noqa: BLE001
            print(f"[broadcaster] tick error: {e}")
        await asyncio.sleep(TICK_WALL_SECONDS)

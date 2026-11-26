"""The GENERATOR → APP contract: live aircraft positions in Lakebase.

This module owns `sim_positions` — the single table that couples the two now-
independent systems, and the only thing they share:

    SYSTEM A (generator, a continuous Databricks Job)
        reads flight_schedule → picks flights → assigns gates → computes positions
        └── WRITES sim_positions ──┐
                                   │   Lakebase
    SYSTEM B (app, a Databricks App)
        └── READS sim_positions ───┘ → WebSocket → 2D map / 3D scene

Neither imports the other. The app holds NO simulation state: everything it renders
comes out of this table. That's what makes the systems independent — and it's the
same seam a Kafka/IoT feed replaces later, by becoming the writer instead of System A.

WHY A NEW TABLE (not the old `live_positions`)
----------------------------------------------
`live_positions` was created out-of-band, so `airport_app` does NOT own it and any
`ALTER TABLE` fails with "must be owner" — which is why `aircraft_reg` could never be
added to it. `sim_positions` is created BY the app user, so it's owned, alterable, and
free to carry the full contract. The legacy table is left alone.

WHY THE KEY IS `flight_id`, NOT `aircraft_reg`
---------------------------------------------
A device reports "where is this AIRFRAME"; the map renders "where is this FLIGHT LEG".
They differ during a turnaround: the same tail is on an inbound AND an outbound flight
at once (measured: A7-ANA on QR859 arriving + QR858 departing), which a tail-keyed
upsert would collapse into one row that the two flights then fight over every tick.
Tails are also present on only 3-12% of departures in the feed. So `aircraft_reg` is a
nullable attribute; when real telemetry arrives the re-key belongs on a separate
airframe-keyed telemetry table, with the flight leg resolved by join.

STALENESS
---------
Because the systems are independent, the app must not silently render a dead
generator's last positions forever. System A writes a heartbeat every tick;
`feed_status()` reports its age so the app can surface "feed stale" instead of showing
frozen aircraft as if they were live.
"""

import json
from datetime import datetime, timezone

from server import airports
from server.db import db
from server.simulator import sim_schema

_TABLE = "sim_positions"
_HEARTBEAT = "sim_heartbeat"

# A flight's position row is considered stale — and the whole feed suspect — if the
# generator hasn't written a heartbeat within this many seconds. The generator ticks
# once a second, so this tolerates ~15 missed ticks before the UI is told.
FEED_STALE_AFTER_SEC = 15.0

_COLUMNS = ("flight_id, callsign, aircraft_type, airline, origin, destination, "
            "direction, assigned_gate, aircraft_reg, lat, lon, heading_deg, phase, "
            "speed_kt, scheduled_utc, gate_arrival, runway, api_status, airport")

# Airport tag stamped onto every position row (the app filters on it).
_write_airport = airports.IATA


# ── Schema (created by the generator at startup; the app only reads) ─────────────
async def ensure_tables() -> None:
    """Create the contract tables. Owned by `airport_app` because the app user
    creates them — so unlike the legacy `live_positions`, these can be ALTERed later
    without an ownership problem."""
    if db.is_demo_mode:
        return
    await db.execute(
        f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
              flight_id     text PRIMARY KEY,
              callsign      text,
              aircraft_type text,
              airline       text,
              origin        text,
              destination   text,
              direction     text,               -- 'arrival' | 'departure'
              assigned_gate text,
              aircraft_reg  text,               -- tail / device identity (nullable, never a key)
              lat           double precision,
              lon           double precision,
              heading_deg   double precision,
              phase         text,
              speed_kt      double precision,
              scheduled_utc timestamptz,        -- touchdown (arr) or pushback (dep) time
              gate_arrival  timestamptz,        -- arrivals: real time it reached the stand
              runway        text,               -- departures: assigned runway
              api_status    text,               -- real-world status from the feed
              updated_at    timestamptz NOT NULL DEFAULT now()
            )"""
    )
    # Airport tag (multi-airport). Nullable so rows written by an older generator
    # (pre-this-column) stay valid — the app treats NULL as "show regardless".
    await db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS airport text")
    # Departure stage timeline (actual + projected pushback/taxi/queue/hold/runway/
    # airborne times) for the Dashboard's departure-queue waterfall. NULL for arrivals.
    await db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS timeline jsonb")
    await db.execute(
        f"""CREATE TABLE IF NOT EXISTS {_HEARTBEAT} (
              airport      text PRIMARY KEY,   -- one liveness row per airport's generator
              updated_at   timestamptz NOT NULL,
              tick_count   bigint,
              live_flights int,
              detail       text
            )"""
    )
    # Pre-multi-airport heartbeat was a single `id = 1` row; re-key it by airport.
    await sim_schema.ensure_airport_key(_HEARTBEAT, [], drop_cols=("id",))


# ── Writes (SYSTEM A only) ──────────────────────────────────────────────────────
async def upsert(flight_id: str, callsign: str, aircraft_type: str, airline: str,
                 origin: str, destination: str, direction: str, assigned_gate,
                 aircraft_reg, lat: float, lon: float, heading: float, phase: str,
                 speed_kt: float, scheduled_utc, gate_arrival, runway,
                 api_status, timeline: dict | None = None) -> str:
    """Write one flight's current position + the descriptive fields the app needs.

    Deliberately carries MORE than raw telemetry (scheduled time, gate_arrival,
    runway, api_status): those used to be read out of the state machines' memory by
    the app, so persisting them here is precisely what lets the app hold no state.

    Keyed on `flight_id` — one row per flight leg, existing exactly while the flight
    is on the map. `timeline` is the departure stage timeline (see
    departures.timelines); arrivals leave it NULL. Returns the flight_id."""
    if db.is_demo_mode:
        return flight_id
    await db.execute(
        f"""INSERT INTO {_TABLE}
              (flight_id, callsign, aircraft_type, airline, origin, destination,
               direction, assigned_gate, aircraft_reg, lat, lon, heading_deg, phase,
               speed_kt, scheduled_utc, gate_arrival, runway, api_status, airport,
               timeline, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
                    $20::jsonb, now())
            ON CONFLICT (flight_id) DO UPDATE SET
              assigned_gate=EXCLUDED.assigned_gate,
              aircraft_reg=COALESCE(EXCLUDED.aircraft_reg, {_TABLE}.aircraft_reg),
              lat=EXCLUDED.lat, lon=EXCLUDED.lon,
              heading_deg=EXCLUDED.heading_deg, phase=EXCLUDED.phase,
              speed_kt=EXCLUDED.speed_kt,
              scheduled_utc=EXCLUDED.scheduled_utc,
              gate_arrival=EXCLUDED.gate_arrival,
              runway=EXCLUDED.runway,
              api_status=EXCLUDED.api_status,
              airport=EXCLUDED.airport,
              timeline=EXCLUDED.timeline,
              updated_at=now()""",
        flight_id, callsign, aircraft_type, airline, origin, destination, direction,
        assigned_gate, aircraft_reg, lat, lon, heading, phase, speed_kt,
        scheduled_utc, gate_arrival, runway, api_status, _write_airport,
        json.dumps(timeline) if timeline is not None else None,
    )
    return flight_id


async def remove(flight_id: str) -> None:
    """Delete a flight's row — it has left the map (arrival GONE / departure airborne).
    Row absence is the app's signal to drop the aircraft."""
    if db.is_demo_mode:
        return
    await db.execute(f"DELETE FROM {_TABLE} WHERE flight_id=$1", flight_id)


async def remove_many(flight_ids: list[str]) -> None:
    """Bulk delete — used at generator startup to clear rows orphaned by a previous
    run (a crash mid-taxi would otherwise leave aircraft frozen on the app's map)."""
    if db.is_demo_mode or not flight_ids:
        return
    await db.execute(f"DELETE FROM {_TABLE} WHERE flight_id = ANY($1)", flight_ids)


async def clear_all() -> None:
    """Wipe the table. The generator does this at startup: its in-memory route state
    (`_path`, `_progress`, Dijkstra results) is NOT persisted, so any pre-existing row
    belongs to a dead run and can never be advanced again."""
    if db.is_demo_mode:
        return
    await db.execute(f"DELETE FROM {_TABLE}")


async def sweep_stale(max_age_sec: float = 300.0) -> int:
    """Delete rows the generator has stopped updating (older than `max_age_sec`).

    Safety net for a flight that somehow escapes its normal removal path — without
    it, one orphaned row would sit on the map forever. Returns the number deleted."""
    if db.is_demo_mode:
        return 0
    n = await db.fetchval(
        f"""WITH gone AS (
              DELETE FROM {_TABLE}
               WHERE updated_at < now() - ($1 || ' seconds')::interval
               RETURNING 1)
            SELECT count(*) FROM gone""",
        str(int(max_age_sec)),
    )
    return int(n or 0)


async def write_heartbeat(tick_count: int, live_flights: int, detail: str = "") -> None:
    """Publish liveness. The app uses this to distinguish "no aircraft are moving"
    from "the generator is dead" — indistinguishable from position rows alone."""
    if db.is_demo_mode:
        return
    await db.execute(
        f"""INSERT INTO {_HEARTBEAT} (airport, updated_at, tick_count, live_flights, detail)
            VALUES ($4, now(), $1, $2, $3)
            ON CONFLICT (airport) DO UPDATE SET
              updated_at=now(), tick_count=EXCLUDED.tick_count,
              live_flights=EXCLUDED.live_flights, detail=EXCLUDED.detail""",
        tick_count, live_flights, detail, _write_airport,
    )


# ── Reads (SYSTEM B / diagnostics) ──────────────────────────────────────────────
async def fetch_all() -> list:
    """Every live position row — i.e. the current world, since a row exists exactly
    while a flight is on the map. This one query is the app's entire view of the
    simulation. Empty in demo mode (the app still boots; the map is simply empty)."""
    if db.is_demo_mode:
        return []
    return await db.fetch(f"SELECT {_COLUMNS} FROM {_TABLE} ORDER BY flight_id")


async def fetch_for_airport(airport: str) -> list:
    """Positions for the active airport only, for the app's map/board. Every row is
    tagged with its airport by the generator (upsert stamps it; startup clears rows from
    a previous run), so this is a plain filter — polled every second by the reader."""
    if db.is_demo_mode:
        return []
    if not airport:
        return await fetch_all()
    return await db.fetch(
        f"SELECT {_COLUMNS} FROM {_TABLE} WHERE airport = $1 ORDER BY flight_id", airport)


async def fetch_departure_timelines(airport: str) -> dict[str, dict]:
    """{flight_id: timeline} for the active airport's live departures, for the
    Dashboard's departure-queue waterfall. Kept OUT of _COLUMNS (the map's read
    path) and failure-tolerant: until the generator restarts and adds the
    `timeline` column, this returns {} instead of breaking the map."""
    if db.is_demo_mode:
        return {}
    try:
        recs = await db.fetch(
            f"""SELECT flight_id, timeline FROM {_TABLE}
                 WHERE direction = 'departure' AND timeline IS NOT NULL
                   AND (airport = $1 OR airport IS NULL)""",
            airport)
    except Exception:  # noqa: BLE001 — column not there yet (older generator)
        return {}
    out = {}
    for r in recs:
        tl = r["timeline"]
        out[r["flight_id"]] = json.loads(tl) if isinstance(tl, str) else tl
    return out


async def feed_status() -> dict:
    """Generator liveness for the app + /api/live-debug:
      { alive, age_seconds, tick_count, live_flights, updated_at, detail }
    `alive` is False when there's no heartbeat at all or it's older than
    FEED_STALE_AFTER_SEC — the app surfaces that rather than rendering stale
    positions as if they were live."""
    if db.is_demo_mode:
        return {"alive": False, "age_seconds": None, "demo_mode": True}
    try:
        row = await db.fetchrow(
            f"SELECT updated_at, tick_count, live_flights, detail FROM {_HEARTBEAT} WHERE airport=$1",
            _write_airport,
        )
    except Exception as e:  # noqa: BLE001
        return {"alive": False, "age_seconds": None, "error": f"{type(e).__name__}: {e}"}
    if not row:
        return {"alive": False, "age_seconds": None, "detail": "no heartbeat yet"}
    age = (datetime.now(timezone.utc) - row["updated_at"]).total_seconds()
    return {
        "alive": age <= FEED_STALE_AFTER_SEC,
        "age_seconds": round(age, 1),
        "tick_count": row["tick_count"],
        "live_flights": row["live_flights"],
        "updated_at": row["updated_at"].isoformat(),
        "detail": row["detail"],
    }


def to_payload(rec) -> dict:
    """One `sim_positions` record → the flight payload the frontend already consumes.

    Field NAMES are the established websocket contract (`id`, `heading`,
    `ground_speed`, …) rather than the column names, so neither the store nor the 2D
    map needed changing when the systems were split. Numerics are cast to float —
    asyncpg returns some numeric types as Decimal, which isn't JSON-serialisable."""
    out = {
        "id":            rec["flight_id"],
        "callsign":      rec["callsign"],
        "aircraft_type": rec["aircraft_type"],
        "airline":       rec["airline"],
        "origin":        rec["origin"],
        "destination":   rec["destination"],
        "assigned_gate": rec["assigned_gate"],
        "aircraft_reg":  rec["aircraft_reg"],
        "pax_count":     0,
        "status":        "ACTIVE",
        "phase":         rec["phase"],
        "lat":           float(rec["lat"]),
        "lon":           float(rec["lon"]),
        "heading":       float(rec["heading_deg"]),
        "ground_speed":  float(rec["speed_kt"]),
        "source":        "lakebase",
        "started":       True,
        "api_status":    rec["api_status"],
    }
    sched = rec["scheduled_utc"]
    if sched:
        # The frontend keys arrivals off scheduled_arr and departures off
        # scheduled_dep, so split the single stored column by direction.
        key = "scheduled_arr" if rec["direction"] == "arrival" else "scheduled_dep"
        out[key] = sched.isoformat()
    if rec["gate_arrival"]:
        out["gate_arrival"] = rec["gate_arrival"].isoformat()
    if rec["runway"]:
        out["runway"] = rec["runway"]
    return out

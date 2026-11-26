"""Live gate occupancy in Lakebase — the second half of the generator → app contract.

`positions.py` carries the aircraft; this carries the stands. Both exist for the same
reason: the app must hold NO simulation state, so anything it displays has to be in a
table.

    SYSTEM A (generator)  gates.py in-memory registry ──publish()──▶ gate_occupancy
    SYSTEM B (app)        GET /api/gates ◀──fetch_overview()──────── gate_occupancy

Why the registry itself stays in memory on the generator side: gate reservation happens
inside the synchronous `advance()` call chain (which cannot `await`), and it's contended
per-tick state — exactly what a single writer should own privately. So `gates.py` keeps
being the authority; this module just PUBLISHES a snapshot of it each tick so the app
can read occupancy without importing the simulator.

That asymmetry is deliberate and worth understanding:
  - GATE occupancy crosses the boundary (the app's Gates page shows it) → persisted here.
  - RUNWAY locks do NOT cross it (internal to the picker) → stay in `runway.py` memory.

CONSEQUENCE: the generator MUST be single-instance. Two generators would each hold their
own `_gate_status` dict and both hand out the same stand. Enforced by deployment (one
continuous job), not by this table — a real multi-writer design would need the
reservation itself to be an atomic DB operation.
"""

from server import airports
from server.db import db
from server.simulator import sim_schema

_TABLE = "gate_occupancy"
_AIRPORT = airports.IATA   # this process's airport — every row is keyed by it


async def ensure_table() -> None:
    """Create the occupancy table (app-owned, so alterable later), keyed
    (airport, gate_id). Called by the generator and the app at startup."""
    if db.is_demo_mode:
        return
    await db.execute(
        f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
              airport        text NOT NULL,
              gate_id        text NOT NULL,
              capacity_class text,
              status         text,          -- 'AVAILABLE' | 'OCCUPIED'
              occupied_by    text,          -- flight_id, or NULL
              occupied_since timestamptz,
              updated_at     timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (airport, gate_id)
            )"""
    )
    await sim_schema.ensure_airport_key(_TABLE, ["gate_id"])


async def publish(snapshot_rows: list[dict]) -> None:
    """Upsert the generator's whole gate registry (61 rows).

    Called every tick. One `executemany` of 61 small upserts is cheap next to the
    position writes, and publishing the FULL set — rather than just what changed —
    means a gate freed by a crashed run can't stay stuck OCCUPIED in the app's view.

    Expects `gates.snapshot()` shape: {id, capacity_class, status, occupied_by,
    occupied_since}."""
    if db.is_demo_mode or not snapshot_rows:
        return
    await db.executemany(
        f"""INSERT INTO {_TABLE}
              (airport, gate_id, capacity_class, status, occupied_by, occupied_since, updated_at)
            VALUES ($6,$1,$2,$3,$4,$5, now())
            ON CONFLICT (airport, gate_id) DO UPDATE SET
              capacity_class=EXCLUDED.capacity_class,
              status=EXCLUDED.status,
              occupied_by=EXCLUDED.occupied_by,
              occupied_since=EXCLUDED.occupied_since,
              updated_at=now()""",
        [(r["id"], r.get("capacity_class"), r.get("status"),
          r.get("occupied_by"), r.get("occupied_since"), _AIRPORT) for r in snapshot_rows],
    )


async def fetch_snapshot() -> list[dict]:
    """Current occupancy for every gate — the app's replacement for
    `gates.snapshot()`. Shape matches it exactly so the websocket payload and the
    map's gate markers are unchanged. Empty in demo mode."""
    if db.is_demo_mode:
        return []
    recs = await db.fetch(
        f"""SELECT gate_id, capacity_class, status, occupied_by, occupied_since
              FROM {_TABLE} WHERE airport = $1 ORDER BY gate_id""", _AIRPORT
    )
    return [
        {
            "id":             r["gate_id"],
            "capacity_class": r["capacity_class"],
            "status":         r["status"],
            "occupied_by":    r["occupied_by"],
            "occupied_since": r["occupied_since"].isoformat() if r["occupied_since"] else None,
        }
        for r in recs
    ]

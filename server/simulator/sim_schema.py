"""Multi-airport keys for the generator → app contract tables.

Every live table the generator writes is keyed by `airport` first, so one Lakebase
database serves N airports with ONE table per data type (one generator per airport,
each writing only its own rows; the app reads WHERE airport = its airport):

    gate_occupancy   PK (airport, gate_id)      gate ids like "A1" repeat across airports
    sensor_current   PK (airport, sensor_id)    sensor ids S01… repeat across airports
    sim_heartbeat    PK (airport)               one liveness row per generator
    sim_positions    PK flight_id + airport col (flight ids are already globally unique)

`ensure_airport_key()` migrates a pre-multi-airport table in place (rows backfilled to
DOH — the only airport that existed). Idempotent, so the app and the generator can both
call it at startup; whichever runs first does the work.
"""
from server.db import db

_DEFAULT_AIRPORT = "DOH"   # every row written before the migration was DOH


async def _pk_columns(table: str) -> list[str]:
    rows = await db.fetch(
        """SELECT a.attname
             FROM pg_index i
             JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = $1::regclass AND i.indisprimary
            ORDER BY array_position(i.indkey, a.attnum)""",
        f"public.{table}",
    )
    return [r["attname"] for r in rows]


async def ensure_airport_key(table: str, key_cols: list[str], drop_cols: tuple = ()) -> None:
    """Make `table`'s primary key (airport, *key_cols). No-op if it already is."""
    want = ["airport", *key_cols]
    if await _pk_columns(table) == want:
        return
    await db.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS airport text")
    await db.execute(f"UPDATE {table} SET airport = $1 WHERE airport IS NULL", _DEFAULT_AIRPORT)
    await db.execute(f"ALTER TABLE {table} ALTER COLUMN airport SET NOT NULL")
    await db.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey")
    await db.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({', '.join(want)})")
    for col in drop_cols:
        await db.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")
    got = await _pk_columns(table)
    if got != want:
        raise RuntimeError(f"[sim_schema] {table} PK is {got}, expected {want}")
    print(f"[sim_schema] migrated {table} primary key → ({', '.join(want)})")

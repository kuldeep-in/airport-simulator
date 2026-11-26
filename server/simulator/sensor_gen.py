"""Synthetic terminal sensors (temperature + passenger density) — every 5 seconds.

Runs as its own task inside the generator job (split out of the flight tick, no extra
compute). Every reading of every sensor is kept:

    SYSTEM A (generator)   generate_and_store() ─┬─▶ Lakebase sensor_current   latest per sensor (live tiles/heatmap)
                                                  ├─▶ Lakebase sensor_readings  rolling 24 h log (live per-sensor charts)
                                                  └─▶ volume raw_telemetry/sensors, 1 JSONL file / 15 min
                                                        └─▶ SDP → Delta sensor_readings_<iata>  (full history)
    SYSTEM B (app)         get_current() / get_readings() ◀── Lakebase

Rows are keyed by airport (multi-airport). 26 fixed sensors: 17 on the outdoor
tarmac/apron (warmer, tracks time of day) and 9 inside the terminal (AC-cooled). Each
reading is noise around a per-sensor baseline, so values look plausible, not uniform.

NOTE: this module never broadcasts — the generator has no WebSocket clients. The app's
reader loop polls sensor_current and pushes `sensor_update` (see server/reader.py).
"""
import asyncio, math, random
from datetime import datetime, timedelta, timezone

from server import airports
from server.db import db
from server.simulator import sim_schema
from server.simulator.telemetry_files import FILE_MINUTES, TelemetryFileSink

INTERVAL_SEC = 5.0                 # one reading per sensor every 5 s
READINGS_RETENTION_HOURS = 24      # Lakebase keeps a rolling day; Delta keeps everything
_PRUNE_EVERY = 60                  # iterations between prunes (≈ 5 min)
_AIRPORT = airports.IATA
_sink = TelemetryFileSink("sensors")

SENSORS = [
    # ── Outdoor tarmac / runway approach (hot — 33-38°C) ───────────────────
    {'id': 'S01', 'lon': 51.609495, 'lat': 25.260010, 'indoor': False},
    {'id': 'S02', 'lon': 51.609988, 'lat': 25.260290, 'indoor': False},
    {'id': 'S03', 'lon': 51.610580, 'lat': 25.260635, 'indoor': False},
    {'id': 'S04', 'lon': 51.610792, 'lat': 25.260341, 'indoor': False},
    {'id': 'S05', 'lon': 51.611511, 'lat': 25.260915, 'indoor': False},
    {'id': 'S06', 'lon': 51.612356, 'lat': 25.261756, 'indoor': False},
    {'id': 'S07', 'lon': 51.612723, 'lat': 25.261119, 'indoor': False},
    {'id': 'S08', 'lon': 51.613343, 'lat': 25.261693, 'indoor': False},
    {'id': 'S09', 'lon': 51.614020, 'lat': 25.261858, 'indoor': False},
    {'id': 'S10', 'lon': 51.613527, 'lat': 25.261081, 'indoor': False},
    {'id': 'S11', 'lon': 51.615219, 'lat': 25.262279, 'indoor': False},
    {'id': 'S12', 'lon': 51.615995, 'lat': 25.262534, 'indoor': False},
    {'id': 'S13', 'lon': 51.616826, 'lat': 25.262814, 'indoor': False},
    {'id': 'S14', 'lon': 51.616403, 'lat': 25.262381, 'indoor': False},
    {'id': 'S15', 'lon': 51.617447, 'lat': 25.262955, 'indoor': False},
    # ── Terminal / indoor (AC-cooled — 20-23°C) ────────────────────────────
    {'id': 'S16', 'lon': 51.605110, 'lat': 25.268573, 'indoor': True},
    {'id': 'S17', 'lon': 51.606139, 'lat': 25.268896, 'indoor': True},
    {'id': 'S18', 'lon': 51.609382, 'lat': 25.268896, 'indoor': True},
    {'id': 'S19', 'lon': 51.610657, 'lat': 25.269321, 'indoor': True},
    {'id': 'S20', 'lon': 51.611752, 'lat': 25.270251, 'indoor': True},
    {'id': 'S21', 'lon': 51.610455, 'lat': 25.268329, 'indoor': True},
    {'id': 'S22', 'lon': 51.610724, 'lat': 25.267700, 'indoor': True},
    {'id': 'S23', 'lon': 51.610120, 'lat': 25.267700, 'indoor': True},
    {'id': 'S24', 'lon': 51.611305, 'lat': 25.267963, 'indoor': True},
    {'id': 'S25', 'lon': 51.612602, 'lat': 25.263635, 'indoor': False},  # apron approach
    {'id': 'S26', 'lon': 51.611372, 'lat': 25.266123, 'indoor': False},  # gate apron
]

_INDOOR = {s['id']: s['indoor'] for s in SENSORS}

_rng0 = random.Random(42)
# Outdoor: 33-38°C base; indoor (AC): 20-23°C base
_BASE_T = {
    s['id']: (19.0 + _rng0.uniform(0.0, 3.0) if s['indoor']   # AC: 19-22°C
              else 22.0 + _rng0.uniform(0.0, 4.0))             # outdoor: 22-26°C base
    for s in SENSORS
}
_BASE_PAX = {
    s['id']: (1.0 + _rng0.uniform(0.0, 1.2) if s['indoor']   # terminal: 1.0-2.2
              else 0.2 + _rng0.uniform(0.0, 0.8))             # tarmac:   0.2-1.0
    for s in SENSORS
}

async def _ensure_table():
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sensor_current (
            airport     TEXT NOT NULL,
            sensor_id   TEXT NOT NULL,
            lon         DOUBLE PRECISION NOT NULL,
            lat         DOUBLE PRECISION NOT NULL,
            temperature DOUBLE PRECISION,
            pax_density DOUBLE PRECISION,
            updated_at  TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (airport, sensor_id)
        )
    """)
    await sim_schema.ensure_airport_key("sensor_current", ["sensor_id"])
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sensor_readings (
            airport     TEXT NOT NULL,
            sensor_id   TEXT NOT NULL,
            ts          TIMESTAMPTZ NOT NULL,
            temperature DOUBLE PRECISION,
            pax_density DOUBLE PRECISION,
            indoor      BOOLEAN,
            PRIMARY KEY (airport, sensor_id, ts)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS sensor_readings_ts_idx ON sensor_readings (airport, ts)")
    await db.executemany(
        """INSERT INTO sensor_current (airport, sensor_id, lon, lat, temperature, pax_density)
           VALUES ($6,$1,$2,$3,$4,$5) ON CONFLICT (airport, sensor_id) DO NOTHING""",
        [(s['id'], s['lon'], s['lat'], _BASE_T[s['id']], _BASE_PAX[s['id']], _AIRPORT) for s in SENSORS],
    )


def ensure_tables():
    """Public alias so the app can create/migrate the sensor tables at startup."""
    return _ensure_table()


async def generate_and_store() -> list[dict]:
    """Generate one reading per sensor and write it everywhere it's needed.
    Write-side only — SYSTEM A (the generator job) calls this."""
    rng = random.Random()
    ts = datetime.now(timezone.utc)
    now_h = (ts.hour + 3) % 24
    # Additive tod: up to +4°C at peak midday — prevents multiplicative runaway
    tod_add = 4.0 * max(0.0, math.sin(math.pi * (now_h - 6) / 12))
    rows, payload = [], []
    for s in SENSORS:
        base_t = _BASE_T[s['id']]
        if s['indoor']:
            temp = round(max(18.0, min(28.0, base_t + rng.gauss(0, 0.5))), 2)
        else:
            temp = round(max(18.0, min(28.0, base_t + tod_add * 0.5 + rng.gauss(0, 0.8))), 2)
        raw_pax = _BASE_PAX[s['id']] + rng.gauss(0, 0.25)
        pax = round(max(0.0, min(2.5, raw_pax)), 2)
        rows.append((s['id'], s['lon'], s['lat'], temp, pax, s['indoor']))
        rec = {'airport': _AIRPORT, 'sensor_id': s['id'], 'ts': ts, 'lon': s['lon'], 'lat': s['lat'],
               'indoor': s['indoor'], 'temperature': temp, 'pax_density': pax}
        payload.append(rec)
        _sink.add(rec)
    if not db.is_demo_mode:
        await db.executemany(
            """INSERT INTO sensor_current (airport,sensor_id,lon,lat,temperature,pax_density,updated_at)
               VALUES ($6,$1,$2,$3,$4,$5,$7)
               ON CONFLICT (airport, sensor_id) DO UPDATE SET
                 temperature=EXCLUDED.temperature, pax_density=EXCLUDED.pax_density,
                 updated_at=EXCLUDED.updated_at""",
            [(*r[:5], _AIRPORT, ts) for r in rows],
        )
        await db.executemany(
            """INSERT INTO sensor_readings (airport, sensor_id, ts, temperature, pax_density, indoor)
               VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING""",
            [(_AIRPORT, r[0], ts, r[3], r[4], r[5]) for r in rows],
        )
    return payload


async def get_current() -> list[dict]:
    """Latest reading per sensor for this airport — the READ side, used by the app."""
    if db.is_demo_mode:
        return []
    rows = await db.fetch(
        "SELECT * FROM sensor_current WHERE airport = $1 ORDER BY sensor_id", _AIRPORT)
    # `indoor` isn't stored in the table — tag it from the fixed sensor list so the
    # dashboard can split terminal vs apron averages without hardcoding sensor IDs.
    return [{**dict(r), 'indoor': _INDOOR.get(r['sensor_id'], False)} for r in rows]


async def get_readings(minutes: int = 60, sensor_id: str | None = None) -> list[dict]:
    """Every reading in the last `minutes` (max 24 h) — per-sensor telemetry for the
    live monitor. Optionally one sensor only. Oldest first."""
    if db.is_demo_mode:
        return []
    since = datetime.now(timezone.utc) - timedelta(minutes=max(1, min(minutes, 24 * 60)))
    rows = await db.fetch(
        """SELECT sensor_id, ts, temperature, pax_density, indoor
             FROM sensor_readings
            WHERE airport = $1 AND ts >= $2 AND ($3::text IS NULL OR sensor_id = $3)
            ORDER BY ts, sensor_id""",
        _AIRPORT, since, sensor_id)
    return [{**dict(r), 'ts': r['ts'].isoformat()} for r in rows]


async def get_trend(minutes: int = 15, bucket_minutes: int = 5) -> dict:
    """Live tracking for the Dashboard: one point per 5 s reading over the last
    `minutes` (averaged across sensors — overall, terminal, apron), plus rolling
    `bucket_minutes` window averages (e.g. 10–15, 5–10, 0–5 min ago). Aggregated in SQL,
    so the browser gets ~180 rows instead of 26 × 180."""
    if db.is_demo_mode:
        return {"points": [], "buckets": []}
    minutes = max(bucket_minutes, min(minutes, 24 * 60))
    now = datetime.now(timezone.utc)
    rows = await db.fetch(
        """SELECT ts,
                  avg(temperature)                        AS temp,
                  avg(temperature) FILTER (WHERE indoor)  AS temp_in,
                  avg(temperature) FILTER (WHERE NOT indoor) AS temp_out,
                  avg(pax_density)                        AS pax,
                  avg(pax_density) FILTER (WHERE indoor)  AS pax_in,
                  avg(pax_density) FILTER (WHERE NOT indoor) AS pax_out,
                  count(*)                                AS sensors
             FROM sensor_readings
            WHERE airport = $1 AND ts >= $2
            GROUP BY ts ORDER BY ts""",
        _AIRPORT, now - timedelta(minutes=minutes))
    r2 = lambda v, d: None if v is None else round(float(v), d)  # noqa: E731
    points = [{"ts": r["ts"].isoformat(), "temp": r2(r["temp"], 2), "temp_in": r2(r["temp_in"], 2),
               "temp_out": r2(r["temp_out"], 2), "pax": r2(r["pax"], 3), "pax_in": r2(r["pax_in"], 3),
               "pax_out": r2(r["pax_out"], 3), "sensors": r["sensors"]} for r in rows]
    buckets = []
    for k in range(minutes // bucket_minutes, 0, -1):          # oldest window first
        start, end = now - timedelta(minutes=k * bucket_minutes), now - timedelta(minutes=(k - 1) * bucket_minutes)
        sel = [r for r in rows if start <= r["ts"] < end]
        avg = lambda key, d: (round(sum(float(r[key]) for r in sel) / len(sel), d) if sel else None)  # noqa: E731
        buckets.append({"start": start.isoformat(), "end": end.isoformat(), "readings": len(sel),
                        "temp": avg("temp", 2), "pax": avg("pax", 3)})
    return {"airport": _AIRPORT, "interval_sec": INTERVAL_SEC, "minutes": minutes,
            "bucket_minutes": bucket_minutes, "now": now.isoformat(), "points": points, "buckets": buckets}


async def run_loop():
    """The sensor task of the generator job: one reading per sensor every 5 s.

    Table setup + the first reading are INSIDE the retry/except below. They used to sit
    outside it, so a transient DB error at startup killed this task permanently and
    sensors then silently never updated for the life of the process."""
    while True:
        try:
            if not db.is_demo_mode:
                await _ensure_table()
            break
        except Exception as e:  # noqa: BLE001
            print(f"[sensor_gen] table setup failed, retrying in {INTERVAL_SEC:.0f}s: {e}")
            await asyncio.sleep(INTERVAL_SEC)
    n = 0
    loop = asyncio.get_running_loop()
    next_at = loop.time()
    while True:
        try:
            await generate_and_store()
            await _sink.maybe_flush()
            n += 1
            if n % _PRUNE_EVERY == 0 and not db.is_demo_mode:
                await db.execute(
                    f"DELETE FROM sensor_readings WHERE airport = $1 "
                    f"AND ts < now() - interval '{READINGS_RETENTION_HOURS} hours'", _AIRPORT)
                print(f"[sensor_gen] {n} readings x {len(SENSORS)} sensors "
                      f"({_sink.files_written} {FILE_MINUTES}-min files written)")
        except Exception as e:  # noqa: BLE001
            print(f"[sensor_gen] error: {e}")
        # Fixed-rate schedule (not sleep-after-work), so readings stay on a 5 s grid
        # instead of drifting by however long each write took.
        next_at += INTERVAL_SEC
        await asyncio.sleep(max(0.0, next_at - loop.time()))

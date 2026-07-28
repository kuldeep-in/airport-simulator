"""Background sensor data generator — 30-second update loop."""
import asyncio, math, random
from datetime import datetime, timezone
from server.db import db
from server.websocket_manager import manager

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
            sensor_id   TEXT PRIMARY KEY,
            lon         DOUBLE PRECISION NOT NULL,
            lat         DOUBLE PRECISION NOT NULL,
            temperature DOUBLE PRECISION,
            pax_density DOUBLE PRECISION,
            updated_at  TIMESTAMPTZ DEFAULT now()
        )
    """)
    await db.executemany(
        """INSERT INTO sensor_current (sensor_id, lon, lat, temperature, pax_density)
           VALUES ($1,$2,$3,$4,$5) ON CONFLICT (sensor_id) DO NOTHING""",
        [(s['id'], s['lon'], s['lat'], _BASE_T[s['id']], _BASE_PAX[s['id']]) for s in SENSORS],
    )

async def generate_and_broadcast() -> list[dict]:
    rng = random.Random()
    now_h = (datetime.now(timezone.utc).hour + 3) % 24
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
        rows.append((s['id'], s['lon'], s['lat'], temp, pax))
        payload.append({'sensor_id': s['id'], 'lon': s['lon'], 'lat': s['lat'],
                        'temperature': temp, 'pax_density': pax})
    if not db.is_demo_mode:
        await db.executemany(
            """INSERT INTO sensor_current (sensor_id,lon,lat,temperature,pax_density,updated_at)
               VALUES ($1,$2,$3,$4,$5,now())
               ON CONFLICT (sensor_id) DO UPDATE SET
                 temperature=EXCLUDED.temperature, pax_density=EXCLUDED.pax_density,
                 updated_at=now()""",
            rows,
        )
    await manager.broadcast({'type': 'sensor_update', 'sensors': payload})
    print(f"[sensor_gen] updated {len(payload)} sensors")
    return payload

async def get_current() -> list[dict]:
    if db.is_demo_mode:
        return []
    rows = await db.fetch("SELECT * FROM sensor_current ORDER BY sensor_id")
    return [dict(r) for r in rows]

async def run_loop():
    if not db.is_demo_mode:
        await _ensure_table()
    await generate_and_broadcast()   # immediate first reading
    while True:
        await asyncio.sleep(15)
        try:
            await generate_and_broadcast()
        except Exception as e:
            print(f"[sensor_gen] error: {e}")

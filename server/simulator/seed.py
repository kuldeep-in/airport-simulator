"""Cold-start seeding: create schema, populate flights/gates/positions.

Idempotent — if the flights table already has rows we resume from existing
state rather than re-seeding.
"""

import json
import os
from datetime import datetime, timezone

from server.db import db
from server.simulator.scheduler import generate_schedule, CAPACITY_OF_TYPE

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")


def _polygon_centroid(coords):
    """Centroid of a GeoJSON Polygon exterior ring (list of [lon,lat])."""
    ring = coords[0]
    x = sum(p[0] for p in ring) / len(ring)
    y = sum(p[1] for p in ring) / len(ring)
    return (x, y)


def load_gates_geojson():
    path = os.path.join(DATA_DIR, "hia_gates.geojson")
    with open(path) as f:
        gj = json.load(f)
    gates = []
    for feat in gj["features"]:
        props = feat["properties"]
        centroid = _polygon_centroid(feat["geometry"]["coordinates"])
        gates.append({
            "id": props["id"],
            "concourse": props["concourse"],
            "capacity_class": props["capacity_class"],
            "lonlat": centroid,
        })
    return gates


def load_zones_geojson():
    path = os.path.join(DATA_DIR, "hia_zones.geojson")
    with open(path) as f:
        gj = json.load(f)
    zones = []
    for feat in gj["features"]:
        props = feat["properties"]
        name = props["zone_name"]
        gate_facing = any(k in name.lower() for k in ["concourse", "departure", "gate", "boarding"])
        zones.append({
            "zone_id": props["zone_id"],
            "zone_name": name,
            "gate_facing": gate_facing,
        })
    return zones


def assign_gates(flights, gates):
    """Assign a compatible gate to each flight. A388 -> A380 gates only.

    Gates are time-shared: since waves are staggered, we allocate greedily by
    scheduled arrival, freeing a gate once the previous flight has departed.
    """
    a380_gates = [g for g in gates if g["capacity_class"] == "A380"]
    wide_gates = [g for g in gates if g["capacity_class"] in ("A380", "WIDE")]
    narrow_gates = [g for g in gates if g["capacity_class"] == "NARROW"]
    # narrow-body aircraft can also use wide gates if needed
    narrow_pool = narrow_gates + wide_gates

    # track (gate_id -> free_after datetime)
    free_after = {g["id"]: None for g in gates}
    gate_by_id = {g["id"]: g for g in gates}

    def pick(pool, arr, dep):
        # prefer a gate already free before this arrival
        for g in pool:
            fa = free_after[g["id"]]
            if fa is None or fa <= arr:
                free_after[g["id"]] = dep
                return g
        # otherwise take the one that frees earliest (overlap tolerated)
        g = min(pool, key=lambda g: free_after[g["id"]] or arr)
        free_after[g["id"]] = dep
        return g

    ordered = sorted(flights, key=lambda f: f["scheduled_arr"])
    for f in ordered:
        cap = f["capacity_class"]
        if cap == "A380":
            pool = a380_gates or wide_gates
        elif cap == "WIDE":
            pool = wide_gates
        else:
            pool = narrow_pool
        g = pick(pool, f["scheduled_arr"], f["scheduled_dep"])
        f["assigned_gate"] = g["id"]
        f["_gate_lonlat"] = g["lonlat"]
    return flights


async def seed_if_empty():
    """Run schema (only if tables absent), then seed the static gate roster."""
    table_exists = await db.fetchval(
        "SELECT EXISTS (SELECT FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='flights')"
    )
    if not table_exists:
        with open(SCHEMA_PATH) as f:
            schema_sql = f.read()
        await db.run_script(schema_sql)

    if db.is_demo_mode:
        print("[seed] Demo mode — skipping DB seed.")
        return False

    # Seed gates (all 42 from GeoJSON) — static positions rendered on the map
    gates = load_gates_geojson()
    await db.executemany(
        """INSERT INTO gates (id, concourse, capacity_class, status)
           VALUES ($1, $2, $3, 'AVAILABLE')
           ON CONFLICT (id) DO NOTHING""",
        [(g["id"], g["concourse"], g["capacity_class"]) for g in gates],
    )

    print(f"[seed] Seeded {len(gates)} gates.")
    return True

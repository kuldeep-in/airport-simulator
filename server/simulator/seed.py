"""Cold-start seeding: create schema, populate flights/gates/positions.

Idempotent — if the flights table already has rows we resume from existing
state rather than re-seeding.
"""

import json
import os


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


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


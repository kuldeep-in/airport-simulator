"""The airport this process simulates and displays.

Airports are defined in config/airports.json (the registry shared with the per-airport
ingest jobs/pipelines). A process serves ONE airport, chosen by env `AIRPORT_IATA`
(default DOH) — there is no in-process airport switching. The `airport` column on
flight dataset (FLIGHT_TABLE below) and sim_positions tags and filters rows by it.

Simulation geometry (taxiways, gates, runway thresholds, arrival exit) exists for DOH
only (data/, taxiway_graph.py, gates.py, seed_runner.py). Other registry airports are
ingest-only until their geodata is added.
"""
import json
import os

_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "airports.json")

with open(_REGISTRY_PATH) as _f:
    REGISTRY: dict = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}

IATA = os.environ.get("AIRPORT_IATA", "DOH").upper()
if IATA not in REGISTRY:
    raise RuntimeError(f"AIRPORT_IATA={IATA!r} is not in config/airports.json "
                       f"(known: {', '.join(sorted(REGISTRY))})")

# `center` is (lon, lat) — the projection origin. `hub` is the fallback airline IATA
# used when the feed omits one.
META: dict = {**REGISTRY[IATA], "center": tuple(REGISTRY[IATA]["center"])}

# The airport's flight dataset in Lakebase: the TRIGGERED synced table fed by the ingest
# job (UC <catalog>.<schema>.flight_schedule_<iata> → Postgres <schema>.flight_schedule_<iata>).
# Read-only — sim-side facts (the gate the simulation used) live in sim_gate_assignment.
SCHEMA: str = META["schema"]
FLIGHT_TABLE: str = f"{SCHEMA}.flight_schedule_{IATA.lower()}"

# Raw telemetry landing zone in Unity Catalog (sensor readings + sim events, one JSONL
# file per stream per minute), loaded into Delta by the ingest pipeline. The catalog is
# the bundle's `catalog` variable; override with env UC_CATALOG.
CATALOG: str = os.environ.get("UC_CATALOG", "classic_stable_kuldeep")
TELEMETRY_VOLUME: str = f"/Volumes/{CATALOG}/{SCHEMA}/raw_telemetry"

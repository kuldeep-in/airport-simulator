# Databricks notebook source
# MAGIC %md
# MAGIC # AeroDataBox → UC Volume (raw landing)
# MAGIC
# MAGIC Task 1 of each per-airport ingest job (`resources/airport_<iata>.yml`).
# MAGIC
# MAGIC Fetches ONE airport's flight window from AeroDataBox (RapidAPI) and lands the **raw,
# MAGIC unmodified API response** as a JSON document in the airport's UC volume. Nothing is
# MAGIC parsed here; the SDP pipeline (`pipelines/flight_schedule`) does bronze → silver.
# MAGIC
# MAGIC ```
# MAGIC /Volumes/<catalog>/<schema>/raw_api/aerodatabox/date=YYYY-MM-DD/<IATA>_<YYYYMMDDTHHMM>Z.json
# MAGIC   { airport, slot_start, fetched_at, request: {...}, response: <AeroDataBox body> }
# MAGIC ```
# MAGIC
# MAGIC * **One file per 4-hour UTC slot.** The slot file doubles as the API cache: if it already
# MAGIC   exists the API is **not** called (a manual re-run or retry costs zero quota).
# MAGIC   Set `force=true` to re-fetch.
# MAGIC * The landed path is published as task value `payload_path` when run as a plain task
# MAGIC   (not possible inside a for-each iteration, where downstream tasks find it in the volume).

# COMMAND ----------

dbutils.widgets.text("airport", "DOH", "Airport IATA")
dbutils.widgets.text("catalog", "classic_stable_kuldeep", "Catalog")
dbutils.widgets.text("schema", "airport_simulator_doh", "Schema")
dbutils.widgets.text("volume", "raw_api", "Volume")
dbutils.widgets.text("secret_scope", "airport-sim", "Secret scope")
dbutils.widgets.text("secret_key", "rapidapi-key", "Secret key (RapidAPI)")
dbutils.widgets.text("offset_minutes", "-240", "Window offset (min)")
dbutils.widgets.text("window_minutes", "720", "Window length (min, API max 720)")
dbutils.widgets.dropdown("force", "false", ["false", "true"], "Force re-fetch")

AIRPORT = dbutils.widgets.get("airport").strip().upper()
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
VOLUME = dbutils.widgets.get("volume").strip()
SECRET_SCOPE = dbutils.widgets.get("secret_scope").strip()
SECRET_KEY = dbutils.widgets.get("secret_key").strip()
OFFSET_MINUTES = int(dbutils.widgets.get("offset_minutes"))
WINDOW_MINUTES = int(dbutils.widgets.get("window_minutes"))
FORCE = dbutils.widgets.get("force") == "true"

SLOT_HOURS = 4
RAPIDAPI_HOST = "aerodatabox.p.rapidapi.com"
RAW_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/aerodatabox"

# COMMAND ----------

import json
import os
from datetime import datetime, timezone

import requests

now = datetime.now(timezone.utc)
slot = now.replace(hour=(now.hour // SLOT_HOURS) * SLOT_HOURS, minute=0, second=0, microsecond=0)
out_dir = f"{RAW_ROOT}/date={slot:%Y-%m-%d}"
out_path = f"{out_dir}/{AIRPORT}_{slot:%Y%m%dT%H%M}Z.json"
print(f"airport={AIRPORT} now={now:%Y-%m-%d %H:%M}Z slot={slot:%Y-%m-%d %H:%M}Z")
print(f"target={out_path}")

# COMMAND ----------

def fetch_window() -> tuple[dict, dict]:
    """One AeroDataBox 'flights by airport (relative)' call. Returns (request, body).
    Raises on any failure, so the task (and the job) fails visibly and retries."""
    key = dbutils.secrets.get(SECRET_SCOPE, SECRET_KEY)
    params = {
        "offsetMinutes": OFFSET_MINUTES,
        "durationMinutes": WINDOW_MINUTES,
        "withLeg": "true",          # without it per-movement scheduledTime/gate come back empty
        "direction": "Both",
        "withCancelled": "false",
        "withCodeshared": "false",  # operating carrier only — no codeshare duplicates
        "withCargo": "false",
        "withPrivate": "false",
        "withLocation": "false",
    }
    url = f"https://{RAPIDAPI_HOST}/flights/airports/iata/{AIRPORT}"
    resp = requests.get(url, params=params, timeout=30,
                        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": RAPIDAPI_HOST})
    if not resp.ok:
        raise RuntimeError(f"AeroDataBox HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    if not isinstance(body, dict) or ("arrivals" not in body and "departures" not in body):
        raise RuntimeError(f"Unexpected AeroDataBox body (keys={list(body)[:6]})")
    return {"url": url, "params": params}, body


if os.path.exists(out_path) and not FORCE:
    stage = "cache-hit"
    print(f"Slot already landed — NOT calling the API: {out_path}")
else:
    request, body = fetch_window()
    doc = {
        "airport": AIRPORT,
        "slot_start": slot.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "request": request,      # never contains the API key
        "response": body,
    }
    os.makedirs(out_dir, exist_ok=True)
    # Write to a temp name then rename, so a reader never sees a half-written file.
    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, out_path)
    stage = "fetched"
    print(f"Landed {len(body.get('arrivals') or [])} arrivals / "
          f"{len(body.get('departures') or [])} departures → {out_path}")

# COMMAND ----------

# Task values are best-effort: they're rejected inside a for-each iteration (the combined
# ingest job), where downstream tasks locate the file from the volume instead.
try:
    dbutils.jobs.taskValues.set(key="payload_path", value=out_path)
    dbutils.jobs.taskValues.set(key="stage", value=stage)
except Exception as e:  # noqa: BLE001
    print(f"(task values not set: {str(e).splitlines()[0][:160]})")
dbutils.notebook.exit(json.dumps({"stage": stage, "payload_path": out_path}))

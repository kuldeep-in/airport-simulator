# End-to-end data flow

_Current as of 2026-11-26. Diagrams: [FLOW.md](FLOW.md) · tables: [ARCHITECTURE.md](ARCHITECTURE.md#3-table-catalog)._

This walks one flight and one sensor reading through the system, from the source to the
screen and into history.

## 1. Flight data

### 1.1 Fetch (Lakehouse, every 4 hours)
`[dev] airport-simulator-ingest` fires at 00/04/08/12/16/20 UTC. Stage 1 runs
`notebooks/fetch_aerodatabox.py` once per airport (job parameter `airports`: DOH, LHR).

- The 4-hour UTC slot is the cache key. If
  `/Volumes/classic_stable_kuldeep/airport_simulator_<iata>/raw_api/aerodatabox/date=…/<IATA>_<slot>Z.json`
  already exists, the API is **not** called (re-runs and retries are free).
- Otherwise one AeroDataBox "flights by airport" call covers `now−4h … now+8h`
  (cancelled, cargo and codeshare rows excluded server-side). The raw response is
  written unchanged, wrapped with `airport`, `slot_start`, `fetched_at`, `request`.

### 1.2 Transform (Lakehouse, same job)
Stage 2 runs the pipeline `[dev] airport-simulator-flights`
(`pipelines/flight_schedule/transformations/flight_schedule.py`), which defines the same
tables for every airport, each in its own schema with an `_<iata>` suffix.

1. **Bronze** `bronze_aerodatabox_raw_<iata>` — Auto Loader picks up each new file once;
   the whole document is stored as VARIANT, so API shape changes never break ingest.
2. **Silver observations** `silver_flight_observation_<iata>` — arrivals and departures
   exploded, parsed and normalised: `flight_number` = callsign without spaces,
   `scheduled_utc` = revised-or-scheduled, `flight_date` = UTC date of the original
   scheduled time (a stable key across delays), aircraft model → type code,
   `delay_minutes` = revised − scheduled, the home airport's city as origin/destination
   name. Rows without a flight number, time, or IATA code for the other end are dropped
   by expectations.
3. **Silver current** `silver_flight_schedule_<iata>` — AUTO CDC SCD1 on
   (airport, flight_date, flight_number, direction), ordered by fetch time; null values
   never overwrite known ones (tail, belt, names). Change Data Feed is on.
4. **Silver history** `silver_flight_schedule_history_<iata>` — SCD2 versions whenever
   times, gate, status, tail or type change.

### 1.3 Sync to Lakebase (same job)
Stage 3 runs `notebooks/sync_to_lakebase.py` per airport: it creates (first run) or
refreshes the **TRIGGERED synced table** `airport_simulator_<iata>.flight_schedule_<iata>`
in `airport-simulator-db / hia_sim`, waits for it, then re-grants USAGE + SELECT to the
app's Postgres role `airport_app`.

### 1.4 Simulate (App, generator child process)
The app starts `generator.py` as a supervised child (`RUN_GENERATOR=1`). It takes the
per-airport advisory lock, then the **flights task** ticks once a second:

- Every 60 s it reads the window (`scheduled_utc ≥ now − 50 min`) from the synced table,
  left-joined with `sim_gate_assignment`, and merges it into the arrival and departure rosters.
- **Arrivals** activate at touchdown time when the runway is clear, reserve a
  capacity-matched gate, taxi on the real taxiway graph, park, and leave after their
  dwell — or hand the stand to a linked departure (`data/turnarounds.json`).
- **Departures** appear at their gate 30–45 min before departure, push back on time,
  pick runway 34R (or 34L overflow), queue, hold short and take off.
- Each tick writes `sim_positions`, `gate_occupancy`, `sim_heartbeat`, and any new
  `sim_gate_assignment` rows; lifecycle transitions are emitted as events.

### 1.5 Serve (App)
The API server's reader loop polls Lakebase every second and pushes only changed
positions over the WebSocket; pages also call REST endpoints:

| Page | Data |
|---|---|
| Home (2D map) | WebSocket positions, gate snapshot, sensor heatmap |
| Schedule | `/api/schedule` (synced table + simulated gate) |
| Gates | `/api/gates` (occupancy, last 5 stays per gate, 24h utilisation) |
| Dashboard | `/api/analytics`, `/api/movements`, `/api/departure-queue`, `/api/sensors/trend` |
| About | `/api/health` (app, Lakebase, generator heartbeat), `/api/schedule`, `/api/genie/space` |

### 1.6 Ask Genie
The Ask Genie tab sends questions to `/api/genie/ask`; the app calls the Genie
Conversation API as its service principal and the UI polls `/api/genie/message` for the
answer text, generated SQL and result rows. The space queries the Delta silver tables on
the SQL warehouse — never Lakebase.

## 2. Sensor data

1. **Generate** — the generator's sensor task produces one reading for each of 26
   sensors on a fixed 5 s grid (temperature °C, passenger density pax/m²).
2. **Live** — `sensor_current` is upserted (latest per sensor) and `sensor_readings`
   appended (pruned to 24 h). The app polls `sensor_current` every 2 s and pushes
   `sensor_update`; the Dashboard reads `/api/sensors/trend` (last 15 min, averaged per
   reading, plus three 5-minute window averages).
3. **History** — every reading (and every sim event) is buffered and written once per
   15-minute window to `raw_telemetry/{sensors,events}/date=…/<stream>_<IATA>_<window>Z_<run>.jsonl`
   via the Files API. Open windows are flushed on SIGTERM (app shutdown or redeploy).
4. **Delta** — the next pipeline run (every 4h, or on demand) loads them into
   `sensor_readings_<iata>` and `sim_events_<iata>` for Genie and AI/BI.

## 3. Failure behaviour

| Failure | Effect |
|---|---|
| AeroDataBox call fails | Fetch task fails; the previous slot's data stays in silver and Lakebase; the next run retries |
| Pipeline or sync fails | Lakebase keeps the last synced rows; the generator and app keep running |
| Generator crashes | Supervisor restarts it after 5 → 15 → 60 s; flights are reconstructed in place; the heartbeat goes stale and the UI says so |
| App redeploy | Simulation restarts (under a minute); telemetry windows flushed on SIGTERM |
| Hard crash | The open 15-min telemetry window is lost from files; sensor readings remain in Lakebase for 24 h |

# Architecture

_Current as of 2026-11-26. Diagrams: [FLOW.md](FLOW.md). Walkthrough: [APPFLOW.md](APPFLOW.md)._

## 1. Boundaries

```
 User ─▶ ┌──────────────── APP  airport-simulator (Databricks App, MEDIUM) ────────────────┐
         │  App UI (React)  ◀── WebSocket / REST ──  API server (FastAPI, reader loop)        │
         │  Supervisor ──spawns/restarts──▶ GENERATOR (child process)                         │
         │                                   ├─ sensor task   every 5 s                       │
         │                                   └─ flights task  every 1 s                       │
         │  App identity = service principal (Lakebase airport_app, WRITE_VOLUME, Genie,      │
         │                 warehouse CAN_USE, SELECT on Delta tables)                         │
         └────────────┬───────────────────────────────┬───────────────────────────┬─────────┘
                      │ read/write                    │ telemetry files 15 min    │ /api/genie
                      ▼                               ▼                           ▼
 ┌──── LAKEBASE  airport-simulator-db / hia_sim ─┐ ┌──── LAKEHOUSE  classic_stable_kuldeep ─────┐  Genie space
 │ sensor_current · sensor_readings (24 h)       │ │ fetch notebook → raw_api / raw_telemetry   │  (SQL warehouse)
 │ sim_positions · gate_occupancy · sim_heartbeat│ │ SDP pipeline → Delta bronze/silver/history │ ──▶ Delta tables
 │ sim_gate_assignment                           │ │ sync notebook ─────────────────────────────┼─▶ synced tables
 │ flight_schedule_<iata>  (synced, read-only) ◀─┼─┤                                            │
 └───────────────────────────────────────────────┘ └──────────────── ▲ AeroDataBox (HTTPS) ────┘
```

| Boundary | Role |
|---|---|
| **App** | The only always-on compute. Serves the UI and API, and runs the simulation as a supervised child process. Holds no durable state. |
| **Lakebase** | Low-latency serving store: synced flight tables (read-only) and the live simulation/sensor tables. Live tables are shared across airports and keyed by `airport`. |
| **Lakehouse** | System of record. Raw files in UC volumes, Delta bronze/silver tables and full sensor/event history, one schema per airport (`airport_simulator_<iata>`). |
| **Genie** | Outside the app: an AI/BI Genie space over the Delta silver tables. The app's Ask Genie tab calls it through `/api/genie/*` as the app SP. |

## 2. Components

| Component | Where | Cadence | Reads → writes |
|---|---|---|---|
| Ingest job `[dev] airport-simulator-ingest` | Lakeflow Job (DAB) | 00/04/08/12/16/20 UTC | fetch (for each airport) → transform → sync_lakebase (for each airport) |
| Fetch `notebooks/fetch_aerodatabox.py` | Notebook task | each run; 1 API call / airport / 4h slot | AeroDataBox → `raw_api/aerodatabox/date=…/<IATA>_<slot>Z.json` (re-runs reuse the slot file) |
| Pipeline `[dev] airport-simulator-flights` | SDP, serverless, triggered | each run or on demand | raw_api → bronze → silver SCD1 + SCD2; raw_telemetry → `sensor_readings_<iata>`, `sim_events_<iata>` |
| Sync `notebooks/sync_to_lakebase.py` | Notebook task | after transform | `silver_flight_schedule_<iata>` → Lakebase `flight_schedule_<iata>` (TRIGGERED); re-grants SELECT to `airport_app` |
| Generator `generator.py` | App child process | flights 1 Hz (roster 60 s), sensors 5 s | synced flight table → live tables + sensor tables; telemetry files → `raw_telemetry` |
| API server + UI | Databricks App | positions 1 s, sensors 2 s, REST 5–15 s | Lakebase → WebSocket + REST → browser; `/api/genie` → Genie |
| Genie space | AI/BI | per question | question → SQL over `silver_flight_schedule_<iata>` → answer + rows |

Single-instance rule: gate and runway state live in the generator's memory, so exactly
one generator runs per airport — enforced by a Postgres advisory lock taken at startup
and held for the process lifetime.

**Delay-risk ML** (Lakehouse): weekly training job (MLflow → UC model `delay_risk_model`,
`@champion` gated on beating the baseline), hourly scoring job (:15 UTC) →
`gold_delay_risk_doh` → synced `delay_risk_doh` → app panel, RISK column, alerts; Genie
reads the scores and the prediction-vs-outcome view. See docs/APPFLOW.md §2.

## 3. Table catalog

### Unity Catalog — `classic_stable_kuldeep.airport_simulator_<iata>`

| Object | Type | Written by | Cadence | Key | Read by |
|---|---|---|---|---|---|
| `raw_api` | Volume | fetch notebook | 1 JSON / airport / 4h | `aerodatabox/date=…/<IATA>_<slot>Z.json` | pipeline (Auto Loader) |
| `raw_telemetry` | Volume | generator (Files API) | 1 JSONL / stream / 15 min | `{sensors,events}/date=…/<stream>_<IATA>_<window>Z_<run>.jsonl` | pipeline (Auto Loader) |
| `bronze_aerodatabox_raw_<iata>` | Streaming table | pipeline | 1 row / file | `source_file` | silver observation |
| `silver_flight_observation_<iata>` | Streaming table | pipeline | 1 row / flight / fetch | airport, flight_date, flight_number, direction (+ fetched_at) | SCD1 + SCD2 tables |
| `silver_flight_schedule_<iata>` | AUTO CDC SCD1, CDF on | pipeline | each run | PK airport, flight_date, flight_number, direction | synced table, Genie |
| `silver_flight_schedule_history_<iata>` | AUTO CDC SCD2 | pipeline | new version on status/time/gate/tail/type change | key + `__START_AT` | Genie / AI-BI |
| `sensor_readings_<iata>` | Streaming table | pipeline | 26 sensors × every 5 s | airport, sensor_id, ts | Genie / AI-BI |
| `sim_events_<iata>` | Streaming table | pipeline | per lifecycle transition | airport, flight_id, event, ts | Genie / AI-BI |
| `flight_schedule_legacy_doh` | Delta archive | one-off | static (98,302 rows, 2026-07-27 → 2026-11-26) | — | long history |

Flight columns: airport, flight_date, flight_number, direction, callsign, airline,
aircraft_type, aircraft_model, origin, destination, origin_name, destination_name,
scheduled_utc (effective), sched_time_utc, revised_time_utc, delay_minutes, api_gate,
aircraft_reg, baggage_belt, api_status, fetched_at.
Event types: TOUCHDOWN, GATE_IN, GATE_OUT, TURNAROUND_HANDOVER (arrivals); AT_GATE,
PUSHBACK, HOLD_SHORT, RUNWAY_ENTER, TAKEOFF (departures); `detail` VARIANT carries
late_s, dwell_s, queue_wait_s, taxi_m, …

**ML objects (DOH):** `gold_departure_features_doh` (training features, point-in-time),
`delay_risk_model` (UC model, `@champion`), `gold_delay_risk_doh` (current scores, MERGE,
CDF), `gold_delay_risk_log_doh` (every prediction), `v_delay_risk_outcomes_doh`
(2-h-ahead prediction vs actual delay).

### Lakebase — `airport-simulator-db` / `hia_sim`

| Table | Written by | Cadence | Key | Retention | Read by |
|---|---|---|---|---|---|
| `airport_simulator_<iata>.flight_schedule_<iata>` | sync notebook (synced, read-only) | every 4h | airport, flight_date, flight_number, direction | mirrors silver current | generator (60 s), app board/dashboard/gates (15 s) |
| `sim_positions` | flights task | 1 Hz per live flight | flight_id (+ airport col) | current only | app reader (1 s) |
| `gate_occupancy` | flights task | 1 Hz (full snapshot) | airport, gate_id | current | `/api/gates`, map snapshot |
| `sim_heartbeat` | flights task | 1 Hz | airport | one row / airport | app feed health (stale > 15 s) |
| `sim_gate_assignment` | flights task | per gate assignment | airport, flight_date, flight_number, direction | kept | board, gate history, utilisation, heatmap; generator restart |
| `sensor_current` | sensor task | 5 s | airport, sensor_id | latest | app (2 s) |
| `sensor_readings` | sensor task | 5 s × 26 | airport, sensor_id, ts | rolling 24h | `/api/sensors/trend`, `/api/sensors/history` |

Lakebase also holds the synced **`delay_risk_doh`** (ML scores; read by the app's
`/api/delay-risk`, Schedule RISK column and alert banner).

## 4. Cost design decisions

| Decision | Why |
|---|---|
| **Generator inside the app** (no separate 24/7 job) | The app is always on and mostly idle; the simulation runs on compute already paid for. One on/off switch for the whole demo. Fallback job kept PAUSED. |
| **Flights + sensors in one generator process** (two tasks) | A separate sensor job would add a second always-on compute; temp and pax come from the same sensors, so they stay one row per reading. |
| **TRIGGERED synced tables**, run by the ingest job | Data changes every 4h; continuous sync would be an always-on pipeline per table. |
| **History via files → Auto Loader**, not a Lakebase→Delta copy | Zero extra compute (files written by the running app, loaded in the existing pipeline run); no read load on Lakebase; history survives a failed pipeline run. CDC from Postgres would need an always-on gateway. |
| **15-minute telemetry files** | 96 files / stream / day instead of 1,440 → faster Auto Loader listing, fewer small files; flushed on SIGTERM so redeploys don't drop history. |
| **One pipeline, per-airport schemas** | One update per run for all airports; adding an airport is config. |
| **Lakebase for live reads, Delta for analytics** | App queries need ms latency and joins with 1 Hz sim tables; a SQL warehouse polled every 15 s would never auto-stop. Genie uses the warehouse only on demand. |

- **ML without always-on compute:** hourly batch scoring job (no model-serving
  endpoint), weekly serverless training (~5 min). Flight inputs change every 4 h and the
  2-h lead only needs hourly refresh, so an endpoint would add 24/7 cost for no gain.

## 5. Identity and permissions

- **App service principal** (`resources/app.yml` app resources): WRITE_VOLUME on
  `airport_simulator_doh.raw_telemetry`; CAN_RUN on the Genie space; CAN_USE on warehouse
  `SQL-xsmall`; SELECT on the synced, silver, history, sensor and event tables of
  both airports (USE CATALOG / USE SCHEMA are granted automatically).
- **Lakebase**: app and generator connect as Postgres role `airport_app` (password from
  secret scope `airport-sim`). `airport_app` owns the live tables; the synced tables are
  owned by the sync role and re-granted each sync.
- **Ingest job / pipeline**: run as the deploying user (owner of both schemas).

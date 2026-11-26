# airport-simulator

Real-time **2D + 3D digital twin of Hamad International Airport (HIA / OTHH, Doha)** on
Databricks. Real flights come from the **AeroDataBox** API, land in the **Lakehouse**
(Unity Catalog volumes → Spark Declarative Pipelines → Delta), are synced into
**Lakebase** (Postgres) for low-latency serving, and drive a ground simulation (taxi,
gates, runways, turnarounds) and synthetic terminal sensors inside a **Databricks App**.
An **Ask Genie** tab answers natural-language questions over the Delta history.

## Features

- **Home `/`** — 2D radar map: aircraft sprites on real HIA pavement, runway markings,
  pax/temperature heatmap, click-to-inspect flight popup, live arrivals/departures board.
- **Dashboard** — live demand, departure queue, gate/delay analytics, and pax density +
  temperature tracking for the last 15 minutes in 5-minute windows.
- **Schedule / Gates** — live flight board and gate occupancy with history.
- **Ask Genie** — questions over the flight tables, answered with text, SQL and results.
- **About** — live architecture diagram (App / Lakebase / Lakehouse boundaries) and a
  catalog of every table and volume in the flow.
- **3D `/3d`** — three.js scene (rotate, tilt, 3D/top toggle).

## Architecture

```
AeroDataBox ─▶ [Lakehouse] ingest job (every 4h): fetch notebook → raw_api volume
                           → SDP pipeline → Delta bronze / silver (SCD1 + SCD2)
                           → TRIGGERED synced table ─▶ [Lakebase] flight_schedule_<iata>
[App] generator (child process): flights 1 Hz, sensors 5 s ─▶ [Lakebase] live tables
                                 └─ telemetry files every 15 min ─▶ raw_telemetry volume ─▶ pipeline ─▶ Delta history
[App] API server ◀─ Lakebase ─▶ React UI (WebSocket + REST) ─▶ Ask Genie ─▶ Genie space ─▶ Delta (SQL warehouse)
```

One always-on compute (the App); everything else is short serverless runs every 4 hours
or on-demand Genie queries. Two airports are configured: **DOH** (simulated) and **LHR**
(ingest + Genie only). Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Layer | Tech |
|---|---|
| App backend | FastAPI, asyncio, `asyncpg`, Databricks SDK |
| Frontend | React, SVG, three.js / `@react-three/fiber`, zustand |
| Serving store | Lakebase (Postgres) — synced flight tables + live sim/sensor tables |
| Lakehouse | Unity Catalog volumes, Spark Declarative Pipelines (Auto Loader, AUTO CDC), Delta |
| Analytics | AI/BI Genie space on a serverless SQL warehouse |
| Deployment | Databricks Asset Bundles (`databricks.yml` + `resources/`) |

## Quick start (local)

```bash
cd frontend && npm install && npm run build && cd ..          # FastAPI serves frontend/dist as-is
RUN_GENERATOR=1 python -m uvicorn app:app --host 0.0.0.0 --port 8000   # needs PG* env vars, else demo mode
cd frontend && npm run dev                                     # optional vite dev server (proxies /api, /ws)
```

## Deploy (Databricks)

Both commands are required every time — `bundle deploy` uploads files and manages the
job, pipeline, volumes, Genie space and app resources; `apps deploy` serves the new code:

```bash
cd frontend && npm run build
databricks bundle deploy -p <profile>
databricks apps deploy airport-simulator \
  --source-code-path /Workspace/Users/<you>/.bundle/airport-simulator/dev/files -p <profile>
```

## Documentation

| Doc | What's in it |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Source of truth: infra, deploy, current state, known issues, conventions, codebase map |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Boundaries, components, table catalog, cost design decisions |
| [docs/APPFLOW.md](docs/APPFLOW.md) | End-to-end data-flow walkthrough (flights and sensors) |
| [docs/FLOW.md](docs/FLOW.md) | Mermaid diagrams of every flow |
| [resources/README.md](resources/README.md) | Bundle resources, Genie workflow, adding an airport |

## Repository layout

```
app.py, app.yaml, generator.py     App entry, app config, generator entry (runs as the app's child)
databricks.yml, resources/         Bundle: app, ingest job + pipeline + volumes, Genie space
notebooks/, pipelines/             Ingest notebooks and the SDP pipeline
genie/                             Genie space definition
config/airports.json               Airport registry
server/                            Backend (API, reader, Genie client, simulator/)
frontend/src/                      React app
data/                              Airport geodata + turnaround pairs
docs/                              Architecture, data flow, diagrams
```

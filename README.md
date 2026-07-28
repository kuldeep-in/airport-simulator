# airport-simulator

Real-time **3D + 2D digital twin of Hamad International Airport (HIA / OTHH, Doha)**,
deployed as a single **Databricks App**. Live flights come from the **AeroDataBox**
API; a FastAPI backend simulates their ground movement (taxi, gates, runways),
persists a live flight dataset in **Lakebase**, and streams updates to the browser
over a WebSocket. Doubles as a continuously growing dataset for BI / Genie / ML.

## Features

- **2D Radar (Home)** `/` — FlightRadar-style top-down map: plane sprites, runway
  markings, pax/temperature heatmap, zoom-locked to the airfield, click-to-inspect
  flight popup (airline logo, gate, route, scheduled time), and a live
  arrivals/departures board.
- **3D scene** `/3d` — three.js scene: real aircraft models taxiing on real HIA
  pavement (hidden from the tab bar; reachable by URL).
- **Schedule / Gates / Dashboard** tabs — live arrivals & departures board, gate
  occupancy, and metrics.
- **Live data** — real OTHH flights from AeroDataBox, refreshed on a 4-hour cadence
  and cached in Lakebase (deploys don't burn API quota).
- **Realistic ground sim** — Dijkstra taxi routing, single-occupant runway locks,
  gate assignment, cross-stream traffic separation.

## Tech stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, asyncio, `asyncpg` |
| Frontend | React, three.js / `@react-three/fiber`, zustand, SVG |
| Data | Lakebase (Postgres) — live positions, flight dataset, gate log |
| Live feed | AeroDataBox (RapidAPI) |
| Platform | Databricks App (FEVM workspace) |

## Quick start

```bash
# 1. Build the frontend (FastAPI serves frontend/dist as-is; it does not build it)
cd frontend && npm install && npm run build && cd ..

# 2. Run locally (needs PG* env vars for Lakebase, else it starts in demo mode)
python -m uvicorn app:app --host 0.0.0.0 --port 8000     # backend
cd frontend && npm run dev                                # vite dev server (proxies /api, /ws)
```

## Deploy (Databricks)

Both commands are required every time — `bundle deploy` uploads files, `apps deploy`
restarts the app:

```bash
cd frontend && npm run build      # rebuild if the frontend changed
databricks bundle deploy -p fevm01
databricks apps deploy airport-simulator \
  --source-code-path /Workspace/Users/you@example.com/.bundle/airport-simulator/dev/files \
  -p fevm01
```

## Documentation

| Doc | What's in it |
|---|---|
| **[docs/APPFLOW.md](docs/APPFLOW.md)** | End-to-end data flow for arrivals & departures — API → Lakebase dataset → simulation → 3D/2D views |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | Module responsibilities, data-flow diagrams, Lakebase table lineage |
| **[CLAUDE.md](CLAUDE.md)** | Source of truth: infra, deploy, current state, known issues, conventions, codebase map |
| **[PLAN.md](PLAN.md)** | Original product vision & feature roadmap |

## Repository layout

```
app.py                 FastAPI entry: REST endpoints, /ws, static serving, lifespan
app.yaml               Databricks App config
databricks.yml         DAB bundle
server/                Backend
  config.py            Auth: workspace client, OAuth, secret fetch
  db.py                asyncpg pool + demo-mode fallback
  simulator/           The simulation (broadcaster, live_schedule, flight_store,
                       seed_runner, departures, gates, taxiway_graph, runway, …)
frontend/src/          React app (Scene3D, pages/Radar2DPage, store, hooks)
data/                  Geo + roster JSON (hiaBase, hiaWays, gates, zones, …)
docs/                  APPFLOW.md, ARCHITECTURE.md
```

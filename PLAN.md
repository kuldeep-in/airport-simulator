# Hamad International Airport Simulator — Project Plan

## Vision

A real-time, browser-based airport digital twin for Hamad International Airport (DOH) that renders a live operational picture — aircraft positions, gate assignments, passenger density, and terminal conditions — on a fully interactive map. A built-in data simulator drives the app when no live feeds are connected.

---

## Core Features

### 1. Interactive Airport Map

- Full-screen, draggable, zoomable canvas of HIA
- Base layer: accurate terminal building footprint, taxiways, runways, aprons
- Smooth pan/zoom with inertia (mouse wheel, pinch-to-zoom, click-drag)
- North indicator and scale bar
- Minimap / overview inset for orientation

### 2. Gates & Terminal Structure

- All gates rendered as labeled polygons/icons (A, B, C, D, E concourses at HIA)
- Gate status colors: Available / Occupied / Boarding / Closed
- Tooltip on hover: gate ID, airline, flight number, scheduled/actual departure

### 3. Aircraft Locations

- Aircraft icons placed on gates and on taxiways/runways
- Callsign label, airline logo (or ICAO code), aircraft type
- Animated movement along taxiway paths for departing/arriving aircraft
- Status: Parked / Pushback / Taxiing / On Runway / Airborne

### 4. Overlay Layers (toggleable)


| Layer                               | Description                                                                         |
| ----------------------------------- | ----------------------------------------------------------------------------------- |
| **Temperature Zones**               | Terminal interior heatmap — colored zones (cool / comfortable / warm) per zone/pier |
| **Passenger Density (Pax Heatmap)** | Crowd density per zone, updated in near-real-time; color ramp green → yellow → red  |
| **Gate Utilization**                | % of gates occupied over current hour                                               |
| **Ground Traffic**                  | Active ground vehicles (buses, tugs, fuel trucks) — future phase                    |


### 5. Real-Time Data Architecture

- WebSocket or SSE channel pushing updates to the frontend every ~5 seconds
- State managed centrally; only diffs sent over the wire (not full snapshots)
- Timestamps on every event; staleness indicator if feed goes quiet

---

## Simulator Component

A background service that generates synthetic but realistic operational data for a configurable time window (default: 6 hours ahead, 1 hour behind).

### What it simulates


| Data Type               | Logic                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------------- |
| **Flight schedule**     | Seeded schedule based on real HIA peak hours (hub waves: ~06:00, ~12:00, ~17:00, ~22:00 GST) |
| **Aircraft movements**  | Arrival → park at gate → turnaround → pushback → taxi → departure cycle                      |
| **Gate assignments**    | Auto-assigns gates by aircraft size; respects concourse logic                                |
| **Pax counts per zone** | Peaks before departure banks, troughs between waves; random walk with wave envelope          |
| **Temperature zones**   | Slow drift with time-of-day envelope; occasional anomaly events (HVAC fault simulation)      |
| **Ground vehicles**     | Appear at gate ~45 min before departure, clear ~15 min after                                 |


### Simulator Controls (UI panel)

- Play / Pause
- Speed multiplier: 1× / 5× / 15× / 60×
- Jump to time (within the simulated window)
- Reset to now
- Inject event: HVAC fault, flight delay, gate change, emergency

---

## Technology Choices (to be decided)


| Concern             | Options                                                             | Notes                                                                 |
| ------------------- | ------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Map rendering       | **Mapbox GL JS**, Leaflet + custom SVG, **Deck.gl**, Pixi.js canvas | Mapbox gives best base-tile + layer compositing; Deck.gl for heatmaps |
| Airport geometry    | GeoJSON / custom SVG overlay                                        | HIA footprint needs to be digitized or sourced                        |
| Frontend framework  | **React**                                                           | Component model suits layered UI well                                 |
| Real-time transport | WebSocket (native or Socket.io), SSE                                | WebSocket preferred for bidirectional control                         |
| Backend / simulator | Python (FastAPI + asyncio) or Node.js                               | Python favored if Databricks integration planned                      |
| Deployment          | Databricks Apps                                                     | Consistent with existing tooling                                      |


---

## UI Layout

```
┌─────────────────────────────────────────────────────┐
│  Header: Airport name | Time (GST) | Sim controls   │
├──────────┬──────────────────────────────────────────┤
│  Layer   │                                          │
│  Panel   │           Full-screen Map                │
│          │                                          │
│ [x] Gates│    [aircraft icons, gate colors,         │
│ [x] Pax  │     heatmap overlays rendered here]      │
│ [ ] Temp │                                          │
│ [ ] Gnd  │                                ┌───────┐ │
│          │                                │Minimap│ │
│──────────│                                └───────┘ │
│  Detail  │                                          │
│  Panel   │                                          │
│ (gate /  │                                          │
│  flight  │                                          │
│  info on │                                          │
│  click)  │                                          │
└──────────┴──────────────────────────────────────────┘
│  Timeline scrubber (sim mode only)                  │
└─────────────────────────────────────────────────────┘
```

---

## Data Model (high-level)

### Flight

```
id, callsign, airline, aircraft_type, origin, destination,
scheduled_arrival, actual_arrival, scheduled_departure, actual_departure,
status, assigned_gate, pax_count
```

### Gate

```
id, concourse, label, coordinates, capacity_class,
status, current_flight_id
```

### Aircraft Position

```
flight_id, lat, lon, heading, ground_speed, status, timestamp
```

### Zone Metric (Pax / Temperature)

```
zone_id, zone_name, metric_type, value, unit, timestamp
```

---

## Phases

### Phase 1 — Foundation (MVP)

- [ ] Static HIA map with terminal outline and gate markers
- [ ] Simulator generates flight schedule and aircraft positions
- [ ] Aircraft icons appear at gates with basic status colors
- [ ] Gate info panel on click

### Phase 2 — Layers & Real-Time

- [ ] Pax density heatmap layer (simulated)
- [ ] Temperature zone layer (simulated)
- [ ] WebSocket feed; UI updates without page refresh
- [ ] Simulator speed controls and time scrubber

### Phase 3 — Polish & Live Data Hooks

- [ ] Smooth aircraft taxiway animation
- [ ] Live data connector interface (swap simulator for real feed)
- [ ] Alerts panel (delays, anomalies)
- [ ] Export / snapshot feature

---

## Decisions


| Question                  | Decision                                                                                                                                              | Implications                                                                                                                                                                                                          |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Airport geometry**      | Use freely available data — OpenStreetMap (OSM) via Overpass API or existing GeoJSON exports for HIA (IATA: DOH, ICAO: OTHH)                          | OSM has reasonable HIA coverage: terminal outline, gates, taxiways, runways. Will need manual cleanup/enrichment for gate labels and concourse groupings.                                                             |
| **Map tile provider** —   | OpenStreetMap raster tiles (tile.openstreetmap.org) or **OpenFreeMap** / **MapTiler free tier** for vector tiles. No API key required for OSM raster. | Vector tiles via free MapTiler or Protomaps self-hosted give better zoom fidelity than raster. Fallback: OSM raster tiles, zero friction.                                                                             |
| **Deployment target**     | Databricks Apps only                                                                                                                                  | Backend = FastAPI on Databricks Apps runtime. Frontend served as static assets bundled with the app. WebSocket handled via the app's HTTP server.                                                                     |
| **Authentication**        | Single-user demo — no login required                                                                                                                  | No auth middleware. App is protected at the Databricks Apps layer (PAT / workspace SSO).                                                                                                                              |
| **Real data integration** | Everything treated as a real-time feed                                                                                                                | Simulator output and any future live source (ADS-B, FIDS, BMS) both emit on the same WebSocket contract. Swapping simulator for live = changing the producer, not the consumer. Feed contract is the source of truth. |
| **Mobile support**        | Desktop-first                                                                                                                                         | Minimum target: 1280×800 browser window. Touch/pinch on map is nice-to-have but not a requirement. No responsive breakpoint engineering.                                                                              |


---

## Revised Technology Stack


| Concern                  | Choice                                           | Reason                                                                   |
| ------------------------ | ------------------------------------------------ | ------------------------------------------------------------------------ |
| Map rendering            | **Deck.gl** + **react-map-gl**                   | Hardware-accelerated WebGL layers; excellent heatmap and GeoJSON layers  |
| Map tiles                | **OpenFreeMap** (Protomaps-based, free, no key)  | Vector tiles, fast, no billing                                           |
| Airport geometry         | **OpenStreetMap / Overpass API** export for OTHH | Free, reasonably complete for HIA; enriched as GeoJSON                   |
| Frontend                 | **React + Vite**                                 | Fast dev cycle; Deck.gl has first-class React bindings                   |
| Real-time transport      | **WebSocket** (native, via FastAPI `websockets`) | Bidirectional; single channel for all event types                        |
| Backend / simulator      | **Python FastAPI + asyncio**                     | Async-friendly for WebSocket broadcast + Lakebase writes                 |
| **Operational database** | **Databricks Lakebase (managed Postgres, CU_1)** | High-frequency transactional writes; sub-second reads; no Spark overhead |
| State management         | **Zustand** (lightweight)                        | Sufficient for layer toggles, selected entity, sim controls              |
| Deployment               | **Databricks Apps**                              | Single target; `app.yaml` wires Lakebase resource automatically          |


---

## Why Lakebase (not UC Delta tables)


| Criteria                             | Lakebase (Postgres)                                      | UC Delta Tables                                 |
| ------------------------------------ | -------------------------------------------------------- | ----------------------------------------------- |
| Write frequency (every 5s)           | Native — transactional row writes                        | Expensive — Delta commits not designed for this |
| Read latency for WebSocket broadcast | Milliseconds (SQL query)                                 | Seconds (Spark SQL Warehouse cold path)         |
| Schema (relational, FK constraints)  | Native                                                   | Possible but awkward                            |
| Connection from FastAPI              | `psycopg2` — standard, lightweight                       | Requires Databricks Connect or REST API         |
| UC integration for analytics         | Yes — `create-database-catalog` registers Lakebase in UC | Native                                          |


Lakebase is the operational store. If historical analytics are needed later, UC synced tables can mirror Lakebase data into Delta via `create-synced-database-table` — no code change needed in the app.

---

## Lakebase Instance Plan


| Setting                    | Value                                                                |
| -------------------------- | -------------------------------------------------------------------- |
| Instance name              | `airport-simulator-db`                                               |
| Capacity                   | `CU_1` (sufficient for demo — 1 write loop + WebSocket reads)        |
| PostgreSQL version         | 16                                                                   |
| PostgreSQL database        | `hia_sim`                                                            |
| Auth                       | OAuth token via `generate-database-credential` (no static passwords) |
| PITR retention             | 7 days (default)                                                     |
| **Databricks CLI profile** | `**fevm01`**                                                         |
| **UC catalog**             | `**classic_stable_kuldeep`**                                         |
| **UC schema**              | `**airport_simulator`**                                              |


The Lakebase instance is declared as a resource in `app.yaml` so Databricks Apps provisions and manages the connection automatically.

UC registration (`create-database-catalog`) maps the `hia_sim` PostgreSQL database into `classic_stable_kuldeep.airport_simulator` — making Lakebase tables queryable from Databricks SQL and notebooks without any app code change.

---

## Lakebase Schema (`hia_sim` database)

### `flights`

```sql
CREATE TABLE flights (
    id              VARCHAR(12) PRIMARY KEY,       -- e.g. QR007
    callsign        VARCHAR(12) NOT NULL,
    airline         VARCHAR(4) DEFAULT 'QR',
    aircraft_type   VARCHAR(4) NOT NULL,           -- A359, B77W, A388 …
    origin          VARCHAR(4),                    -- ICAO
    destination     VARCHAR(4),                    -- ICAO
    scheduled_arr   TIMESTAMPTZ,
    actual_arr      TIMESTAMPTZ,
    scheduled_dep   TIMESTAMPTZ,
    actual_dep      TIMESTAMPTZ,
    status          VARCHAR(20) NOT NULL,          -- SCHEDULED, ARRIVED, BOARDING, DEPARTED, DELAYED
    assigned_gate   VARCHAR(6),
    pax_count       INT,
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

### `gates`

```sql
CREATE TABLE gates (
    id              VARCHAR(6) PRIMARY KEY,        -- e.g. A1, B22, E14
    concourse       CHAR(1) NOT NULL,              -- A/B/C/D/E
    capacity_class  VARCHAR(8) NOT NULL,           -- WIDE, NARROW, A380
    status          VARCHAR(16) NOT NULL,          -- AVAILABLE, OCCUPIED, BOARDING, CLOSED
    current_flight  VARCHAR(12) REFERENCES flights(id),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

### `aircraft_positions`

```sql
CREATE TABLE aircraft_positions (
    flight_id       VARCHAR(12) PRIMARY KEY REFERENCES flights(id),
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    heading         SMALLINT DEFAULT 0,            -- degrees 0–359
    ground_speed    SMALLINT DEFAULT 0,            -- knots
    phase           VARCHAR(16) NOT NULL,          -- PARKED, PUSHBACK, TAXIING, RUNWAY, AIRBORNE
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

### `zone_metrics`

```sql
CREATE TABLE zone_metrics (
    zone_id         VARCHAR(16) NOT NULL,
    metric_type     VARCHAR(12) NOT NULL,          -- PAX_DENSITY, TEMPERATURE
    value           REAL NOT NULL,
    unit            VARCHAR(8),                    -- pax/m², °C
    recorded_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (zone_id, metric_type, recorded_at)
);

-- Keep only last 2 hours of zone history (rolling delete by simulator)
CREATE INDEX ON zone_metrics (recorded_at DESC);
```

---

## Data Flow

```
┌─────────────────────────────────────────────────┐
│              Simulator (asyncio loop)            │
│  Every 5s:                                       │
│  1. Advance aircraft state machines              │
│  2. Update pax / temperature models              │
│  3. UPSERT → Lakebase (hia_sim)                 │
│  4. Read current state → broadcast via WebSocket │
└─────────────────────────────────────────────────┘
              │ psycopg2              │ WebSocket
              ▼                      ▼
      ┌──────────────┐      ┌────────────────────┐
      │   Lakebase   │      │   React Frontend   │
      │  (Postgres)  │      │  Deck.gl map +     │
      │  hia_sim DB  │      │  layer panels      │
      └──────────────┘      └────────────────────┘
```

The simulator is both the writer (to Lakebase) and the broadcaster (to WebSocket). FastAPI reads from Lakebase only for the initial page load state sync; subsequent updates come via WebSocket diff events.

---

## Feed Contract (WebSocket message schema)

All event types share the same envelope:

```json
{
  "type": "aircraft_update | gate_update | zone_metric | sim_tick | event",
  "ts": "<ISO-8601 UTC timestamp>",
  "payload": {}
}
```

The frontend is feed-agnostic — swapping simulator for a live source means changing the producer only.

---

## Airport Geometry Plan (OTHH)

1. Pull HIA footprint from OSM via Overpass API (aeroway=terminal, aeroway=gate, aeroway=taxiway, aeroway=runway)
2. Export as GeoJSON; store as static file in repo (`/data/hia_base.geojson`)
3. Manually annotate gate IDs (A1–A20, B1–B44, C1–C16, D1–D12, E1–E14) and concourse polygons
4. Validate overlay alignment by cross-referencing satellite imagery in QGIS or geojson.io
5. Terminal interior zones (for pax/temperature layers) drawn as separate polygon layer (`/data/hia_zones.geojson`) — approximated from public terminal maps

---

## Final Decisions (all open questions resolved)


| Question                   | Decision                                                                                                                                            |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Flight schedule flavor** | Qatar Airways hub-centric — QR callsigns, realistic widebody fleet mix (A350, B777, A380), peak departure banks mirroring QR's actual DOH hub waves |
| **Live data**              | None — everything simulated. The simulator is the sole data producer for the demo. No external API calls.                                           |
| **UI theme**               | Dark — night operations center aesthetic. Deep navy/charcoal background, neon accent colors for status, high-contrast gate labels.                  |


---

## Simulator — Qatar Airways Fleet & Schedule Detail

### Fleet Mix


| Type             | ICAO | Count in sim | Gate class                    |
| ---------------- | ---- | ------------ | ----------------------------- |
| Airbus A350-900  | A359 | 12           | Wide (D/E gates)              |
| Airbus A350-1000 | A35K | 6            | Wide (D/E gates)              |
| Boeing 777-300ER | B77W | 14           | Wide (B/C gates)              |
| Airbus A380-800  | A388 | 4            | A380-capable (A gates: A1–A7) |
| Airbus A320neo   | A20N | 8            | Narrow (B/C inner gates)      |
| Boeing 787-9     | B789 | 6            | Wide (B/C gates)              |


### Hub Wave Schedule (GST, simulated)


| Wave                 | Arrivals peak | Departures peak | Pax density peak |
| -------------------- | ------------- | --------------- | ---------------- |
| Wave 1 (overnight)   | 02:00–04:00   | 05:30–07:00     | 03:30–06:30      |
| Wave 2 (mid-morning) | 09:30–11:00   | 11:30–13:00     | 10:30–12:30      |
| Wave 3 (afternoon)   | 15:00–16:30   | 17:00–18:30     | 15:30–18:00      |
| Wave 4 (evening)     | 20:00–21:30   | 22:00–23:30     | 20:30–23:00      |


### Turnaround Cycle (per aircraft)

```
Arrival → Taxi in (8–15 min) → Park at gate → Ground services (45–90 min)
→ Boarding (30–45 min) → Pushback (5 min) → Taxi out (10–20 min) → Departure
```

---

## UI Theme Specification

### Color Palette


| Token           | Hex       | Usage                       |
| --------------- | --------- | --------------------------- |
| `bg-base`       | `#0a0e1a` | App background              |
| `bg-panel`      | `#111827` | Side panels, header         |
| `bg-card`       | `#1c2535` | Tooltips, info cards        |
| `border`        | `#2d3748` | Panel borders, dividers     |
| `text-primary`  | `#e2e8f0` | Main labels                 |
| `text-muted`    | `#64748b` | Secondary text              |
| `accent-blue`   | `#38bdf8` | Selected entity, highlights |
| `accent-amber`  | `#fbbf24` | Warnings, delays            |
| `status-green`  | `#22c55e` | Available / On time         |
| `status-yellow` | `#eab308` | Boarding / Caution          |
| `status-red`    | `#ef4444` | Closed / Delayed / Alert    |
| `status-gray`   | `#475569` | Inactive / No data          |


### Map Style

- Dark basemap (Protomaps dark scheme or equivalent OSM dark style)
- Terminal building: `#1e293b` fill, `#334155` stroke
- Runways: `#1e2d40` fill, `#2d4a6b` stroke
- Taxiways: `#172236` stroke only
- Gate polygons: color-coded by status (green/yellow/red/gray)
- Aircraft icons: white silhouette, rotated to heading, size scaled by aircraft class

### Heatmap Layer Colors

- Pax density: transparent → `#1d4ed8` (low) → `#f59e0b` (medium) → `#dc2626` (high)
- Temperature zones: `#1e40af` (cool, <20°C) → `#16a34a` (comfortable, 20–24°C) → `#ea580c` (warm, >24°C)

---

## Project Structure (planned)

```
airport-simulator/
├── app.yaml                        # Databricks Apps config (incl. Lakebase resource)
├── databricks.yml                  # DABs bundle definition
├── PLAN.md                         # This file
├── data/
│   ├── hia_base.geojson            # OSM-sourced airport geometry (runways, taxiways, terminal)
│   ├── hia_gates.geojson           # Gate polygons with ID, concourse, capacity_class
│   └── hia_zones.geojson           # Interior terminal zones (pax/temp overlays)
├── backend/
│   ├── main.py                     # FastAPI entry point; mounts /ws and serves frontend/dist
│   ├── db.py                       # Lakebase connection pool (psycopg2, OAuth token refresh)
│   ├── schema.sql                  # DDL for hia_sim database (run once on first deploy)
│   ├── simulator/
│   │   ├── scheduler.py            # QR hub wave schedule generator (seeded, deterministic)
│   │   ├── aircraft.py             # Aircraft state machine (PARKED→PUSHBACK→TAXIING→…)
│   │   ├── zones.py                # Pax density + temperature models (wave envelope + noise)
│   │   ├── seed.py                 # Populates flights + gates tables on startup if empty
│   │   └── broadcaster.py          # Asyncio loop: tick → upsert Lakebase → broadcast WS diff
│   └── models/
│       ├── flight.py               # Pydantic models matching Lakebase schema
│       ├── gate.py
│       ├── aircraft_position.py
│       └── zone_metric.py
└── frontend/
    ├── index.html
    ├── vite.config.js
    └── src/
        ├── main.jsx
        ├── App.jsx
        ├── store/
        │   └── useSimStore.js      # Zustand: ws state, layer visibility, selected entity, sim controls
        ├── components/
        │   ├── MapView.jsx         # Deck.gl + react-map-gl root canvas
        │   ├── layers/
        │   │   ├── GatesLayer.jsx          # GeoJSON polygons, status color
        │   │   ├── AircraftLayer.jsx       # IconLayer, rotated to heading
        │   │   ├── PaxHeatmapLayer.jsx     # HeatmapLayer from zone_metrics
        │   │   └── TempZoneLayer.jsx       # GeoJSON fill, temperature color ramp
        │   ├── panels/
        │   │   ├── LayerPanel.jsx          # Left sidebar: layer toggles
        │   │   ├── DetailPanel.jsx         # Left sidebar: gate/flight info on click
        │   │   └── SimControls.jsx         # Header: play/pause, speed ×, time display
        │   └── Minimap.jsx
        └── hooks/
            └── useWebSocket.js     # WS connect, reconnect, dispatch to Zustand store
```

---

## `app.yaml` Structure

```yaml
command: ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

resources:
  - name: airport-simulator-db
    database:
      instance: airport-simulator-db          # Lakebase instance name
```

The `database` resource injects the Lakebase connection string and OAuth credentials into the app environment automatically — no hardcoded secrets.

---

## Build & Deploy Steps (Databricks Apps)

```bash
# 1. Build frontend
cd frontend && npm run build        # outputs to frontend/dist/

# 2. Deploy (fevm01 profile)
databricks bundle deploy -p fevm01 --var="catalog=classic_stable_kuldeep,schema=airport_simulator"
databricks apps deploy airport-simulator -p fevm01
```

On first startup, `backend/seed.py` runs `schema.sql` (idempotent `CREATE TABLE IF NOT EXISTS`) and seeds the initial flight schedule if tables are empty. Subsequent restarts skip seeding and resume from current Lakebase state.

---

## No remaining open questions — ready to implement.

---

## High-Level Activity Plan

### Activity 1 — Infrastructure Setup

1.1 Verify `fevm01` profile is authenticated and workspace is reachable  
1.2 Create Lakebase instance `airport-simulator-db` (CU_1) on fevm01 workspace  
1.3 Wait for instance to reach AVAILABLE state  
1.4 Create PostgreSQL database `hia_sim` inside the instance  
1.5 Run `schema.sql` — create all four tables (`flights`, `gates`, `aircraft_positions`, `zone_metrics`)  
1.6 Register Lakebase in Unity Catalog: `create-database-catalog` → `classic_stable_kuldeep.airport_simulator`  
1.7 Verify tables visible in UC (`SHOW TABLES IN classic_stable_kuldeep.airport_simulator`)

### Activity 2 — Airport Geometry

2.1 Pull HIA (OTHH) geometry from Overpass API: terminal outline, gates, taxiways, runways  
2.2 Export and clean as `hia_base.geojson` (remove noise, fix topology)  
2.3 Annotate gate features with `id`, `concourse`, `capacity_class` → `hia_gates.geojson`  
2.4 Draw interior terminal zone polygons (pax/temp regions) → `hia_zones.geojson`  
2.5 Validate all GeoJSON in geojson.io against satellite reference

### Activity 3 — Backend: Simulator Engine

3.1 `scheduler.py` — generate QR hub wave flight schedule (4 waves × ~12 flights, seeded deterministic)  
3.2 `seed.py` — populate `flights` and `gates` tables from schedule + HIA gate list on cold start  
3.3 `aircraft.py` — implement aircraft state machine (SCHEDULED → ARRIVED → PARKED → BOARDING → PUSHBACK → TAXIING → DEPARTED)  
3.4 `zones.py` — pax density model (wave envelope + Gaussian noise per zone); temperature model (diurnal drift + HVAC anomaly events)  
3.5 `db.py` — Lakebase connection pool with OAuth token auto-refresh  
3.6 `broadcaster.py` — asyncio tick loop: advance state → UPSERT Lakebase → compute diff → push WebSocket message

### Activity 4 — Backend: FastAPI App

4.1 `main.py` — mount WebSocket at `/ws`, serve `frontend/dist/` at `/`  
4.2 REST endpoint `GET /api/state` — full state snapshot for initial page load  
4.3 REST endpoint `GET /api/sim/controls` — expose play/pause/speed state  
4.4 REST endpoint `POST /api/sim/controls` — accept speed change, pause, time jump commands from UI  
4.5 WebSocket connection manager — fan-out broadcast to all connected clients

### Activity 5 — Frontend: Map & Layers

5.1 Scaffold React + Vite project; install Deck.gl, react-map-gl, Zustand  
5.2 `MapView.jsx` — dark basemap (OpenFreeMap), fit map to HIA bounding box on load  
5.3 `GatesLayer.jsx` — render `hia_gates.geojson` as colored polygons by gate status  
5.4 `AircraftLayer.jsx` — IconLayer with aircraft silhouettes rotated to heading, sized by class  
5.5 `PaxHeatmapLayer.jsx` — HeatmapLayer driven by zone_metrics pax density values  
5.6 `TempZoneLayer.jsx` — GeoJSON fill layer, temperature color ramp per zone  
5.7 Click interaction — select gate or aircraft → populate DetailPanel

### Activity 6 — Frontend: Panels & Controls

6.1 `LayerPanel.jsx` — toggles for Gates, Aircraft, Pax Heatmap, Temperature; dark theme  
6.2 `DetailPanel.jsx` — gate info (flight, airline, status, pax); aircraft info (callsign, type, phase, route)  
6.3 `SimControls.jsx` — header bar: current sim time (GST), play/pause, speed selector (1×/5×/15×/60×), wave indicator  
6.4 `Minimap.jsx` — small overview inset, viewport rectangle indicator  
6.5 `useWebSocket.js` — connect to `/ws`, auto-reconnect on drop, dispatch events to Zustand store  
6.6 `useSimStore.js` — Zustand store: ws state, all entity maps, layer flags, selected entity, sim controls

### Activity 7 — Databricks Apps Deployment

7.1 Author `app.yaml` — command, Lakebase database resource  
7.2 Author `databricks.yml` — DABs bundle with app definition  
7.3 First deploy: `databricks bundle deploy -p fevm01` + `databricks apps deploy -p fevm01`  
7.4 Smoke test: open app URL, verify map loads, WebSocket connects, aircraft appear  
7.5 Validate Lakebase writes: query `classic_stable_kuldeep.airport_simulator.aircraft_positions` in Databricks SQL

### Activity 8 — Polish & Demo Readiness

8.1 Tune simulator timing — verify all 4 hub waves produce visible pax heatmap spikes  
8.2 Add gate status legend and layer panel icons  
8.3 Inject demo events: HVAC fault → temperature anomaly visible on map; flight delay → gate status change  
8.4 Performance check: WebSocket message size, frame rate at 60× sim speed  
8.5 Final deploy + share app URL
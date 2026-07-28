# App data-flow — arrivals & departures

How live flight data moves from the **AeroDataBox** API, through the persisted
Lakebase dataset, into the running simulation, and onto the two views (the 2D Radar
home page and the 3D scene at `/3d`). This is the end-to-end flow for **arrivals**
and **departures**.

> See also: **[../README.md](../README.md)** (overview & quick start),
> **[../CLAUDE.md](../CLAUDE.md)** (infra, deploy, current state, codebase map),
> **[ARCHITECTURE.md](ARCHITECTURE.md)** (module responsibilities, table lineage).

---

## The two pages

Both pages render the **same live data** from the same backend simulation — they're
just different views:

| Page | Route | What it is |
|---|---|---|
| **2D Radar (Home)** | `/` | FlightRadar-style top-down SVG map — plane sprites, runway markings, pax/temp heatmap, click-to-inspect popup, arrivals/departures board. **The landing page (since 2026-07-28).** |
| **3D scene** | `/3d` | three.js perspective scene — 3D aircraft models taxiing on real pavement. **Hidden from the tab bar** (URL-only). |

The flow below is identical for both — the simulation is entirely backend; each page
only renders whatever the backend last broadcast.

---

## Data-flow — DEPARTURES

### 1. Hit the API → get the departures window
- `server/simulator/live_schedule.py` calls AeroDataBox's "flights by airport"
  endpoint for **OTHH**, window `[now − 4 h, now + 8 h]` (`OFFSET_MINUTES=-240`,
  `WINDOW_MINUTES=720` — the API max).
- **Cadence: at most once per 4-hour UTC slot** (00:00 / 04:00 / 08:00 / …). The API
  is *not* called on every deploy — see step 3.
- HIA-side outbound movements are parsed (`parse_departures`) into roster rows:
  `{ callsign, aircraft_type, destination, assigned_gate (real API gate or null),
  scheduled_utc, api_status }`.

### 2. What happens to that data
- `_publish(payload, now)` upserts the full window into the **`flight_schedule`**
  dataset, then loads the live rosters back from it filtered to `scheduled_utc ≥
  now − 10 min` (see "Persisted flight dataset" below).
- The loaded departure rows become the in-memory **departure roster** (`departures.py`):
  per-flight dicts plus `_scheduled_dep[fid]` (absolute real departure time) and
  `_api_gate[fid]` (the real gate the airport reported, which may be missing or a stand
  we don't model).

### 3. How that data is stored in the background
- **Two Lakebase tables** (see "Persisted flight dataset"): `flight_schedule` (one
  upserted row per flight — the READ source) and `flight_feed_cache` (`slot_start` PK,
  raw `jsonb` payload — the 4-hour cache that avoids re-hitting the API on deploy).
- Startup (every deploy restarts the app) is **cache-first**: if the current 4-hour
  slot's payload is already cached, it's reused with **zero API calls**. Only crossing
  into a new, uncached slot triggers a real fetch. → deploys, tests and validation
  never burn API quota.
- `departures.load_roster()` reads the published roster at startup and clears any
  stale `live_positions` rows for those flights. (`live_positions` is the only
  per-flight live table — a row exists exactly while a flight is on the map. The
  old `plane_status` table was retired 2026-07-27.)

### 4. How the data is populated on the app (lifecycle)
Each departure sits **`SCHEDULED`** (metadata only — on the Schedule board, not on the
map) until its time approaches. `broadcaster.py` ticks once/second with `sim_time`
pinned to the real UTC clock. Lifecycle:

```
SCHEDULED ──(≤45 min before dep)──▶ GATE_PARKED ──(dep time reached)──▶ ACTIVE ──▶ DONE ──▶ (removed)
             claim gate + build route      pushback              taxi-out → HOLDING_SHORT → RUNWAY → takeoff
```

- **GATE_PARKED**: at `_GATE_PARK_LEAD_SEC` (45 min) before departure the aircraft
  appears parked at its stand, visible on the map.
- **ACTIVE**: the instant `sim_time` reaches `scheduled_dep`, it pushes back and taxis
  out immediately (phases `TAXIING_OUT → HOLDING_SHORT → RUNWAY`).
- **DONE → removed**: on reaching the runway's far end it broadcasts one final
  `INACTIVE` payload (the frontend deletes on that), releases its gate + runway lock.
- **Schedule-board status** (display only, `ConnectionsPage.jsx`): a departure shows
  **GATE CLOSED** within 2 min before `scheduled_dep` OR once it's
  TAXIING_OUT/HOLDING_SHORT (unless already RUNWAY/Departed). Never written to the DB.

Each tick writes the flight's position/phase/speed to Lakebase `live_positions` and
broadcasts the diff over the WebSocket; the browser renders it.

### 5. When & how the gate is assigned
- **When:** at the **GATE_PARKED transition (~45 min before departure)**, inside
  `_activate_route()`. (Not at fetch time — the airport often assigns real gates only
  shortly before departure, so between 4-hour refreshes the API gate is frequently
  missing.)
- **How (priority):**
  1. **Claim the real API gate** (`gates.claim_gate`) if the airport reported one we
     model *and* it's currently free.
  2. **Otherwise reserve any free modelled gate** (`gates.reserve_gate`) — a random
     capacity-matched stand the live registry (`gates.py`) shows as unoccupied (i.e.
     not currently held by another arrival or departure).
  3. Only if **every** gate is occupied does it stay `SCHEDULED` and retry next tick
     (never crashes). So a gateless departure still taxis out on a real free stand.
- The chosen gate is written to the flight's `flight_schedule.sim_gate` column
  (`flight_store.enqueue_sim_gate`, flushed each tick). This is the gate the **sim
  actually used**, which may differ from `api_gate` (the airport's reported gate) in
  the fallback cases above. The Gates page's history is the **last 5 appearances
  per gate** (keyed on `sim_gate`), each window DERIVED from `scheduled_utc ± dwell`
  (`flight_store.recent_gate_history`) — there is no separate gate-assignment table.

### 6. When & how the taxi-out route is defined
- **When:** at the same GATE_PARKED moment, in `_build_route_for_runway()`.
- **How:** Dijkstra over the real taxiway graph (`taxiway_graph.py`, built from
  `hiaWays.json`) from the assigned gate to the runway hold-short point, **forbidding
  runway edges** (`avoid_runway=True`) so the taxi can't cut across runway pavement.
  The path is then extended: hold-short → threshold → runway far end (the takeoff roll).
- **Runway choice:** decided at **pushback** (`_choose_runway`), not at gate-park —
  currently always **34R** (departures) so 34L stays clear for arrivals.

---

## Data-flow — ARRIVALS

### 1. Hit the API → get the arrivals window
- Same single AeroDataBox call / same 4-hour slot / same cache as departures (one
  fetch returns both). HIA-side inbound movements are parsed (`parse_arrivals`) into:
  `{ callsign, aircraft_type, origin, scheduled_utc (real TOUCHDOWN time), api_status }`.
- **Note:** arrivals carry **no gate** from the API (the API only exposes baggage belt,
  not an arrival stand) — the sim assigns one itself (step 5).

### 2. What happens to that data
- Same `_publish` path as departures: upsert into `flight_schedule`, then load the
  arrival roster back filtered to `scheduled_utc ≥ now − 10 min`.
- The loaded rows become the in-memory **arrival roster** (`seed_runner.py`) plus
  `_scheduled_arr[fid]` (absolute real touchdown time).

### 3. How that data is stored in the background
- Same two Lakebase tables as departures (`flight_schedule` upserted rows +
  `flight_feed_cache` raw cache) — one fetch/payload holds both directions.
- `seed_runner.load_gate_positions()` loads the gate coordinates at startup; the
  arrival roster is loaded from the `flight_schedule` dataset.

### 4. How the data is populated on the app (lifecycle)
Each arrival sits **`SCHEDULED`** until its real touchdown time. Lifecycle:

```
SCHEDULED ──(touchdown time reached)──▶ ARRIVING ──(taxi finishes)──▶ PARKED ──(30 min)──▶ GONE ──▶ (removed)
             assign gate + build route    landing roll → taxi-in     sits at gate
```

- **ARRIVING**: at `scheduled_arr`, if the arrival runway (34L) is clear it "touches
  down" and starts a realistic speed profile — fast landing roll (~150 kt) decelerating
  to the runway exit (~30 kt), then a steady ~18 kt taxi to the gate.
- **PARKED**: when its taxi finishes, it parks and sits for `_GROUND_TIME_SEC`
  (**30 min real time**, measured from the actual gate-arrival time — not tick-counted).
- **GONE → removed**: after 30 min it broadcasts `INACTIVE`, releases its gate, and
  disappears (no depart mechanic — arrivals and departures are independent rosters).
- **Schedule-board status** (display only, `ConnectionsPage.jsx`): an arrival shows
  **LANDED** as soon as it's on the map (has a sim `phase`), regardless of the raw
  `api_status`. Gateless arrivals render a blank gate cell. Never written to the DB.

Same per-tick Lakebase write + WebSocket broadcast as departures.

### 5. When & how the gate is assigned
- **When:** at the **ARRIVING transition (touchdown time)**, inside
  `_assign_gate_and_route()`. (Earlier than departures because an arrival needs a
  destination stand the moment it lands.)
- **How:** `gates.reserve_gate(capacity_class)` — a **random free capacity-matched
  gate** from the live registry (arrivals never have an API gate). If none is free it
  holds (stays SCHEDULED, delays touchdown) and retries — never crashes.
- The chosen gate is written to `flight_schedule.sim_gate` (same as departures). For
  arrivals this is the **only** gate info that exists (`api_gate` is always null).

### 6. When & how the taxi-in route is defined
- **When:** at the same touchdown moment, in `_assign_gate_and_route()`.
- **How:** a fixed straight **landing-roll** segment from the shared touchdown point
  (`_ARRIVAL_START_LONLAT`) to a capacity-class exit point (heavier aircraft roll
  further), then Dijkstra over the real taxiway graph (`avoid_runway=True`) from that
  exit to the reserved gate, with a short stub into the actual stand.
- The **arrival runway (34L)** is guarded by a single-occupant lock (`runway.py`) so
  only one aircraft is on the landing roll at a time; it's released the moment the
  arrival clears the runway exit onto the taxiway.

---

## Persisted flight dataset (`flight_schedule` — the source of truth)

The AeroDataBox payload is upserted into a durable Lakebase table
(`server/simulator/flight_store.py`), which the rest of the app **reads from** — so
the app also doubles as a growing dataset for historical analysis (BI / Genie / ML).
(The old `flight_feed_cache` still exists, but only as the 4-hour raw-payload cache
that avoids re-hitting the API on deploy; `flight_schedule` is the read source.)

### 1. Fetch window
- Each API call pulls a **`[now − 4 h, now + 8 h]`** window (12 h total) for OTHH
  (`OFFSET_MINUTES=-240`, `WINDOW_MINUTES=720`). The **−4 h tail** lets already-operated
  flights get their final status/gate written to their row (history); the **+8 h head**
  gives the map/board upcoming flights to simulate.
- Cadence: **once per 4-hour UTC slot** (00:00 / 04:00 / 08:00 / …), cache-first on
  startup so deploys/tests don't call the API.

### 2. Store — upsert into `flight_schedule`
- Every fetch (or cached-payload reload) upserts its parsed arrivals + departures via
  `flight_store.upsert()`.
- **Upsert key = `(flight_date, flight_number, direction)`.** Re-observing the same
  flight **updates its single row in place** rather than appending — exactly **one
  authoritative record per flight**.
- **Columns:** callsign, airline, aircraft_type, origin, destination, `scheduled_utc`
  (effective = revised-or-scheduled — drives the sim clock), `sched_time_utc` +
  `revised_time_utc` (captured separately), `api_gate` (real API gate — null for
  arrivals), **`sim_gate`** (the gate the sim actually placed the aircraft at —
  sim-derived, kept separate so real-vs-synthetic stays clear), `aircraft_reg`
  (tail #, sparse), `baggage_belt` (arrivals), `api_status`.
- **Upsert rules:** `aircraft_reg`/`baggage_belt` use `COALESCE` (a later
  observation missing the value doesn't wipe a previously-captured one); the
  live-feed upsert never overwrites `sim_gate`; and **`api_status` is refreshed only
  for FUTURE flights** (`scheduled_utc ≥ now()`) so already-operated flights keep
  their final status in history.
- **Gate history** (Gates page) = the **last 5 appearances per gate** keyed on
  `sim_gate`, each window DERIVED from `scheduled_utc ± dwell`
  (`flight_store.recent_gate_history`) — there is no separate `gate_assignments`
  table (removed 2026-07-27). `/api/live-debug` also exposes a `dataset` block
  (row count / column coverage / recent rows) for spot-checking the store.

### 3. What each surface reads from the dataset
- `live_schedule._publish()` upserts the full window, then **loads the live rosters
  back from `flight_store.load_window(now)`** — the table, not the raw payload, is the
  source of truth.

| Surface | Reads | Filter |
|---|---|---|
| **Schedule board** (arrivals + departures) | the dataset | records with `scheduled_utc ≥ now − 10 min` (`_BOARD_GRACE_SEC`) |
| **Map** (3D + 2D) | the dataset | the simulator activates arrivals/departures from the same loaded roster |

- **Board** starts ~10 min in the past and looks forward — older rows stay in the
  table for history but aren't loaded into the live roster.
- **Map & board share one dataset**, so they're always consistent.

### 4. Why (the Databricks angle)
- An upserted, columnar, retained table turns the live app into a **continuously
  growing dataset** you can put a Lakeview/AI-BI dashboard, a Genie space, or a
  forecast model on top of — closing the loop **live app → Lakebase table → BI/AI**.

---

## Shared building blocks

| Concern | Where |
|---|---|
| 1-second clock, tick loop, WebSocket broadcast | `server/simulator/broadcaster.py` |
| Live AeroDataBox fetch + 4-hour slot cache | `server/simulator/live_schedule.py` |
| Persistent flight dataset (`flight_schedule`, upserted) | `server/simulator/flight_store.py` |
| Arrivals state machine | `server/simulator/seed_runner.py` |
| Departures state machine | `server/simulator/departures.py` |
| Live gate-occupancy registry | `server/simulator/gates.py` |
| Persisted flight dataset + gate history (Lakebase `flight_schedule`) | `server/simulator/flight_store.py` |
| Dijkstra taxiway/runway routing (`hiaWays.json`) | `server/simulator/taxiway_graph.py` |
| Single-occupant runway locks (dep 34R / arr 34L) | `server/simulator/runway.py` |
| Ground-traffic separation (cross-stream) | `server/simulator/traffic.py` |
| Synthetic pax/temperature sensor metrics | `server/simulator/sensor_gen.py` / `zones.py` |
| 2D Radar — HOME `/` (self-contained SVG: sprites, popup, flight board) | `frontend/src/pages/Radar2DPage.jsx` |
| Top-down GLB→PNG plane sprites (for the 2D map) | `frontend/src/pages/planeSprites.js` |
| 3D scene (three.js) — now at `/3d`, hidden from tabs | `frontend/src/components/Scene3D.jsx` |
| Page routing / tab bar | `frontend/src/App.jsx`, `frontend/src/components/TopTabs.jsx` |
| Airline logo PNGs + manifest (popups) | `frontend/public/airline-logos/` |

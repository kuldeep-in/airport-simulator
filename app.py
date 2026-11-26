"""SYSTEM B — the app. Reads Lakebase, serves the UI. Runs NO simulation, NO fetching.

Entry point for the Databricks App. THREE independent systems share only Lakebase:

    SYSTEM C  fetcher.py    scheduled job (00/04/08/12/16/20 UTC)
                            AeroDataBox ──▶ flight_schedule
    SYSTEM A  generator.py  continuous job
                            reads flight_schedule ──▶ sim_positions / gate_occupancy
                                                      sim_heartbeat / sensor_current
    SYSTEM B  this file     Databricks App
                            reads all of the above ──▶ WebSocket ──▶ 2D map / 3D scene

Consequences of that split, all deliberate:
  - This module imports NO state machine and NO AeroDataBox client. There is no roster,
    no gate registry, no runway lock and no RapidAPI key in this process — a restart
    here loses nothing, and the API key isn't reachable from anything web-facing.
  - The sim clock is just real UTC (`reader.sim_state()`); the generator pins its own
    clock to the same wall clock, so no coordination is needed.
  - Pausing is gone: the app cannot pause a separate process, and a pause button that
    silently did nothing would be worse than none.
  - Each upstream system has its OWN health signal on /api/live-debug —
    `position_feed` (generator heartbeat) and `flight_feed` (how far ahead the fetcher
    has kept the window). "The app is up" implies nothing about either.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server.db import db
from server.websocket_manager import manager
from server import reader
from server import airports
from server.simulator import connections
from server.simulator import flight_store
from server.simulator import analytics
from server.simulator import positions
from server.simulator import gate_state
from server.simulator import sensor_gen
from server import generator_supervisor
from server import genie

_bg_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bg_task
    await db.get_pool()
    # NOTE: no SIM schema creation and no seeding here — the generator owns the write side
    # and creates its own tables. The app is a pure reader; if a table is missing that
    # means the generator hasn't run yet, which `feed.alive == false` already reports.
    # One exception to "no schema here": flight_store.ensure_table — idempotent, but
    # it ALSO runs the `airport`-column migration. The app filters reads by `airport`,
    # so the column must exist even if the app deploys before the generator/fetcher
    # restart to run their own ensure_table. Best-effort: a concurrent PK swap may
    # raise and is caught; the ADD COLUMN IF NOT EXISTS (all the reads need) still lands.
    try:
        await flight_store.ensure_table()
        # The app now filters sim_positions by `airport` (positions.fetch_for_airport),
        # but that column is added by the generator's ensure_tables — which only runs on
        # a generator restart. Ensure it here too (idempotent; app_app owns the table) so
        # the map query never hits a missing column before the generator picks up the code.
        await positions.ensure_tables()
        # Same reason for the airport-keyed gate + sensor tables (sim_schema migration):
        # the app filters them by airport, so the key must exist before the first read.
        await gate_state.ensure_table()
        await sensor_gen.ensure_tables()
    except Exception as e:  # noqa: BLE001 — never block startup on this
        print(f"[startup] schema ensure skipped: {e}")
    _bg_task = asyncio.create_task(reader.run_loop())
    # The simulation runs as a supervised child process of this app (RUN_GENERATOR=1):
    # same always-on compute, no separate generator job. See server/generator_supervisor.py.
    generator_supervisor.start()
    print(f"[startup] genie space configured: {'yes' if genie.SPACE_ID else 'NO (GENIE_SPACE_ID empty)'}")
    print("[startup] app started — reading generator output from Lakebase.")
    yield
    await generator_supervisor.stop()
    if _bg_task:
        _bg_task.cancel()
    await db.close()


app = FastAPI(title="Airport Operations", lifespan=lifespan)


@app.get("/api/health")
async def health():
    """App health PLUS generator liveness — the two are now independent, so "the app is
    up" no longer implies "flights are moving"."""
    return {
        "status": "ok",
        "demo_mode": db.is_demo_mode,
        "clients": manager.count,
        "feed": await positions.feed_status(),
        "generator": generator_supervisor.status(),
    }


@app.get("/api/state")
async def get_state():
    """Full snapshot for initial load — read entirely from Lakebase."""
    return await reader.build_full_state()


@app.get("/api/sim")
async def get_sim():
    """The clock only. Speed/pause controls are gone: the app cannot control a separate
    generator process (see the module docstring)."""
    return reader.sim_state()


# The /api/aircraft and /api/aircraft/{id}/start endpoints were removed along
# with the Simulate page (their only caller). Arrivals auto-activate from the
# live schedule now; there's no manual single-flight replay.


def _board_row(rec, live: dict, direction: str) -> dict:
    """One Schedule-board row: a `flight_schedule` record LEFT-JOINED with the live
    position row for the same flight (matched on flight number).

    Both sides are Lakebase — the schedule record owns identity + real-world fields,
    and `live` (from `sim_positions`) supplies what only the generator knows: the
    simulated phase, the stand it actually used, gate_arrival, runway. Before the
    systems were split this overlay came from the state machines' memory; reading it
    from the table is precisely what lets the app run without them.

    A flight absent from `sim_positions` isn't currently on the map — it gets
    `phase: null`, which the frontend already renders as "not on the map yet".

    Gate precedence: the live stand, else the `sim_gate` persisted on the schedule row
    (so a flight that has already left still shows the stand it used), else the
    airport's reported `api_gate`."""
    fid = rec["flight_number"]
    sched = rec["scheduled_utc"]
    row = {
        "id":            fid,
        "callsign":      rec["callsign"] or fid,
        "airline":       rec["airline"] or "QR",
        "aircraft_type": rec["aircraft_type"],
        "origin":        rec["origin"],
        "destination":   rec["destination"],
        "origin_name":      rec["origin_name"],
        "destination_name": rec["destination_name"],
        "api_status":    rec["api_status"],
        "assigned_gate": (live.get("assigned_gate") if live else None)
                         or rec["sim_gate"] or rec["api_gate"],
        "phase":         live.get("phase") if live else None,
        "on_map":        bool(live),
    }
    if direction == "arrival":
        row["scheduled_arr"] = sched.isoformat() if sched else None
        ga = live.get("gate_arrival") if live else None
        row["gate_arrival"] = ga.isoformat() if ga else None
    else:
        row["scheduled_dep"] = sched.isoformat() if sched else None
        # The frontend keys its countdown + connection filtering off `status`.
        # Derived from the live phase: a flight not on the map is still scheduled.
        phase = live.get("phase") if live else None
        row["status"] = ("GATE_PARKED" if phase == "GATE_PARKED"
                         else "ACTIVE" if phase else "SCHEDULED")
        row["runway"] = live.get("runway") if live else None
    return row


@app.get("/api/schedule")
async def get_schedule():
    """Combined inbound/outbound flight list for the Schedule page — BOTH sides read
    from Lakebase, no simulator involvement.

    Row set: `flight_store.board_window(now)` — everything scheduled >= now − 10 min,
    no upper bound, evaluated at REQUEST time. Live sim state is then joined from
    `sim_positions`. So the board is complete regardless of what the generator
    currently holds, and stays correct even if the generator is down."""
    now = datetime.now(timezone.utc)
    active = airports.IATA
    arr_recs, dep_recs = await flight_store.board_window(now, active)
    live = {r["flight_id"]: r for r in await positions.fetch_for_airport(active)}
    return {
        "inbound": [_board_row(r, live.get(r["flight_number"]), "arrival") for r in arr_recs],
        "outbound": [_board_row(r, live.get(r["flight_number"]), "departure") for r in dep_recs],
        # Connection pairs are authored static data, so they need no live state.
        "connections": connections.connections_for_schedule(list(live.keys())),
    }


@app.get("/api/genie/space")
async def genie_space():
    """Which Genie space the Ask Genie tab talks to (title, warehouse)."""
    try:
        return await genie.run(genie.space_info)
    except Exception as e:  # noqa: BLE001
        return {"configured": bool(genie.SPACE_ID), "error": str(e)[:300]}


@app.post("/api/genie/ask")
async def genie_ask(body: dict):
    """Ask a question (new conversation, or a follow-up when conversation_id is given).
    Returns ids immediately; poll /api/genie/message for the answer."""
    q = (body or {}).get("question", "").strip()
    if not q:
        return JSONResponse({"error": "question is required"}, status_code=400)
    if not genie.SPACE_ID:
        return JSONResponse({"error": "GENIE_SPACE_ID is not configured"}, status_code=503)
    try:
        return await genie.run(genie.ask, q, (body or {}).get("conversation_id"))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:500]}, status_code=502)


@app.get("/api/genie/message")
async def genie_message(conversation_id: str, message_id: str):
    try:
        return await genie.run(genie.message, conversation_id, message_id)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:500], "status": "FAILED"}, status_code=502)


@app.get("/api/sensors/trend")
async def sensor_trend(minutes: int = 15, bucket_minutes: int = 5):
    """Live tracking: per-reading averages (overall / terminal / apron) for the last
    `minutes`, plus rolling `bucket_minutes` window averages — the Dashboard's
    Pax density and Temperature panels."""
    return await sensor_gen.get_trend(minutes, bucket_minutes)


@app.get("/api/sensors/history")
async def sensor_history(minutes: int = 60, sensor_id: str | None = None):
    """Every 5 s reading in the last `minutes` (≤ 24 h), optionally for one sensor —
    per-sensor telemetry + the basis for all-sensor temperature/density views. Served
    from the Lakebase rolling log; older history lives in Delta sensor_readings_<iata>."""
    readings = await sensor_gen.get_readings(minutes, sensor_id)
    return {"airport": airports.IATA, "interval_sec": sensor_gen.INTERVAL_SEC,
            "minutes": minutes, "count": len(readings), "readings": readings}


@app.get("/api/gates")
async def get_gates():
    """Every gate's status + capacity class + occupancy history for the Gates page.

    Current occupancy comes from `gate_occupancy` (published each tick by the
    generator — it used to be read out of gates.py's in-memory registry, which this
    process no longer has). History is the last 5 appearances per gate, derived from
    `flight_schedule.sim_gate`."""
    now = datetime.now(timezone.utc)
    active = airports.IATA
    overview = await gate_state.fetch_snapshot()
    db_history = await flight_store.recent_gate_history(now, active, 5)
    # Utilization is computed over ALL stays in the last 24h (not the 5-item display
    # cap), so the KPI % is real and its thresholds are reachable — see
    # flight_store.gate_utilization_24h.
    util = await flight_store.gate_utilization_24h(now, active)
    for g in overview:
        g["history"] = db_history.get(g["id"], [])
        g["utilization_pct"] = round(util.get(g["id"], 0.0), 1)
    return {"gates": overview}


@app.get("/api/movements")
async def get_movements():
    """Arrivals + departures per hour over the last 12 h, from the persisted
    flight_schedule dataset (real history, not just the live board window). Used
    by the Dashboard's 'Movements per hour' chart."""
    active = airports.IATA
    return {"movements": await flight_store.movements_per_hour(datetime.now(timezone.utc), active, 12)}


@app.get("/api/analytics")
async def get_analytics():
    """Historical analytics rollups over the flight_schedule dataset (delay-by-hour,
    delay distribution, on-time trend, gate×hour heatmap, top routes, airline share)
    for the Operations Dashboard."""
    active = airports.IATA
    return await analytics.dashboard_analytics(datetime.now(timezone.utc), active)


_DEP_QUEUE_MAX = 10


@app.get("/api/departure-queue")
async def get_departure_queue():
    """The next `_DEP_QUEUE_MAX` departures, current and future, for the Dashboard's
    departure-queue waterfall, ordered by pushback.

    Live departures carry the generator's stage `timeline` (actual + projected
    pushback → taxi → queue → hold → runway → airborne). Flights not yet at their
    stand have `timeline: null` — the frontend projects those from typical stage
    durations. Excluded: flights already airborne, and past slots no longer on the
    map (they've gone). Live flights are taken from `sim_positions` too, not just the
    board window, so one that pushed back >10 min ago and is still queuing stays in."""
    now = datetime.now(timezone.utc)
    active = airports.IATA
    _, dep_recs = await flight_store.board_window(now, active)
    live = {r["flight_id"]: r for r in await positions.fetch_for_airport(active)}
    tls = await positions.fetch_departure_timelines(active)

    rows, seen = [], set()
    for rec in dep_recs:
        fid = rec["flight_number"]
        seen.add(fid)
        tl = tls.get(fid)
        if tl and "airborne" in tl["actual"]:
            continue
        if not tl and fid not in live and rec["scheduled_utc"] < now:
            continue
        row = _board_row(rec, live.get(fid), "departure")
        row["timeline"] = tl
        rows.append(row)
    for fid, tl in tls.items():
        if fid in seen or fid not in live or "airborne" in tl["actual"]:
            continue
        row = positions.to_payload(live[fid])
        row["timeline"] = tl
        rows.append(row)

    rows.sort(key=lambda r: r["timeline"]["t"]["pushback"] if r.get("timeline")
              else (r.get("scheduled_dep") or ""))
    return {"departures": rows[:_DEP_QUEUE_MAX], "now": now.isoformat()}


@app.get("/api/live-debug")
async def get_live_debug():
    """Diagnostic, from the app's side of the split. Never exposes the API key.

    `feed` is the first thing to check: it reports whether the GENERATOR process is
    alive (heartbeat age). Since the two systems are independent, "the app is up" says
    nothing about whether flights are moving — and an empty map with `feed.alive =
    false` means the job is down, not that the feed is empty.

    Three independent systems, so three separate health signals:
      `position_feed` — is the GENERATOR (continuous job) alive? (heartbeat age)
      `flight_feed`   — is the FETCHER (scheduled job) keeping the window ahead of us?
                        Derived from flight_schedule itself, so the app needs no
                        AeroDataBox client — `horizon_hours` falling toward 0 is the
                        signal that the fetcher has stopped.
      `dataset`       — row counts / column coverage of the persisted dataset."""
    now = datetime.now(timezone.utc)
    active = airports.IATA
    live = await positions.fetch_for_airport(active)
    return {
        "airport": active,
        "position_feed": await positions.feed_status(),
        "flight_feed": await flight_store.fetch_status(now, active),
        "live_positions_count": len(live),
        "dataset": await flight_store.debug_sample(),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        snapshot = await reader.build_full_state()
        await manager.send_personal(ws, snapshot)
        while True:
            # keep the connection alive; ignore inbound content
            await ws.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(ws)
    except Exception:  # noqa: BLE001
        await manager.disconnect(ws)


# ---- static frontend ------------------------------------------------------
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

# expose raw geojson data files to the frontend
DATA_DIR = Path(__file__).parent / "data"
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    def _index() -> FileResponse:
        # no-cache: browsers must re-check index.html on every load, so a new deploy
        # (new hashed /assets/ bundle names) shows up without a hard refresh.
        return FileResponse(str(FRONTEND_DIST / "index.html"),
                            headers={"Cache-Control": "no-cache"})

    @app.get("/")
    async def serve_index():
        return _index()

    _DIST_ROOT = FRONTEND_DIST.resolve()

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws"):
            return JSONResponse({"error": "not found"}, status_code=404)
        # Resolve the candidate and confirm it stays INSIDE the dist root before
        # serving — otherwise a path like "../server/config.py" would escape the
        # frontend directory and leak backend source. Any escape / non-file falls
        # through to the SPA index.html (client-side routing handles unknown paths).
        candidate = (FRONTEND_DIST / full_path).resolve()
        if (
            candidate.is_file()
            and (candidate == _DIST_ROOT or _DIST_ROOT in candidate.parents)
        ):
            return FileResponse(str(candidate))
        return _index()
else:
    @app.get("/")
    async def no_frontend():
        return {"message": "Frontend not built. Run `npm run build` in frontend/."}

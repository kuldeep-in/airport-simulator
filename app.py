"""FastAPI entry point for the Hamad International Airport Simulator."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server.db import db
from server.websocket_manager import manager
from server.simulator import broadcaster
from server.simulator import sensor_gen
from server.simulator import seed_runner
from server.simulator import departures
from server.simulator import connections
from server.simulator import gates
from server.simulator import live_schedule
from server.simulator import flight_store
from server.simulator.seed import seed_if_empty

_bg_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bg_task
    # ensure pool + schema + seed
    await db.get_pool()
    try:
        await seed_if_empty()
    except Exception as e:  # noqa: BLE001
        print(f"[startup] seed error: {e}")
    _bg_task = asyncio.create_task(broadcaster.run_loop())
    asyncio.create_task(sensor_gen.run_loop())
    asyncio.create_task(live_schedule.run_loop())
    print("[startup] broadcaster started.")
    yield
    if _bg_task:
        _bg_task.cancel()
    await db.close()


app = FastAPI(title="HIA Simulator", lifespan=lifespan)


class SimUpdate(BaseModel):
    speed: int | None = None
    paused: bool | None = None


@app.get("/api/health")
async def health():
    return {"status": "ok", "demo_mode": db.is_demo_mode, "clients": manager.count}



@app.get("/api/state")
async def get_state():
    """Full snapshot for initial load."""
    return await broadcaster.build_full_state()


@app.get("/api/sim")
async def get_sim():
    return broadcaster.get_sim_state()


@app.post("/api/sim")
async def post_sim(update: SimUpdate):
    state = broadcaster.set_sim_state(speed=update.speed, paused=update.paused)
    await manager.broadcast({"type": "sim_tick", **state})
    return state


# The /api/aircraft and /api/aircraft/{id}/start endpoints were removed along
# with the Simulate page (their only caller). Arrivals auto-activate from the
# live schedule now; there's no manual single-flight replay.


@app.get("/api/schedule")
async def get_schedule():
    """Combined inbound/outbound roster for the Schedule page, plus the real
    (authored) inbound->outbound connection list, keyed by arrival callsign —
    see server/simulator/connections.py."""
    return {
        "inbound": seed_runner.arrivals_schedule_info(),
        "outbound": departures.schedule_info(broadcaster.SIM["sim_time"]),
        "connections": connections.connections_for_schedule(departures.all_ids()),
    }


@app.get("/api/gates")
async def get_gates():
    """Every gate's current status + capacity class + occupancy history for the
    Gates page. Live current-occupant comes from the in-memory registry; the
    history is the last 5 aircraft appearances per gate, derived from the
    flight_schedule dataset (keyed on sim_gate) so it survives restarts. Falls
    back to the in-memory history in demo mode / when the dataset is empty."""
    overview = gates.gates_overview()
    db_history = await flight_store.recent_gate_history(broadcaster.SIM["sim_time"], 5)
    if db_history:
        for g in overview:
            g["history"] = db_history.get(g["id"], [])
    return {"gates": overview}


@app.get("/api/movements")
async def get_movements():
    """Arrivals + departures per hour over the last 12 h, from the persisted
    flight_schedule dataset (real history, not just the live board window). Used
    by the Dashboard's 'Movements per hour' chart."""
    return {"movements": await flight_store.movements_per_hour(broadcaster.SIM["sim_time"], 12)}


# Arrivals and departures auto-activate at their real scheduled times from the
# live AeroDataBox feed (see server/simulator/live_schedule.py) — there is no
# manual scheduling trigger. The old POST /api/schedule/start-arrivals and
# /start-departures endpoints were removed.


@app.get("/api/live-debug")
async def get_live_debug():
    """Diagnostic: last AeroDataBox fetch/parse outcome + roster sizes, plus the
    persisted flight_schedule dataset's row count / column coverage / sample rows
    (to verify the upsert is storing every field). Never exposes the API key."""
    return {**live_schedule.debug_status(), "dataset": await flight_store.debug_sample()}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        snapshot = await broadcaster.build_full_state()
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

    @app.get("/")
    async def serve_index():
        return FileResponse(str(FRONTEND_DIST / "index.html"))

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws"):
            return JSONResponse({"error": "not found"}, status_code=404)
        candidate = FRONTEND_DIST / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
else:
    @app.get("/")
    async def no_frontend():
        return {"message": "Frontend not built. Run `npm run build` in frontend/."}

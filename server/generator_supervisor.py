"""Runs the generator as a supervised CHILD PROCESS of the Databricks App.

Cost choice: the app is already on 24/7 and mostly idle, so the simulation runs on its
compute instead of a separate always-on job. It's a separate *process* (not an asyncio
task in the web server) because the simulator does synchronous CPU work — Dijkstra
routing when a flight activates can take 10-100+ ms — which would otherwise stall every
HTTP request and WebSocket push while it ran.

    app (uvicorn)  ──spawns──▶  python -u generator.py   (GENERATOR_CHILD=1, same env/creds)
         ▲                            │ stdout/stderr → the app log ("[generator] …")
         └──── restarts on exit ──────┘ backoff 5 s → 15 s → 60 s

Enabled by env RUN_GENERATOR=1. Single-instance safety doesn't depend on this module:
the generator takes a per-airport Postgres advisory lock at startup (see generator.py),
so a second copy — e.g. the fallback job, or an overlapping redeploy — just waits.
"""
import asyncio
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKOFF_SEC = (5, 15, 60)
_HEALTHY_RUN_SEC = 300      # a child that lived this long resets the backoff

_task: asyncio.Task | None = None
_proc: asyncio.subprocess.Process | None = None
_state = {"enabled": False, "restarts": 0, "last_exit_code": None, "pid": None}


def enabled() -> bool:
    return os.environ.get("RUN_GENERATOR", "0") == "1"


async def _supervise() -> None:
    global _proc
    loop = asyncio.get_running_loop()
    failures = 0
    while True:
        env = {**os.environ, "GENERATOR_CHILD": "1", "PYTHONUNBUFFERED": "1"}
        started = loop.time()
        try:
            _proc = await asyncio.create_subprocess_exec(
                sys.executable, "-u", os.path.join(_ROOT, "generator.py"), cwd=_ROOT, env=env)
        except Exception as e:  # noqa: BLE001
            print(f"[supervisor] could not start generator: {e}")
            await asyncio.sleep(_BACKOFF_SEC[-1])
            continue
        _state["pid"] = _proc.pid
        print(f"[supervisor] generator started (pid {_proc.pid})")
        rc = await _proc.wait()
        _state["last_exit_code"] = rc
        _state["pid"] = None
        ran = loop.time() - started
        failures = 0 if ran >= _HEALTHY_RUN_SEC else failures + 1
        delay = _BACKOFF_SEC[min(max(failures, 1), len(_BACKOFF_SEC)) - 1]
        _state["restarts"] += 1
        print(f"[supervisor] generator exited rc={rc} after {ran:.0f}s — restarting in {delay}s")
        await asyncio.sleep(delay)


def start() -> None:
    """Call from the app's startup (inside the running event loop)."""
    global _task
    _state["enabled"] = enabled()
    if not _state["enabled"]:
        print("[supervisor] RUN_GENERATOR != 1 — generator not started by the app")
        return
    if _task is None:
        _task = asyncio.create_task(_supervise(), name="generator-supervisor")


async def stop() -> None:
    """Call from the app's shutdown: stop supervising, then stop the child cleanly."""
    global _task
    if _task:
        _task.cancel()
        _task = None
    if _proc and _proc.returncode is None:
        _proc.terminate()
        try:
            await asyncio.wait_for(_proc.wait(), timeout=25)   # time to flush telemetry files
        except asyncio.TimeoutError:
            _proc.kill()


def status() -> dict:
    running = bool(_proc and _proc.returncode is None)
    return {**_state, "running": running}

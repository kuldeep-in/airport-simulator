"""SYSTEM A entry point — the position generator, run as a continuous Databricks Job.

    python generator.py

Runs two independent tasks forever (one job, no extra compute), for ONE airport
(env AIRPORT_IATA, default DOH):
  - broadcaster.run_loop()   1 Hz  — FLIGHTS: pull the window from the synced flight
                                     table (every 60 s), pick flights due now, assign
                                     gates, compute positions → sim_positions,
                                     gate_occupancy, sim_heartbeat, sim_gate_assignment;
                                     emits sim events → volume raw_telemetry/events
  - sensor_gen.run_loop()    5 s   — SENSORS: temperature + passenger density for all
                                     26 sensors → sensor_current, sensor_readings (24 h),
                                     volume raw_telemetry/sensors

It makes NO AeroDataBox calls — fetching is the ingest job's (resources/ingest.yml) —
so a generator restart costs zero API quota by construction.

There is no HTTP server and no WebSocket here. The app (System B, app.py) is a separate
process that reads those tables; they share only Lakebase. See
server/simulator/positions.py for the contract and the reasoning.

WHERE IT RUNS (cost choice, 2026-11-26): as a CHILD PROCESS of the Databricks App —
`app.py` spawns `python generator.py` at startup when env RUN_GENERATOR=1 and restarts
it if it exits (server/generator_supervisor.py). That puts the simulation on the app's
always-on compute instead of a separate 24/7 job. The same file still runs unchanged as
a Databricks job (spark_python_task) — the job is kept, paused, as a fallback.

SINGLE INSTANCE PER AIRPORT, enforced in the database: gate and runway state live in
Python dicts, so two generators would both hand out the same stand. At startup the
generator takes a Postgres session advisory lock for its airport and holds it for its
whole life; a second copy (job + app child, or a redeploy overlap) waits until the first
is gone instead of running alongside it.

Exit behaviour: these loops must never end, so ANY return from them is abnormal and
RAISES — the run is marked FAILED and the continuous job restarts it, rather than sitting
alive doing nothing. Never sys.exit(): serverless exec()s this file inside an IPython
kernel that reports SystemExit as an uncaught exception (that's what made the fetcher
report FAILED after a successful run). Per-tick errors are caught inside the loops and
logged — a transient DB blip must not kill the generator.
"""

import asyncio
import sys
from datetime import datetime, timezone


class _Timestamped:
    """stdout/stderr wrapper that prefixes every line with a UTC timestamp, so every
    log line in this process (ours and tracebacks) is time-stamped in the job log
    without touching each print()."""

    def __init__(self, stream):
        self._stream = stream
        self._line_start = True

    def write(self, text):
        out = []
        for part in text.splitlines(keepends=True):
            if self._line_start:
                out.append(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ "))
            out.append(part)
            self._line_start = part.endswith("\n")
        return self._stream.write("".join(out))

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


import os  # noqa: E402

# As the app's child, stdout goes straight into the app log, which already timestamps
# every line — so only add our own timestamps when running as a job.
if os.environ.get("GENERATOR_CHILD") != "1":
    sys.stdout = _Timestamped(sys.stdout)
    sys.stderr = _Timestamped(sys.stderr)

from server.db import db  # noqa: E402 — after the wrapper so import-time prints are stamped too
from server.simulator import broadcaster  # noqa: E402
from server.simulator import sensor_gen  # noqa: E402


async def _acquire_singleton_lock():
    """Block until this process holds the per-airport generator lock.

    A SESSION-level advisory lock lives exactly as long as the connection that took it,
    so the connection is held for the life of the process (returned, never released).
    If this process dies, Postgres drops the connection and the lock with it — so a
    crashed generator can never wedge the next one out."""
    from server import airports
    key = f"airport-simulator-generator:{airports.IATA}"
    pool = await db.get_pool()
    conn = await pool.acquire()
    waited = 0
    while not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", key):
        if waited % 60 == 0:
            print(f"[generator] another generator holds the {airports.IATA} lock — waiting "
                  f"(so two copies never run at once)")
        await asyncio.sleep(10)
        waited += 10
    print(f"[generator] singleton lock acquired for {airports.IATA}")
    return conn


async def main() -> int:
    from server import config
    print("[generator] starting — System A (position generator)")
    print(f"[generator] runtime: app={config.IS_DATABRICKS_APP} "
          f"job={config.IS_DATABRICKS_JOB} managed={config.IS_MANAGED_RUNTIME}")
    await db.get_pool()
    if db.is_demo_mode:
        # No Lakebase means nothing this process produces can be read by the app, so
        # running on would burn CPU writing to nowhere. Fail loudly — and say WHY,
        # because a bare exit code here is undiagnosable from the job UI.
        print("[generator] FATAL: no Lakebase connection (demo mode) — nowhere to "
              "publish. The password is resolved from the Databricks Secrets API "
              "(scope 'airport-sim', key 'lakebase-password'); that path requires "
              "config.IS_MANAGED_RUNTIME to be true and the job's identity to have "
              "READ on the scope. See the runtime flags logged above.")
        return 1

    lock_conn = await _acquire_singleton_lock()  # noqa: F841 — held (never released): closing it would drop the lock

    # The tick loop owns startup (schema creation, roster load, clearing rows orphaned
    # by a previous run), so start it first and let it fail loudly if the world can't
    # be loaded.
    tick_task = asyncio.create_task(broadcaster.run_loop(), name="tick")
    sensor_task = asyncio.create_task(sensor_gen.run_loop(), name="sensors")
    tasks = [tick_task, sensor_task]

    # Graceful stop (SIGTERM from the app's supervisor on shutdown/redeploy, or from the
    # job runner): flush the open telemetry windows before exiting, so a redeploy never
    # drops the last ≤15 min of sensor readings / sim events from history.
    stop = asyncio.Event()
    try:
        import signal
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    except (NotImplementedError, RuntimeError, ValueError):
        pass  # not the main thread (serverless job kernel) — no signal hook there
    stop_task = asyncio.create_task(stop.wait(), name="stop")

    # Any task returning/raising means the generator is no longer whole — surface it
    # instead of silently continuing with a degraded process.
    done, pending = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        print("[generator] SIGTERM — flushing telemetry files and stopping")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        from server.simulator import telemetry_files
        await telemetry_files.flush_all()
        await db.close()
        return 0
    stop_task.cancel()
    for t in done:
        name = t.get_name()
        if t.cancelled():
            print(f"[generator] task {name} was cancelled")
            continue
        exc = t.exception()
        print(f"[generator] task {name} exited: {exc!r}" if exc
              else f"[generator] task {name} returned unexpectedly")
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await db.close()
    return 1  # a completed task is always abnormal here — these loops never end


def _run() -> int:
    """Start `main()` regardless of the host's event-loop situation.

    Databricks serverless executes a `spark_python_task` inside an IPython kernel that
    ALREADY has a running asyncio loop on this thread, so plain `asyncio.run()` fails
    with "asyncio.run() cannot be called from a running event loop".

    Attaching to that loop is NOT safe either: `run_coroutine_threadsafe(...).result()`
    called from the loop's own thread deadlocks — the loop can never progress the
    coroutine because we're blocking it. So when a loop is already running we start our
    own loop on a DEDICATED THREAD and block the main thread on that thread instead.
    The kernel's loop is left untouched."""
    try:
        asyncio.get_running_loop()
        nested = True
    except RuntimeError:
        nested = False

    if not nested:
        return asyncio.run(main())

    print("[generator] event loop already running (serverless kernel) — "
          "starting a dedicated loop on its own thread")
    import threading
    result: dict = {}

    def _worker():
        try:
            result["rc"] = asyncio.run(main())
        except BaseException as e:  # noqa: BLE001
            result["exc"] = e

    t = threading.Thread(target=_worker, name="generator-loop", daemon=False)
    t.start()
    t.join()          # continuous job: this blocks for the life of the run
    if "exc" in result:
        raise result["exc"]
    return int(result.get("rc", 1))


if __name__ == "__main__":
    # Same rule as fetcher.py: never sys.exit(). Serverless exec()s this file inside an
    # IPython kernel, which reports the resulting SystemExit as an uncaught exception and
    # marks the run FAILED — even for exit code 0.
    #
    # This loop is not supposed to end, so ANY return here is abnormal and SHOULD fail the
    # run (a continuous job then restarts it). Raising is therefore both correct and the
    # right signal — unlike the fetcher, where returning 0 is the normal outcome.
    try:
        _rc = _run()
    except KeyboardInterrupt:
        print("[generator] interrupted")
    else:
        if _rc == 0:
            # Graceful stop (SIGTERM): telemetry was flushed. As the app's child, simply
            # end — exit code 0. (Never sys.exit() in a job kernel; see the note above.)
            print("[generator] stopped cleanly (telemetry flushed)")
        else:
            raise RuntimeError(
                f"[generator] loops exited (rc={_rc}) — the generator must run forever; "
                "see the logged task failure above")

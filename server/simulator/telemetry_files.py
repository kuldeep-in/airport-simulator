"""Time-bucketed files of raw telemetry → the airport's UC volume, for Delta history.

The generator produces two append-only telemetry streams — every sensor reading (5 s)
and every simulation event (touchdown, gate in/out, pushback, takeoff, …). Live
consumers read Lakebase; HISTORY is kept in Delta. To get there with no extra compute,
each stream is buffered in memory and written as ONE JSONL file per time window
(TELEMETRY_FILE_MINUTES, default 15, aligned to :00/:15/:30/:45):

    /Volumes/<catalog>/<schema>/raw_telemetry/<stream>/date=YYYY-MM-DD/<stream>_<IATA>_<YYYYMMDDTHHMM>Z_<run>.jsonl
                                                                    (window start UTC, writer run id)

The ingest job's SDP pipeline picks the files up with Auto Loader (every 4h, or on
demand). Fewer, larger files = faster Auto Loader listing and fewer small Delta files.

Durability: a window is held in memory until it closes, so the generator flushes every
open window on a graceful stop (SIGTERM — app shutdown / redeploy), see flush_all().
Only a hard crash can lose the open window; sensor readings are still in Lakebase
`sensor_readings` for 24 h, sim events are not.

Write path, chosen once:
  * /Volumes is mounted (Databricks job)  → local write to a temp name, then rename.
  * not mounted, but a Databricks runtime (the generator as the APP's child process)
    → Files API upload as the app's service principal (WRITE_VOLUME via the app's
    uc_securable resource in resources/app.yml). Uploads are atomic.
  * neither (local dev)                    → disabled, said once, nothing buffered.
"""
import asyncio
import io
import json
import os
from datetime import datetime, timezone

from server import airports


def _jsonable(v):
    return v.isoformat() if isinstance(v, datetime) else v


FILE_MINUTES = max(1, int(os.environ.get("TELEMETRY_FILE_MINUTES", "15")))
# Per-process writer id in every filename: after a graceful stop flushes a PARTIAL window,
# the restarted generator writes the rest of that window to a different file instead of
# overwriting (Files API uploads replace) the one already landed.
_RUN_ID = datetime.now(timezone.utc).strftime("%H%M%S") + f"{os.getpid() % 1000:03d}"
_SINKS: list["TelemetryFileSink"] = []


def _window_start(ts: datetime) -> datetime:
    return ts.replace(minute=(ts.minute // FILE_MINUTES) * FILE_MINUTES, second=0, microsecond=0)


class TelemetryFileSink:
    """Buffers records into FILE_MINUTES windows keyed by each record's OWN `ts`, and
    writes every window that has fully closed. (Bucketing by flush time put the reading
    taken at hh:mm:00.4 into the previous window's file.)"""

    def __init__(self, stream: str):
        self.stream = stream
        self.root = os.path.join(airports.TELEMETRY_VOLUME, stream)
        self._buckets: dict[datetime, list[dict]] = {}
        self._enabled: bool | None = None      # decided on first flush
        self._mode: str | None = None          # 'mount' | 'files_api'
        self._ws = None                         # WorkspaceClient (files_api mode)
        self.files_written = 0
        _SINKS.append(self)

    def add(self, record: dict) -> None:
        if self._enabled is False:
            return
        ts = record.get("ts") or datetime.now(timezone.utc)
        self._buckets.setdefault(_window_start(ts), []).append({k: _jsonable(v) for k, v in record.items()})

    def _check_enabled(self) -> bool:
        if self._enabled is None:
            from server import config
            if os.path.isdir(airports.TELEMETRY_VOLUME):
                self._mode = "mount"
            elif config.IS_MANAGED_RUNTIME:
                self._mode = "files_api"
                self._ws = config.get_workspace_client()
            self._enabled = self._mode is not None
            print(f"[telemetry] {self.stream} history → {self.root} "
                  f"({self._mode or 'disabled: no volume mount and not a Databricks runtime'})")
            if not self._enabled:
                self._buckets.clear()
        return self._enabled

    def _write(self, minute: datetime, rows: list[dict]) -> str:
        day_dir = os.path.join(self.root, f"date={minute:%Y-%m-%d}")
        path = os.path.join(day_dir, f"{self.stream}_{airports.IATA}_{minute:%Y%m%dT%H%M}Z_{_RUN_ID}.jsonl")
        data = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode()
        if self._mode == "files_api":
            self._ws.files.upload(path, io.BytesIO(data), overwrite=True)
            return path
        os.makedirs(day_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path

    async def maybe_flush(self, now: datetime | None = None, force: bool = False) -> None:
        """Write every window that has closed (all open windows too, when forced)."""
        now = now or datetime.now(timezone.utc)
        current = _window_start(now)
        done = sorted(m for m in self._buckets if force or m < current)
        if not done or not self._check_enabled():
            return
        for minute in done:
            rows = self._buckets.pop(minute)
            try:
                await asyncio.to_thread(self._write, minute, rows)
                self.files_written += 1
            except Exception as e:  # noqa: BLE001 — history is best-effort; live path is Lakebase
                print(f"[telemetry] {self.stream}: could not write {minute:%H:%M} file ({len(rows)} rows): {e}")


# Backwards-compatible name (the sink used to be per-minute).
MinuteFileSink = TelemetryFileSink


async def flush_all() -> None:
    """Write every open window of every stream now — call on graceful shutdown."""
    for sink in _SINKS:
        await sink.maybe_flush(force=True)

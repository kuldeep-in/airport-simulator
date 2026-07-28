"""Async Lakebase (Postgres) connection pool with OAuth token refresh.

On Databricks Apps the PGHOST / PGPORT / PGDATABASE / PGUSER env vars are
injected by the `database` app resource. The password is a short-lived OAuth
token obtained from the service principal, which must be refreshed
periodically (Lakebase tokens expire ~1h, we refresh every 45 min).

Locally, the same env vars can be set manually. If PGHOST is not present the
pool operates in "demo mode" and returns empty result sets so the app still
boots without a database.
"""

import os
import asyncio
import time
from typing import Optional

import asyncpg

from server.config import get_oauth_token, get_lakebase_token, get_secret_value, IS_DATABRICKS_APP

TOKEN_REFRESH_SECONDS = 45 * 60
_SECRETS_SCOPE = "airport-sim"


def _resolve_pg_var(env_key: str, secret_key: str, default: str) -> str:
    """Return env var value, falling back to Databricks Secrets API if the
    value looks like an unresolved template ({{...}}) or is empty."""
    val = os.environ.get(env_key, "")
    if val and not val.startswith("{{"):
        return val
    if IS_DATABRICKS_APP:
        fetched = get_secret_value(_SECRETS_SCOPE, secret_key)
        if fetched:
            return fetched
    return default


LAKEBASE_INSTANCE = "airport-simulator-db"
LAKEBASE_DATABASE = "hia_sim"
# Known host for this demo instance — used as fallback when env vars not injected.
LAKEBASE_HOST = "ep-example-0000.database.eastus.azuredatabricks.net"


class DatabasePool:
    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._demo_mode = False
        self._token_ts = 0.0
        self._lock = asyncio.Lock()

    @property
    def is_demo_mode(self) -> bool:
        return self._demo_mode

    async def get_pool(self) -> Optional[asyncpg.Pool]:
        if self._demo_mode:
            return None
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is not None:
                return self._pool
            pg_host = _resolve_pg_var("PGHOST", "lakebase-host", LAKEBASE_HOST)
            pg_password = _resolve_pg_var("PGPASSWORD", "lakebase-password", "")
            pg_user = _resolve_pg_var("PGUSER", "lakebase-user", "airport_app")
            pg_database = _resolve_pg_var("PGDATABASE", "lakebase-db", LAKEBASE_DATABASE)
            pg_port = int(os.environ.get("PGPORT", "5432"))
            if not pg_password:
                print("[db] password unavailable — demo mode.")
                self._demo_mode = True
                return None
            print(f"[db] connecting to {pg_host}/{pg_database} as {pg_user}")
            try:
                self._pool = await asyncpg.create_pool(
                    host=pg_host,
                    port=pg_port,
                    database=pg_database,
                    user=pg_user,
                    password=pg_password,
                    ssl="require",
                    min_size=2,
                    max_size=10,
                    command_timeout=30,
                )
                print(f"[db] Lakebase pool created — {pg_host}.")
            except Exception as e:  # noqa: BLE001
                print(f"[db] Lakebase connection failed ({e}) — demo mode.")
                self._demo_mode = True
                return None
        return self._pool

    async def maybe_refresh_token(self):
        """No-op for native password auth — connection pool is persistent."""
        pass

    async def execute(self, sql: str, *args):
        if self._demo_mode:
            return None
        pool = await self.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def executemany(self, sql: str, args_seq):
        if self._demo_mode:
            return None
        pool = await self.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            return await conn.executemany(sql, args_seq)

    async def fetch(self, sql: str, *args):
        if self._demo_mode:
            return []
        pool = await self.get_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:
            return await conn.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args):
        if self._demo_mode:
            return None
        pool = await self.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args):
        if self._demo_mode:
            return None
        pool = await self.get_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:
            return await conn.fetchval(sql, *args)

    async def run_script(self, sql_script: str):
        """Run a multi-statement DDL script."""
        if self._demo_mode:
            return
        pool = await self.get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(sql_script)

    async def close(self):
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


db = DatabasePool()


async def get_pool() -> Optional[asyncpg.Pool]:
    return await db.get_pool()

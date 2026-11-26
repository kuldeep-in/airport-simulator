# Databricks notebook source
# MAGIC %md
# MAGIC # Silver → Lakebase (synced table refresh)
# MAGIC
# MAGIC Final stage of the ingest job (once per airport). Pushes `silver_flight_schedule_<sfx>` (SCD1,
# MAGIC Change Data Feed on) into Lakebase as a **TRIGGERED synced table**.
# MAGIC
# MAGIC * **First run:** creates the synced table — UC `<catalog>.<schema>.flight_schedule_<sfx>`,
# MAGIC   Postgres `<database>.<schema>.flight_schedule_<sfx>` on the Lakebase instance. Creation
# MAGIC   performs the initial load itself.
# MAGIC * **Every later run:** starts an incremental update of the synced table's pipeline and
# MAGIC   waits for it, so the job only succeeds once Lakebase has the new data.
# MAGIC
# MAGIC Created here rather than as a DAB resource because its source table only exists after
# MAGIC the SDP pipeline's first run; a bundle deploy would otherwise fail on a fresh schema.
# MAGIC
# MAGIC * **After every sync:** re-grants `USAGE` + `SELECT` to `grant_to` (the app/generator's
# MAGIC   Postgres role). The synced table is owned by the sync service role, and default
# MAGIC   privileges can't be set on it — so this keeps read access intact even if the table is
# MAGIC   ever recreated.

# COMMAND ----------

# MAGIC %pip install --quiet "psycopg[binary]>=3.1"

# COMMAND ----------

dbutils.widgets.text("catalog", "classic_stable_kuldeep", "Catalog")
dbutils.widgets.text("schema", "airport_simulator_doh", "Schema")
dbutils.widgets.text("source_table", "silver_flight_schedule_doh", "Source (Delta) table")
dbutils.widgets.text("synced_table", "flight_schedule_doh", "Synced table name")
dbutils.widgets.text("instance", "airport-simulator-db", "Lakebase instance")
dbutils.widgets.text("database", "hia_sim", "Lakebase database")
dbutils.widgets.text("primary_key", "airport,flight_date,flight_number,direction", "Primary key (csv)")
dbutils.widgets.text("timeout_minutes", "30", "Wait timeout (min)")
dbutils.widgets.text("grant_to", "", "Postgres role to grant SELECT (optional)")

CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
SOURCE = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('source_table').strip()}"
SYNCED = f"{CATALOG}.{SCHEMA}.{dbutils.widgets.get('synced_table').strip()}"
INSTANCE = dbutils.widgets.get("instance").strip()
DATABASE = dbutils.widgets.get("database").strip()
PK = [c.strip() for c in dbutils.widgets.get("primary_key").split(",") if c.strip()]
TIMEOUT_S = int(dbutils.widgets.get("timeout_minutes")) * 60
GRANT_TO = dbutils.widgets.get("grant_to").strip()
print(f"source={SOURCE}  synced={SYNCED}  lakebase={INSTANCE}/{DATABASE}  pk={PK}")

# COMMAND ----------

import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.database import (
    NewPipelineSpec, SyncedDatabaseTable, SyncedTableSchedulingPolicy, SyncedTableSpec,
)

w = WorkspaceClient()


def get_synced():
    try:
        return w.database.get_synced_database_table(SYNCED)
    except (NotFound, ResourceDoesNotExist):
        return None


def wait_for_pipeline(pipeline_id: str, update_id: str | None = None) -> None:
    """Block until the given update (or the pipeline's latest update) finishes."""
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        if update_id:
            state = w.pipelines.get_update(pipeline_id, update_id).update.state.value
        else:
            p = w.pipelines.get(pipeline_id)
            latest = (p.latest_updates or [None])[0]
            state = latest.state.value if latest else "COMPLETED"
            update_id = latest.update_id if latest else None
        print(f"  pipeline {pipeline_id} update {update_id}: {state}")
        if state == "COMPLETED":
            return
        if state in ("FAILED", "CANCELED"):
            raise RuntimeError(f"Synced-table update {update_id} ended {state}")
        time.sleep(15)
    raise TimeoutError(f"Synced-table update did not finish within {TIMEOUT_S // 60} min")


# COMMAND ----------

st = get_synced()
if st is None:
    print(f"Creating synced table {SYNCED} (TRIGGERED) …")
    st = w.database.create_synced_database_table(SyncedDatabaseTable(
        name=SYNCED,
        database_instance_name=INSTANCE,
        logical_database_name=DATABASE,
        spec=SyncedTableSpec(
            source_table_full_name=SOURCE,
            primary_key_columns=PK,
            scheduling_policy=SyncedTableSchedulingPolicy.TRIGGERED,
            create_database_objects_if_missing=True,
            new_pipeline_spec=NewPipelineSpec(storage_catalog=CATALOG, storage_schema=SCHEMA),
        ),
    ))
    # Creation kicks off the initial load; wait for its pipeline to finish.
    for _ in range(40):
        pid = (get_synced().data_synchronization_status or None)
        pid = getattr(pid, "pipeline_id", None)
        if pid:
            break
        time.sleep(15)
    if not pid:
        raise RuntimeError("Synced table created but no pipeline id reported yet")
    wait_for_pipeline(pid)
else:
    pid = st.data_synchronization_status.pipeline_id
    print(f"Synced table exists (pipeline {pid}) — starting incremental refresh")
    try:
        upd = w.pipelines.start_update(pid)
        wait_for_pipeline(pid, upd.update_id)
    except Exception as e:  # noqa: BLE001
        # An update already in progress (e.g. a manual run) — just wait for it.
        if "active update" in str(e).lower() or "already" in str(e).lower():
            print(f"  update already running ({e}); waiting for it")
            wait_for_pipeline(pid)
        else:
            raise

st = get_synced()
print(f"Done. Lakebase table {DATABASE}.{SCHEMA}.{SYNCED.split('.')[-1]} — "
      f"state: {getattr(st.data_synchronization_status, 'detailed_state', '?')}")

# COMMAND ----------

# Re-grant read access for the app/generator role (idempotent).
if GRANT_TO:
    import uuid

    import psycopg
    from psycopg import sql

    inst = w.database.get_database_instance(INSTANCE)
    cred = w.database.generate_database_credential(request_id=str(uuid.uuid4()), instance_names=[INSTANCE])
    me = w.current_user.me().user_name
    table = SYNCED.split(".")[-1]
    with psycopg.connect(host=inst.read_write_dns, dbname=DATABASE, user=me,
                         password=cred.token, sslmode="require", autocommit=True) as conn:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(SCHEMA), sql.Identifier(GRANT_TO)))
        conn.execute(sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
            sql.Identifier(SCHEMA), sql.Identifier(table), sql.Identifier(GRANT_TO)))
        ok = conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT')",
                          (GRANT_TO, f"{SCHEMA}.{table}")).fetchone()[0]
    if not ok:
        raise RuntimeError(f"{GRANT_TO} still lacks SELECT on {SCHEMA}.{table}")
    print(f"Granted USAGE on {SCHEMA} + SELECT on {SCHEMA}.{table} to {GRANT_TO}")

# Databricks notebook source
# MAGIC %md
# MAGIC # Score — departure delay risk (hourly job `[dev] airport-simulator-score-delay-risk`, :15 UTC)
# MAGIC
# MAGIC Scores every departure in the next `horizon_hours` with the **`champion`** model from
# MAGIC Unity Catalog and writes:
# MAGIC * `gold_delay_risk_<sfx>` — CURRENT score per upcoming departure (MERGE upsert, CDF on) →
# MAGIC   synced to Lakebase `delay_risk_<sfx>` for the app (board column, dashboard panel, alerts);
# MAGIC * `gold_delay_risk_log_<sfx>` — every prediction ever made (append-only);
# MAGIC * `v_delay_risk_outcomes_<sfx>` — each departure's 2-h-ahead prediction vs what actually happened.
# MAGIC
# MAGIC No champion yet → exits cleanly, so the ingest job never fails because of the ML step.

# COMMAND ----------

# MAGIC %pip install --quiet "mlflow>=3.1" "scikit-learn>=1.3"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "classic_stable_kuldeep")
dbutils.widgets.text("schema", "airport_simulator_doh")
dbutils.widgets.text("suffix", "doh")
dbutils.widgets.text("horizon_hours", "6")
dbutils.widgets.text("backtest_hours_ago", "0")   # >0: score as of N h ago vs what happened; writes nothing
CATALOG = dbutils.widgets.get("catalog"); SCHEMA = dbutils.widgets.get("schema")
SFX = dbutils.widgets.get("suffix"); HORIZON_H = float(dbutils.widgets.get("horizon_hours"))
BACKTEST_H = float(dbutils.widgets.get("backtest_hours_ago") or 0)
MODEL_NAME = f"{CATALOG}.{SCHEMA}.delay_risk_model"
CURRENT = f"{CATALOG}.{SCHEMA}.gold_delay_risk_{SFX}"
LOG = f"{CATALOG}.{SCHEMA}.gold_delay_risk_log_{SFX}"
OUTCOMES = f"{CATALOG}.{SCHEMA}.v_delay_risk_outcomes_{SFX}"
HIGH, MEDIUM = 0.5, 0.25          # risk bands (base rate is ~13%, so 0.5 ≈ 4× normal)

# COMMAND ----------

import json, os, sys, time
import mlflow
from mlflow import MlflowClient
sys.path.append(os.getcwd())
from features import NUMERIC_FEATURES, build_features, reasons

mlflow.set_registry_uri("databricks-uc")
client = MlflowClient(registry_uri="databricks-uc")
try:
    mv = client.get_model_version_by_alias(MODEL_NAME, "champion")
except Exception as e:  # noqa: BLE001
    print(f"No champion model yet ({str(e)[:120]}) — skipping scoring.")
    dbutils.notebook.exit(json.dumps({"scored": 0, "reason": "no champion"}))
overall = float((mv.tags or {}).get("overall_late_rate", 0.13))
model = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@champion")
print(f"champion: v{mv.version}  ·  overall late rate {overall:.1%}")

# COMMAND ----------

import numpy as np, pandas as pd
from pyspark.sql import functions as F

now_ts = int(time.time()) - int(BACKTEST_H * 3600)
pairs = json.load(open(os.path.join(os.getcwd(), "..", "data", "turnarounds.json"))).get("pairs", [])
feats = build_features(spark, CATALOG, SCHEMA, SFX, pairs, now_ts=now_ts)
up = feats.where((F.col("ts") >= now_ts - 900) & (F.col("ts") <= now_ts + int(HORIZON_H * 3600))).toPandas()
print(f"departures to score: {len(up)}")
if up.empty:
    dbutils.notebook.exit(json.dumps({"scored": 0, "reason": "no departures in horizon"}))

pred = np.asarray(model.predict(up[NUMERIC_FEATURES].astype(float)))
up["risk"] = pred[:, 1] if pred.ndim == 2 else pred
up["risk_band"] = np.where(up.risk >= HIGH, "high", np.where(up.risk >= MEDIUM, "medium", "low"))

if BACKTEST_H > 0:
    # Compare "as of N hours ago" predictions with what actually happened (silver).
    act = (spark.table(f"{CATALOG}.{SCHEMA}.silver_flight_schedule_{SFX}")
           .where("direction = 'departure' AND delay_minutes IS NOT NULL")
           .select("flight_date", "flight_number", F.col("delay_minutes").alias("actual_delay")).toPandas())
    bt = up.merge(act, on=["flight_date", "flight_number"], how="inner")
    bt = bt[bt.ts < int(time.time()) - 1800]                       # only flights that have operated
    bt["late"] = (bt.actual_delay >= 15).astype(int)
    res = {"as_of_hours_ago": BACKTEST_H, "flights": int(len(bt)), "actual_late_share": round(float(bt.late.mean()), 3) if len(bt) else None,
           "mean_predicted": round(float(bt.risk.mean()), 3) if len(bt) else None,
           "bands": {b: {"n": int((bt.risk_band == b).sum()), "actual_late": round(float(bt[bt.risk_band == b].late.mean()), 3) if (bt.risk_band == b).any() else None}
                     for b in ("high", "medium", "low")}}
    try:
        from sklearn.metrics import roc_auc_score
        res["auc"] = round(float(roc_auc_score(bt.late, bt.risk)), 3) if bt.late.nunique() == 2 else None
    except Exception:  # noqa: BLE001
        pass
    print(json.dumps(res, indent=1))
    print(bt.sort_values("risk", ascending=False)[["flight_number", "destination", "risk", "actual_delay"]].head(12).to_string(index=False))
    dbutils.notebook.exit(json.dumps(res))
up["reasons"] = [" · ".join(reasons(r, overall)) for r in up.to_dict("records")]
up["model_version"] = int(mv.version)
up["scored_at"] = pd.Timestamp.utcfromtimestamp(now_ts)
up["feature_time"] = pd.to_datetime(up.ft, unit="s")
out_cols = ["airport", "flight_date", "flight_number", "direction", "airline", "destination", "aircraft_type",
            "scheduled_utc", "sched_time_utc", "risk", "risk_band", "reasons", "model_version", "scored_at",
            "feature_time"] + NUMERIC_FEATURES
sdf = spark.createDataFrame(up[out_cols])
sdf = (sdf.withColumn("scored_at", F.col("scored_at").cast("timestamp"))
          .withColumn("feature_time", F.col("feature_time").cast("timestamp"))
          .withColumn("risk", F.col("risk").cast("double"))
          .withColumn("model_version", F.col("model_version").cast("int")))
for c in NUMERIC_FEATURES:                     # stable types run-to-run (MERGE needs them)
    sdf = sdf.withColumn(c, F.col(c).cast("double"))

# COMMAND ----------

# Append-only log (every prediction) — the basis for measuring the model against reality.
sdf.write.mode("append").option("mergeSchema", "true").saveAsTable(LOG)

# Current scores: one row per upcoming departure, MERGE so the synced table sees clean CDF.
cols = ", ".join(f"{c} {t}" for c, t in sdf.dtypes)
spark.sql(f"CREATE TABLE IF NOT EXISTS {CURRENT} ({cols}) "
          f"TBLPROPERTIES (delta.enableChangeDataFeed = true) "
          f"COMMENT 'Current departure delay-risk scores (P of leaving 15+ min late, asked 2 h ahead). "
          f"One row per departure in the next {HORIZON_H:.0f} h. Written by ml/score_delay_risk.py; synced to Lakebase.'")
sdf.createOrReplaceTempView("new_scores")
key = "t.airport = s.airport AND t.flight_date = s.flight_date AND t.flight_number = s.flight_number AND t.direction = s.direction"
spark.sql(f"MERGE INTO {CURRENT} t USING new_scores s ON {key} WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")
spark.sql(f"DELETE FROM {CURRENT} WHERE sched_time_utc < timestamp_seconds({now_ts - 3600})")

# Outcomes: for each departure, the latest prediction made at least 2 h ahead (else the
# earliest one) vs its actual delay from the governed silver table.
spark.sql(f"""
CREATE OR REPLACE VIEW {OUTCOMES}
COMMENT 'Delay-risk model vs reality: each departure''s 2-h-ahead prediction joined to its actual delay (silver).'
AS WITH ranked AS (
  SELECT l.*, row_number() OVER (
           PARTITION BY airport, flight_date, flight_number
           ORDER BY CASE WHEN scored_at <= sched_time_utc - INTERVAL 2 HOURS THEN 0 ELSE 1 END,
                    CASE WHEN scored_at <= sched_time_utc - INTERVAL 2 HOURS THEN -unix_timestamp(scored_at)
                         ELSE unix_timestamp(scored_at) END) AS rn
  FROM {LOG} l)
SELECT r.airport, r.flight_date, r.flight_number, r.airline, r.destination, r.sched_time_utc,
       r.scored_at, round((unix_timestamp(r.sched_time_utc) - unix_timestamp(r.scored_at)) / 3600.0, 1) AS lead_hours,
       r.risk, r.risk_band, r.reasons, r.model_version,
       s.delay_minutes AS actual_delay_minutes,
       CASE WHEN s.delay_minutes IS NULL THEN NULL ELSE s.delay_minutes >= 15 END AS actually_late
FROM ranked r
LEFT JOIN {CATALOG}.{SCHEMA}.silver_flight_schedule_{SFX} s
  ON s.airport = r.airport AND s.flight_date = r.flight_date AND s.flight_number = r.flight_number AND s.direction = 'departure'
WHERE r.rn = 1""")

summary = up.risk_band.value_counts().to_dict()
print(f"scored {len(up)} departures with v{mv.version}: {summary}")
print(up.sort_values("risk", ascending=False)[["flight_number", "destination", "sched_time_utc", "risk", "reasons"]].head(8).to_string(index=False))
dbutils.notebook.exit(json.dumps({"scored": len(up), "model_version": int(mv.version), "bands": summary}))

# Databricks notebook source
# MAGIC %md
# MAGIC # Train — departure delay-risk model
# MAGIC
# MAGIC Predicts **P(departure leaves 15+ min late)**, asked **2 h before** scheduled departure.
# MAGIC Features: `ml/features.py` (shared with scoring). Data: governed silver flight table + the
# MAGIC 122-day archive, both in Unity Catalog. Tracked in MLflow; registered in Unity Catalog as
# MAGIC `<catalog>.<schema>.delay_risk_model`. The **`champion`** alias moves only if the new model
# MAGIC beats the simple airline-history baseline on the most recent 14 days (time-based split).

# COMMAND ----------

# MAGIC %pip install --quiet "mlflow>=3.1" "scikit-learn>=1.3"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "classic_stable_kuldeep")
dbutils.widgets.text("schema", "airport_simulator_doh")
dbutils.widgets.text("suffix", "doh")
dbutils.widgets.text("experiment", "")
dbutils.widgets.text("test_days", "14")
CATALOG = dbutils.widgets.get("catalog"); SCHEMA = dbutils.widgets.get("schema")
SFX = dbutils.widgets.get("suffix"); TEST_DAYS = int(dbutils.widgets.get("test_days"))
EXPERIMENT = dbutils.widgets.get("experiment").strip()
MODEL_NAME = f"{CATALOG}.{SCHEMA}.delay_risk_model"
FEATURES_TABLE = f"{CATALOG}.{SCHEMA}.gold_departure_features_{SFX}"

# COMMAND ----------

import json, os, sys, time
sys.path.append(os.getcwd())                       # ml/ — shared feature code
from features import NUMERIC_FEATURES, build_features

pairs = json.load(open(os.path.join(os.getcwd(), "..", "data", "turnarounds.json"))).get("pairs", [])
now_ts = int(time.time())
feats = build_features(spark, CATALOG, SCHEMA, SFX, pairs)
# Labelled departures that have already operated (1 h grace so the final time has landed).
train_df = feats.where(f"label IS NOT NULL AND ts < {now_ts - 3600}")
train_df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(FEATURES_TABLE)
spark.sql(f"COMMENT ON TABLE {FEATURES_TABLE} IS 'Delay-risk training features: one row per operated "
          f"departure, features as known 2 h before departure, label = left 15+ min late. Built by ml/train_delay_risk.py.'")
pdf = spark.table(FEATURES_TABLE).toPandas().sort_values("ts")
print(f"training rows: {len(pdf):,}  ·  delayed share: {pdf.label.mean():.1%}  ·  "
      f"{pdf.sched_time_utc.min()} → {pdf.sched_time_utc.max()}")

# COMMAND ----------

import mlflow, numpy as np, pandas as pd
from mlflow.models import infer_signature
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

cut = pdf.ts.max() - TEST_DAYS * 86400
tr, te = pdf[pdf.ts < cut], pdf[pdf.ts >= cut]
X_tr, y_tr = tr[NUMERIC_FEATURES].astype(float), tr.label.astype(int)
X_te, y_te = te[NUMERIC_FEATURES].astype(float), te.label.astype(int)

mlflow.set_registry_uri("databricks-uc")
if EXPERIMENT:
    mlflow.set_experiment(EXPERIMENT)
with mlflow.start_run(run_name=f"delay-risk-{SFX}") as run:
    model = HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
                                           l2_regularization=1.0, early_stopping=True,
                                           validation_fraction=0.15, random_state=42)
    model.fit(X_tr, y_tr)
    p_te = model.predict_proba(X_te)[:, 1]
    base = te.airline_late_rate_28d.fillna(tr.label.mean())           # baseline: airline history alone
    m = {
        "test_auc": roc_auc_score(y_te, p_te),
        "test_pr_auc": average_precision_score(y_te, p_te),
        "test_brier": brier_score_loss(y_te, p_te),
        "baseline_auc": roc_auc_score(y_te, base),
        "test_rows": len(te), "train_rows": len(tr),
        "test_delayed_share": float(y_te.mean()),
        "overall_late_rate": float(pdf.label.mean()),
    }
    hi = p_te >= 0.5
    m["precision_at_0_5"] = float(y_te[hi].mean()) if hi.any() else 0.0
    m["recall_at_0_5"] = float(hi[y_te == 1].mean()) if (y_te == 1).any() else 0.0
    mlflow.log_metrics(m)
    mlflow.log_params({"features": ",".join(NUMERIC_FEATURES), "lead_hours": 2, "late_minutes": 15,
                       "test_days": TEST_DAYS, "model": "HistGradientBoostingClassifier"})
    # Permutation-free importance proxy: AUC drop when a feature is shuffled (cheap, model-agnostic).
    rng = np.random.default_rng(0); imp = {}
    for c in NUMERIC_FEATURES:
        Xs = X_te.copy(); Xs[c] = rng.permutation(Xs[c].values)
        imp[c] = round(m["test_auc"] - roc_auc_score(y_te, model.predict_proba(Xs)[:, 1]), 4)
    mlflow.log_dict(dict(sorted(imp.items(), key=lambda kv: -kv[1])), "feature_importance.json")
    sig = infer_signature(X_tr.head(50), model.predict_proba(X_tr.head(50))[:, 1])
    info = mlflow.sklearn.log_model(model, name="model", signature=sig, input_example=X_tr.head(5),
                                    pyfunc_predict_fn="predict_proba", registered_model_name=MODEL_NAME)
    print(json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in m.items()}, indent=1))
    print("feature importance (AUC drop):", dict(sorted(imp.items(), key=lambda kv: -kv[1])[:6]))

# COMMAND ----------

from mlflow import MlflowClient
client = MlflowClient(registry_uri="databricks-uc")
version = info.registered_model_version
client.set_registered_model_tag(MODEL_NAME, "use_case", "departure delay risk (15+ min, 2 h lead)")
client.set_model_version_tag(MODEL_NAME, version, "test_auc", f"{m['test_auc']:.4f}")
client.set_model_version_tag(MODEL_NAME, version, "overall_late_rate", f"{m['overall_late_rate']:.4f}")
if m["test_auc"] > m["baseline_auc"]:
    client.set_registered_model_alias(MODEL_NAME, "champion", version)
    verdict = f"v{version} is CHAMPION (AUC {m['test_auc']:.3f} > baseline {m['baseline_auc']:.3f})"
else:
    verdict = f"v{version} NOT promoted (AUC {m['test_auc']:.3f} ≤ baseline {m['baseline_auc']:.3f}); champion unchanged"
print(verdict)
dbutils.notebook.exit(json.dumps({"version": version, **{k: round(v, 4) if isinstance(v, float) else v for k, v in m.items()},
                                  "verdict": verdict}))

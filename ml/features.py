"""Delay-risk features — ONE definition shared by training and scoring.

Question the model answers: "Will this departure leave 15+ minutes late?", asked
2 HOURS BEFORE its scheduled departure (the feature time, ft = sched − 2 h; for a flight
being scored now that is still more than 2 h out, ft = now).

Every feature uses only information that existed at ft:
  * schedule facts (airline, destination, aircraft class, local hour, weekday, how many
    departures are scheduled in the same hour) — known days ahead;
  * history rates over the 28 days BEFORE ft (airline / route / hour-of-day share of
    departures that left 15+ min late) — only flights scheduled before ft count;
  * what the airport is doing in the 2 hours before ft (share of departures leaving
    late, average arrival delay) — flights scheduled in [ft − 2 h, ft);
  * for configured turnaround pairs (data/turnarounds.json), the linked inbound
    flight's arrival delay.

Label: actual delay (revised − scheduled) ≥ 15 minutes, for departures that have a
revised time. Source data: the 122-day archive (flight_schedule_legacy_doh) unioned with
the live silver table (silver_flight_schedule_doh); silver wins on overlap.

Point-in-time correctness: a flight's outcome only feeds another flight's features once
it has actually happened (its revised/actual time < ft) — identical rule in training and
scoring (see build_features). Remaining approximation: the linked-inbound delay uses the
inbound's final delay where the live system would have its ETA estimate.
"""
from pyspark.sql import DataFrame, SparkSession, functions as F, Window

LEAD_SEC = 2 * 3600            # predict 2 h before departure
HIST_SEC = 28 * 24 * 3600      # history-rate window
RECENT_SEC = 2 * 3600          # "airport right now" window before ft
LATE_MIN = 15                  # delayed = 15+ minutes

NUMERIC_FEATURES = [
    "hour_local", "dow", "is_wide", "is_a380",
    "airline_late_rate_28d", "airline_n_28d",
    "route_late_rate_28d", "route_n_28d",
    "hour_late_rate_28d",
    "airport_dep_late_rate_prev2h", "airport_arr_avg_delay_prev2h",
    "deps_same_hour",
    "has_inbound_link", "inbound_delay_min",
]
_WIDE = ("A359", "A35K", "A333", "B77W", "B789", "B788")


def base_flights(spark: SparkSession, catalog: str, schema: str, sfx: str) -> DataFrame:
    """Archive ∪ silver, one row per flight (silver wins), with delay + label."""
    cols = ["airport", "flight_date", "flight_number", "direction", "airline", "aircraft_type",
            "origin", "destination", "scheduled_utc", "sched_time_utc", "revised_time_utc"]
    silver = (spark.table(f"{catalog}.{schema}.silver_flight_schedule_{sfx}").select(*cols)
              .withColumn("_src", F.lit(1)))
    tables = [silver]
    legacy_name = f"{catalog}.{schema}.flight_schedule_legacy_{sfx}"
    if spark.catalog.tableExists(legacy_name):
        tables.append(spark.table(legacy_name).select(*cols).withColumn("_src", F.lit(0)))
    df = tables[0]
    for t in tables[1:]:
        df = df.unionByName(t)
    key = ["airport", "flight_date", "flight_number", "direction"]
    w = Window.partitionBy(*key).orderBy(F.col("_src").desc())
    df = (df.withColumn("_rn", F.row_number().over(w)).where("_rn = 1").drop("_rn", "_src")
          .where(F.col("sched_time_utc").isNotNull()))
    delay = (F.col("revised_time_utc").cast("long") - F.col("sched_time_utc").cast("long")) / 60.0
    return (df.withColumn("delay_min", F.when(F.col("revised_time_utc").isNotNull(), delay))
              .withColumn("label", F.when(F.col("delay_min").isNotNull(),
                                          (F.col("delay_min") >= LATE_MIN).cast("int")))
              .withColumn("ts", F.col("sched_time_utc").cast("long")))


def build_features(spark: SparkSession, catalog: str, schema: str, sfx: str,
                   turnaround_pairs: list[dict] | None = None, now_ts: int | None = None) -> DataFrame:
    """Features for EVERY departure (training rows have a label; future ones don't).

    `now_ts` (epoch seconds) caps the feature time for flights being scored now:
    ft = min(sched − 2 h, now). Training passes None (ft = sched − 2 h)."""
    f = base_flights(spark, catalog, schema, sfx)
    ft = F.col("ts") - F.lit(LEAD_SEC)
    if now_ts is not None:
        ft = F.least(ft, F.lit(int(now_ts)))
    is_dep = F.col("direction") == "departure"
    f = (f.withColumn("ft", ft)
          .withColumn("hour_local", F.hour(F.from_utc_timestamp("sched_time_utc", "Asia/Qatar")))
          .withColumn("dow", F.dayofweek(F.from_utc_timestamp("sched_time_utc", "Asia/Qatar")))
          .withColumn("is_wide", F.col("aircraft_type").isin(*_WIDE).cast("int"))
          .withColumn("is_a380", (F.col("aircraft_type") == "A388").cast("int"))
          # When the outcome became KNOWN: the actual (revised) departure/arrival time.
          .withColumn("ot", F.col("revised_time_utc").cast("long")))

    # POINT-IN-TIME features. A flight's outcome may only inform another flight's
    # features once it has actually HAPPENED (ot < ft) — the same rule at training and
    # at scoring, so there is no train/serve skew and no leakage. Implemented as one
    # timeline: outcome EVENTS at their actual time + one QUERY row per departure at its
    # feature time; windows over the timeline see only events strictly before each query.
    # (At scoring time, flights that haven't operated have ot in the future → excluded.)
    common = ["airline", "destination", "hour_local"]
    q = (f.where(is_dep)
          .select(F.lit("q").alias("kind"), F.col("ft").alias("t"), *common,
                  "airport", "flight_date", "flight_number", "direction",
                  F.lit(None).cast("int").alias("e_label"), F.lit(None).cast("int").alias("e_cnt"),
                  F.lit(None).cast("double").alias("e_arr_delay")))
    ev_dep = (f.where(is_dep & F.col("label").isNotNull() & F.col("ot").isNotNull())
               .select(F.lit("d").alias("kind"), F.col("ot").alias("t"), *common,
                       F.lit(None).cast("string").alias("airport"), F.lit(None).cast("date").alias("flight_date"),
                       F.lit(None).cast("string").alias("flight_number"), F.lit(None).cast("string").alias("direction"),
                       F.col("label").alias("e_label"), F.lit(1).alias("e_cnt"),
                       F.lit(None).cast("double").alias("e_arr_delay")))
    ev_arr = (f.where(~is_dep & F.col("delay_min").isNotNull() & F.col("ot").isNotNull())
               .select(F.lit("a").alias("kind"), F.col("ot").alias("t"),
                       F.lit(None).cast("string").alias("airline"), F.lit(None).cast("string").alias("destination"),
                       F.lit(None).cast("int").alias("hour_local"),
                       F.lit(None).cast("string").alias("airport"), F.lit(None).cast("date").alias("flight_date"),
                       F.lit(None).cast("string").alias("flight_number"), F.lit(None).cast("string").alias("direction"),
                       F.lit(None).cast("int").alias("e_label"), F.lit(None).cast("int").alias("e_cnt"),
                       F.col("delay_min").alias("e_arr_delay")))
    tl = q.unionByName(ev_dep).unionByName(ev_arr)

    def before(part, span):            # events in [t − span, t)
        w = Window.orderBy("t").rangeBetween(-span, -1)
        return w.partitionBy(*part) if part else w
    for name, part in (("airline", ["airline"]), ("route", ["destination"]), ("hour", ["hour_local"])):
        w = before(part, HIST_SEC)
        tl = (tl.withColumn(f"{name}_late_rate_28d", F.sum("e_label").over(w) / F.sum("e_cnt").over(w))
                .withColumn(f"{name}_n_28d", F.coalesce(F.sum("e_cnt").over(w), F.lit(0))))
    wr = before([], RECENT_SEC)
    tl = (tl.withColumn("airport_dep_late_rate_prev2h", F.sum("e_label").over(wr) / F.sum("e_cnt").over(wr))
            .withColumn("airport_arr_avg_delay_prev2h", F.avg("e_arr_delay").over(wr)))
    pit = tl.where("kind = 'q'").drop("kind", "t", "e_label", "e_cnt", "e_arr_delay", *common)

    # Schedule-only feature (known days ahead): departures scheduled within ±30 min.
    sched = f.withColumn("deps_same_hour",
                         F.sum(F.when(is_dep, 1).otherwise(0)).over(Window.orderBy("ts").rangeBetween(-1800, 1800)))
    f = sched.join(pit, ["airport", "flight_date", "flight_number", "direction"], "left")

    deps = f.where(is_dep)
    # Turnaround link: linked inbound arriving 0–5 h before the departure.
    pairs = turnaround_pairs or []
    if pairs:
        pdf = spark.createDataFrame([(p["arrival"].upper(), p["departure"].upper()) for p in pairs],
                                    "arr_fn string, dep_fn string")
        arr = (f.where(~is_dep).select(F.col("flight_number").alias("arr_fn"),
                                        F.col("ts").alias("arr_ts"), F.col("delay_min").alias("arr_delay_min")))
        link = (deps.select("airport", "flight_date", "flight_number", "ts")
                .join(pdf, F.col("flight_number") == F.col("dep_fn"))
                .join(arr, "arr_fn")
                .where((F.col("arr_ts") < F.col("ts")) & (F.col("arr_ts") >= F.col("ts") - 5 * 3600))
                .groupBy("airport", "flight_date", "flight_number")
                .agg(F.max("arr_delay_min").alias("inbound_delay_min"), F.lit(1).alias("has_inbound_link")))
        deps = deps.join(link, ["airport", "flight_date", "flight_number"], "left")
    else:
        deps = deps.withColumn("inbound_delay_min", F.lit(None).cast("double")).withColumn("has_inbound_link", F.lit(0))

    deps = (deps.withColumn("has_inbound_link", F.coalesce("has_inbound_link", F.lit(0)))
                .withColumn("inbound_delay_min", F.coalesce("inbound_delay_min", F.lit(0.0))))
    keep = ["airport", "flight_date", "flight_number", "direction", "airline", "destination",
            "aircraft_type", "scheduled_utc", "sched_time_utc", "revised_time_utc", "delay_min", "label",
            "ts", "ft"] + [c for c in NUMERIC_FEATURES if c not in ("has_inbound_link", "inbound_delay_min")] \
           + ["has_inbound_link", "inbound_delay_min"]
    return deps.select(*keep)


def reasons(row: dict, overall_late_rate: float, top_n: int = 3) -> list[str]:
    """Plain-language drivers for one scored departure — the factors furthest above
    normal, so the ops team sees WHY a flight is flagged, not just a number."""
    base = max(overall_late_rate, 0.01)
    cands = []
    def add(score, text):
        if score > 1.0:
            cands.append((score, text))
    r = row.get
    if r("airline_late_rate_28d") is not None and (r("airline_n_28d") or 0) >= 10:
        add(r("airline_late_rate_28d") / base, f"{r('airline')} departures late {r('airline_late_rate_28d'):.0%} (28 d)")
    if r("route_late_rate_28d") is not None and (r("route_n_28d") or 0) >= 5:
        add(r("route_late_rate_28d") / base, f"Route to {r('destination')} late {r('route_late_rate_28d'):.0%}")
    if r("hour_late_rate_28d") is not None:
        add(r("hour_late_rate_28d") / base, f"{int(r('hour_local')):02d}:00 slot late {r('hour_late_rate_28d'):.0%}")
    if r("airport_dep_late_rate_prev2h") is not None:
        add(r("airport_dep_late_rate_prev2h") / base, f"Departures running late now ({r('airport_dep_late_rate_prev2h'):.0%})")
    if (r("airport_arr_avg_delay_prev2h") or 0) >= 10:
        add(r("airport_arr_avg_delay_prev2h") / 10.0, f"Arrivals running {r('airport_arr_avg_delay_prev2h'):.0f} min late")
    if r("has_inbound_link") and (r("inbound_delay_min") or 0) >= 10:
        add(r("inbound_delay_min") / 10.0 + 1, f"Inbound aircraft {r('inbound_delay_min'):.0f} min late")
    if (r("deps_same_hour") or 0) >= 20:
        add(r("deps_same_hour") / 20.0, f"Peak hour: {int(r('deps_same_hour'))} departures")
    return [t for _, t in sorted(cands, reverse=True)[:top_n]]

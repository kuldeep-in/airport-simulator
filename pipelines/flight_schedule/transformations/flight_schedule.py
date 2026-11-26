"""SDP pipeline — AeroDataBox raw JSON → bronze → silver, for EVERY configured airport.

One pipeline serves all airports. Each airport's tables are published into its OWN UC
schema (multi-schema publishing), read from its OWN volume, and every output carries the
airport suffix (<sfx> = lower-case IATA, e.g. _doh / _lhr):

    /Volumes/<catalog>/<schema>/raw_api/**/*.json              (landed by notebooks/fetch_aerodatabox.py)
      └─▶ <schema>.bronze_aerodatabox_raw_<sfx>                 one row per landed file, doc kept as VARIANT
            └─▶ <schema>.silver_flight_observation_<sfx>        one row per flight per fetch (append)
                  ├─▶ <schema>.silver_flight_schedule_<sfx>      SCD1 current row per flight (CDF on) → Lakebase sync
                  └─▶ <schema>.silver_flight_schedule_history_<sfx>  SCD2 every revision (analytics)

    /Volumes/<catalog>/<schema>/raw_telemetry/**/*.jsonl       (written by the generator, 1 file / 15 min / stream)
      ├─▶ <schema>.sensor_readings_<sfx>    EVERY 5 s reading of every sensor (temperature, pax density)
      └─▶ <schema>.sim_events_<sfx>         touchdown · gate in/out · pushback · hold-short · takeoff · turnaround

Pipeline configuration (resources/ingest.yml):
    catalog   UC catalog
    airports  JSON list: [{"iata": "DOH", "schema": "airport_simulator_doh", "hub": "QR",
                           "city": "Doha"}, …]   (city = name shown for the HOME airport)

Parsing reproduces the retired Python fetcher (live_schedule.parse_arrivals /
parse_departures and flight_store.upsert — see git history), so the synced Lakebase
table kept the exact shape the app and generator already consumed:
  * flight_number = callsign with whitespace removed, upper-cased ('QR 1238' → 'QR1238')
  * scheduled_utc = revised-or-scheduled (effective, drives the sim clock)
  * flight_date   = UTC date of the ORIGINAL scheduled time (stable key across delays)
  * cancelled / cargo / codeshared dropped; rows with no IATA for the other end dropped
  * aircraft model → short type code (same ordered rules as map_model_to_icao)
  * COALESCE semantics (a later fetch missing tail/belt/name keeps the known value)
    → ignore_null_updates on the CDC flows
  * delay_minutes = revised_time_utc − sched_time_utc in minutes (both directions)
  * origin_name / destination_name for the HOME airport = its configured city (e.g. Doha);
    the other end keeps the API's airport name
  * api_status = the latest status the API reported (for a past flight that's normally
    its final Departed/Arrived). Deliberately NOT flight_store's "only refresh future
    flights" rule: under CDC that left flights first seen after operating with no status.
"""
import json

import dlt
from pyspark.sql import functions as F

CATALOG = spark.conf.get("catalog")                  # noqa: F821 — `spark` is injected by SDP
AIRPORTS = json.loads(spark.conf.get("airports"))    # noqa: F821

KEYS = ["airport", "flight_date", "flight_number", "direction"]
TRACK_HISTORY = ["scheduled_utc", "revised_time_utc", "api_gate", "api_status",
                 "aircraft_reg", "aircraft_type", "baggage_belt"]

# Ordered: first matching pattern wins.
_MODEL_RULES = [
    ("A380|A388", "A388"), ("A350-?1000|A35K", "A35K"), ("A350", "A359"),
    ("A330|A33[0-9]", "A333"), ("777", "B77W"), ("787-?9|B789", "B789"),
    ("787-?8|B788|787", "B788"), ("767", "B788"), ("747", "B77W"),
    ("A321|A32[1-9]|A21N", "A21N"), ("A320|A319|A318", "A320"),
    ("737|B73[0-9]|MAX", "A320"), ("E19|E17|E29|CRJ|RJ|A220", "A320"),
]
AIRCRAFT_TYPE_SQL = ("CASE " + " ".join(
    f"WHEN upper(aircraft_model) RLIKE '{p}' THEN '{c}'" for p, c in _MODEL_RULES
) + " ELSE 'A320' END")


def _ts(col: str) -> str:
    """AeroDataBox 'YYYY-MM-DD HH:MMZ' (sometimes with seconds) → UTC timestamp."""
    v = f"trim(replace({col}::string, 'Z', ''))"
    return (f"coalesce(try_to_timestamp({v}, 'yyyy-MM-dd HH:mm'), "
            f"try_to_timestamp({v}, 'yyyy-MM-dd HH:mm:ss'))")


# Auto CDC API name changed across releases; support both.
_auto_cdc = getattr(dlt, "create_auto_cdc_flow", None) or dlt.apply_changes


def define_airport(iata: str, schema: str, hub: str, city: str) -> None:
    """Declare one airport's bronze/silver tables in its own schema, suffixed _<iata>."""
    sfx = iata.lower()
    fq = lambda t: f"{CATALOG}.{schema}.{t}_{sfx}"  # noqa: E731
    raw_path = f"/Volumes/{CATALOG}/{schema}/raw_api/"

    # ── BRONZE ──────────────────────────────────────────────────────────────
    @dlt.table(name=fq("bronze_aerodatabox_raw"),
               comment=f"{iata}: raw AeroDataBox responses, one row per landed 4h slot file.")
    @dlt.expect_or_drop("has_response", "response IS NOT NULL")
    def bronze():
        return (
            spark.readStream.format("cloudFiles")                     # noqa: F821
            .option("cloudFiles.format", "text")
            .option("wholetext", "true")
            .option("pathGlobFilter", "*.json")
            .option("recursiveFileLookup", "true")
            .load(raw_path)
            .selectExpr("parse_json(value) AS doc", "_metadata.file_path AS source_file")
            .selectExpr(
                f"coalesce(doc:airport::string, '{iata}') AS airport",
                "doc:slot_start::timestamp AS slot_start",
                "doc:fetched_at::timestamp AS fetched_at",
                "doc:request AS request",
                "doc:response AS response",
                "source_file",
                "current_timestamp() AS ingested_at",
            )
        )

    # ── SILVER: one row per flight per fetch ────────────────────────────────
    @dlt.table(name=fq("silver_flight_observation"),
               comment=f"{iata}: every observation of every flight, one row per flight per slot.")
    @dlt.expect_or_drop("valid_flight_number", "flight_number IS NOT NULL")
    @dlt.expect_or_drop("valid_time", "scheduled_utc IS NOT NULL")
    @dlt.expect_or_drop("other_end_known", "origin IS NOT NULL AND destination IS NOT NULL")
    def observation():
        b = dlt.read_stream(fq("bronze_aerodatabox_raw"))
        base = ["airport", "slot_start", "fetched_at", "source_file"]
        legs = (
            b.selectExpr(*base, "'departure' AS direction",
                         "posexplode(CAST(response:departures AS ARRAY<VARIANT>)) AS (pos, f)")
            .unionByName(
                b.selectExpr(*base, "'arrival' AS direction",
                             "posexplode(CAST(response:arrivals AS ARRAY<VARIANT>)) AS (pos, f)"))
        )
        raw = legs.selectExpr(
            *base, "direction", "pos",
            "upper(regexp_replace(coalesce(f:number::string, f:callSign::string), '\\\\s+', '')) AS flight_number",
            "f:status::string AS api_status",
            "coalesce(f:isCargo::boolean, false) AS is_cargo",
            "f:codeshareStatus::string AS codeshare_status",
            f"coalesce(f:airline:iata::string, '{hub}') AS airline",
            "f:aircraft:model::string AS aircraft_model",
            "f:aircraft:reg::string AS aircraft_reg",
            # the HOME-airport movement, and the airport at the OTHER end
            "CASE direction WHEN 'departure' THEN f:departure ELSE f:arrival END AS home_mv",
            "CASE direction WHEN 'departure' THEN f:arrival:airport ELSE f:departure:airport END AS other_ap",
        )
        timed = (
            raw.selectExpr("*",
                           f"{_ts('home_mv:scheduledTime:utc')} AS sched_time_utc",
                           f"{_ts('home_mv:revisedTime:utc')} AS revised_time_utc")
            .where("lower(coalesce(api_status, '')) NOT IN ('canceled', 'cancelled')")
            .where("NOT is_cargo")
            .where("coalesce(codeshare_status, '') <> 'IsCodeshared'")
        )
        return timed.selectExpr(
            "airport",
            "to_date(coalesce(sched_time_utc, revised_time_utc)) AS flight_date",
            "flight_number",
            "direction",
            "flight_number AS callsign",
            "airline",
            f"{AIRCRAFT_TYPE_SQL} AS aircraft_type",
            "aircraft_model",
            "CASE direction WHEN 'departure' THEN airport ELSE other_ap:iata::string END AS origin",
            "CASE direction WHEN 'departure' THEN other_ap:iata::string ELSE airport END AS destination",
            f"CASE direction WHEN 'arrival' THEN other_ap:name::string ELSE '{city}' END AS origin_name",
            f"CASE direction WHEN 'departure' THEN other_ap:name::string ELSE '{city}' END AS destination_name",
            "coalesce(revised_time_utc, sched_time_utc) AS scheduled_utc",
            "sched_time_utc",
            "revised_time_utc",
            # Delay in whole minutes, for arrivals AND departures: revised − scheduled
            # (gate times). Positive = late, negative = early, NULL = no revised time yet.
            # The API's `status` only says "Delayed" while a flight is still pending —
            # once it operates it becomes Departed/Arrived — so this is the durable signal.
            "CAST((unix_timestamp(revised_time_utc) - unix_timestamp(sched_time_utc)) / 60 AS INT) AS delay_minutes",
            "CASE direction WHEN 'departure' THEN home_mv:gate::string END AS api_gate",
            "aircraft_reg",
            "CASE direction WHEN 'arrival' THEN home_mv:baggageBelt::string END AS baggage_belt",
            "api_status",
            "fetched_at",
            "slot_start",
            "source_file",
            # CDC tie-break when one fetch holds the same key twice: earliest scheduled
            # wins.
            "-unix_timestamp(coalesce(revised_time_utc, sched_time_utc)) AS seq_tiebreak",
        )

    # ── SILVER: SCD1 current table (synced to Lakebase) ─────────────────────
    dlt.create_streaming_table(
        name=fq("silver_flight_schedule"),
        comment=f"{iata}: current state, one row per flight (SCD1). Source of the Lakebase synced table.",
        table_properties={"delta.enableChangeDataFeed": "true"},
    )
    _auto_cdc(
        target=fq("silver_flight_schedule"),
        source=fq("silver_flight_observation"),
        keys=KEYS,
        sequence_by=F.struct("fetched_at", "seq_tiebreak"),
        ignore_null_updates=True,
        except_column_list=["seq_tiebreak", "slot_start", "source_file"],
        stored_as_scd_type=1,
    )

    # ── SILVER: SCD2 history (analytics) ────────────────────────────────────
    dlt.create_streaming_table(
        name=fq("silver_flight_schedule_history"),
        comment=f"{iata}: every revision of a flight (SCD2): status, times, gate, tail, type.",
    )
    _auto_cdc(
        target=fq("silver_flight_schedule_history"),
        source=fq("silver_flight_observation"),
        keys=KEYS,
        sequence_by=F.struct("fetched_at", "seq_tiebreak"),
        ignore_null_updates=True,
        except_column_list=["seq_tiebreak", "source_file"],
        stored_as_scd_type=2,
        track_history_column_list=TRACK_HISTORY,
    )


# Raw telemetry JSONL (server/simulator/telemetry_files.py). `ts` is read as text and
# cast, so microsecond ISO-8601 with an offset parses regardless of JSON reader defaults;
# `detail` (an object) lands as JSON text and is parsed to VARIANT.
SENSOR_SCHEMA = ("airport STRING, sensor_id STRING, ts STRING, lon DOUBLE, lat DOUBLE, "
                 "indoor BOOLEAN, temperature DOUBLE, pax_density DOUBLE")
EVENT_SCHEMA = ("airport STRING, ts STRING, event STRING, flight_id STRING, direction STRING, "
                "callsign STRING, airline STRING, aircraft_type STRING, aircraft_reg STRING, "
                "origin STRING, destination STRING, gate STRING, runway STRING, detail STRING")


def _telemetry_stream(path: str, prefix: str, schema: str):
    return (
        spark.readStream.format("cloudFiles")                         # noqa: F821
        .option("cloudFiles.format", "json")
        .option("pathGlobFilter", f"{prefix}_*.jsonl")
        .option("recursiveFileLookup", "true")
        .schema(schema)
        .load(path)
        .withColumn("ts", F.col("ts").cast("timestamp"))
        .withColumn("source_file", F.col("_metadata.file_path"))
        .withColumn("ingested_at", F.current_timestamp())
    )


def define_telemetry(iata: str, schema: str) -> None:
    """Sensor + event history for one airport (empty until its generator writes files)."""
    sfx = iata.lower()
    fq = lambda t: f"{CATALOG}.{schema}.{t}_{sfx}"  # noqa: E731
    tel_path = f"/Volumes/{CATALOG}/{schema}/raw_telemetry/"

    @dlt.table(name=fq("sensor_readings"),
               comment=f"{iata}: every sensor reading (one per sensor every 5 s) — temperature °C, "
                       f"passenger density pax/m². Full history of the live Lakebase log.")
    @dlt.expect_or_drop("valid_reading", "sensor_id IS NOT NULL AND ts IS NOT NULL")
    def sensor_readings():
        return _telemetry_stream(tel_path, "sensors", SENSOR_SCHEMA)

    @dlt.table(name=fq("sim_events"),
               comment=f"{iata}: simulation lifecycle events (TOUCHDOWN, GATE_IN, GATE_OUT, "
                       f"TURNAROUND_HANDOVER, AT_GATE, PUSHBACK, HOLD_SHORT, RUNWAY_ENTER, TAKEOFF).")
    @dlt.expect_or_drop("valid_event", "event IS NOT NULL AND flight_id IS NOT NULL AND ts IS NOT NULL")
    def sim_events():
        return (_telemetry_stream(tel_path, "events", EVENT_SCHEMA)
                .withColumn("detail", F.expr("try_parse_json(detail)")))


for _a in AIRPORTS:
    define_airport(_a["iata"].upper(), _a["schema"], _a.get("hub") or "",
                   (_a.get("city") or _a["iata"]).replace("'", "''"))
    define_telemetry(_a["iata"].upper(), _a["schema"])

# Bundle resources

| File | What | Managed by |
|---|---|---|
| `app.yml` | Databricks App `airport-simulator`: API server + UI, and the generator as a supervised child process (`RUN_GENERATOR=1` in `app.yaml`). App resources grant its service principal WRITE_VOLUME on `raw_telemetry`, CAN_RUN on the Genie space, CAN_USE on the warehouse and SELECT on the flight/sensor/event tables. **Resource names must be ≤ 30 characters.** | DAB |
| `ingest.yml` | Ingest for **all** airports: `raw_api` + `raw_telemetry` volumes per airport schema, ONE SDP pipeline `[dev] airport-simulator-flights`, ONE job `[dev] airport-simulator-ingest` | DAB |
| `airport_genie.genie_space.yml` | Genie space "Airport Flight Schedule Overview for DOH and LHR" (id in the bound deployment state, not in the repo); definition in `../genie/airport_genie.geniespace.json`. Bound to the existing space, so the ID never changes | DAB |
| — | `[dev] HIA airport-simulator-generator` — fallback only, **PAUSED** | Manual UI job |

## Ingest job shape

```
cron 00/04/08/12/16/20 UTC
Stage 1  fetch            for_each airport in job parameter `airports` (parallel)
                          notebooks/fetch_aerodatabox.py → /Volumes/<cat>/<schema>/raw_api/aerodatabox/…
Stage 2  transform        ONE SDP pipeline → per airport: bronze_*_<sfx>, silver_*_<sfx> (SCD1 + SCD2),
                          sensor_readings_<sfx>, sim_events_<sfx> (from raw_telemetry)
then     sync_lakebase    for_each airport → Lakebase <db>.<schema>.flight_schedule_<sfx> (TRIGGERED)
                          + re-grant SELECT to airport_app
```

- **Which airports are fetched:** job parameter `airports` (DOH + LHR — each fetched
  airport costs 6 AeroDataBox calls/day). `<sfx>` = lower-case IATA.
- **Add a new airport:**
  1. `config/airports.json` entry (iata, icao, tz, hub, schema, …);
  2. create the UC schema `airport_simulator_<iata>`;
  3. in `ingest.yml`: `raw_api_<iata>` + `raw_telemetry_<iata>` volume blocks and an
     entry in the pipeline's `airports` configuration;
  4. add it to the job parameter `airports`;
  5. to also simulate it: geodata in `data/` plus a generator for that airport
     (`AIRPORT_IATA=<IATA>`), and app SELECT resources for its tables.

## Generator

Runs inside the app (`server/generator_supervisor.py` spawns `generator.py`, restarts it
on exit with 5 → 15 → 60 s backoff). One generator per airport is enforced by a Postgres
advisory lock, so the paused fallback job would simply wait if both ran.

Fallback (`RUN_GENERATOR=0`): unpause the manual job — `spark_python_task` →
`generator.py`, serverless client `"3"`, dependencies `asyncpg>=0.29.0`,
`databricks-sdk>=0.30.0`, `requests>=2.31.0`, **CONTINUOUS**, `max_concurrent_runs: 1`.
Note: pausing a continuous job does **not** stop its active run — cancel the run too.

## Genie space workflow

The space is DAB-managed: `bundle deploy` pushes `genie/airport_genie.geniespace.json`.
Edits made in the Genie UI are **overwritten** on the next deploy unless you pull them
into the bundle first:

```
databricks bundle generate genie-space --resource airport_genie --force -p fevm01   # UI → files
git checkout -- resources/airport_genie.genie_space.yml                             # keep the PARAMETERIZED yaml (see below)
git diff genie/                                                                     # review the definition change
databricks bundle deploy -p fevm01                                                  # files → workspace
```

`generate --force` also rewrites `airport_genie.genie_space.yml` with literal values
(warehouse id, your workspace path). The committed version uses `${var.warehouse_id}` and
`${workspace.current_user.userName}` — always restore it before committing, because this
repo is public.

## Public-repo hygiene

No workspace URL, database host, email or resource id is stored in the repo: the host comes
from the CLI profile, the Lakebase host from the `lakebase-host` secret, user paths from
`${workspace.current_user.userName}`, the warehouse via a name `lookup`, and the Genie space
id via `${resources.genie_spaces.airport_genie.id}` / the app's `valueFrom: genie-space`.
Real values are kept only in the gitignored `CLAUDE.md`.

`databricks.yml` excludes `genie/**` from the upload (`sync.exclude`): the app snapshots
the bundle root and cannot copy a `.geniespace.json` workspace object.

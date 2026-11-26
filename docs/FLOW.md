# Flow diagrams

_Current as of 2026-11-26. Prose: [APPFLOW.md](APPFLOW.md) · boundaries and tables: [ARCHITECTURE.md](ARCHITECTURE.md)._

## 1. Architecture

```mermaid
flowchart LR
  User([User browser]) --> UI
  ADB([AeroDataBox API])

  subgraph APP["App · airport-simulator (MEDIUM)"]
    UI[App UI · React]
    API[API server · FastAPI<br/>reader loop 1 s]
    SUP[Supervisor]
    subgraph GEN["Generator · child process"]
      SEN[Sensor task · 5 s]
      FLT[Flights task · 1 s]
    end
    SUP --> GEN
    API -- WebSocket / REST --> UI
  end

  subgraph LB["Lakebase · airport-simulator-db / hia_sim"]
    LBS[(sensor_current<br/>sensor_readings 24h)]
    LBL[(sim_positions · gate_occupancy<br/>sim_heartbeat · sim_gate_assignment)]
    LBF[(flight_schedule_&lt;iata&gt;<br/>synced · read-only)]
  end

  subgraph LH["Lakehouse · classic_stable_kuldeep.airport_simulator_&lt;iata&gt;"]
    FET[Fetch notebook]
    VOL[(raw_api · raw_telemetry)]
    SDP[SDP pipeline]
    DLT[(Delta bronze / silver<br/>sensor_readings · sim_events)]
    SYN[Sync notebook]
  end

  GENIE([Genie space<br/>SQL warehouse])

  ADB --> FET --> VOL --> SDP --> DLT --> SYN --> LBF
  LBF -- roster 60 s --> FLT
  SEN -- 5 s --> LBS
  FLT -- 1 s --> LBL
  GEN -- telemetry files 15 min --> VOL
  LBS & LBL & LBF --> API
  UI -- /api/genie --> GENIE --> DLT
```

## 2. Ingest job (every 4 hours)

```mermaid
flowchart LR
  T([cron 00/04/08/12/16/20 UTC]) --> F
  subgraph F["Stage 1 · fetch (for each airport)"]
    F1{slot file exists?}
    F1 -- yes --> F2[reuse · 0 API calls]
    F1 -- no --> F3[call AeroDataBox<br/>now−4h … now+8h] --> F4[write raw_api/…/&lt;IATA&gt;_&lt;slot&gt;Z.json]
  end
  F --> P
  subgraph P["Stage 2 · transform (one pipeline, all airports)"]
    P1[bronze_aerodatabox_raw_&lt;iata&gt;<br/>VARIANT] --> P2[silver_flight_observation_&lt;iata&gt;]
    P2 --> P3[silver_flight_schedule_&lt;iata&gt;<br/>SCD1 · CDF]
    P2 --> P4[silver_flight_schedule_history_&lt;iata&gt;<br/>SCD2]
    P5[raw_telemetry] --> P6[sensor_readings_&lt;iata&gt;]
    P5 --> P7[sim_events_&lt;iata&gt;]
  end
  P --> S
  subgraph S["Stage 3 · sync_lakebase (for each airport)"]
    S1[create or refresh<br/>TRIGGERED synced table] --> S2[re-grant SELECT<br/>to airport_app]
  end
```

## 3. Generator — flights task (1 Hz)

```mermaid
sequenceDiagram
  participant G as Flights task
  participant SM as State machines<br/>(seed_runner, departures)
  participant LB as Lakebase
  participant FS as Telemetry sink
  loop every second
    G->>G: sim_time = now (UTC), dt = real elapsed
    G->>SM: traffic.begin_tick · register_traffic · advance(arrivals → departures)
    SM-->>FS: emit lifecycle events (buffered)
    G->>LB: upsert sim_positions (per live flight)
    G->>LB: publish gate_occupancy · flush sim_gate_assignment
    G->>FS: flush closed 15-min windows → raw_telemetry/events
    G->>LB: write sim_heartbeat
    opt every 60 ticks
      G->>LB: read flight_schedule_<iata> window → refresh rosters
      G->>LB: sweep stale positions (> 5 min)
    end
  end
```

## 4. Generator — sensor task (every 5 s)

```mermaid
flowchart LR
  T([fixed 5 s grid]) --> R[26 readings<br/>17 apron · 9 terminal<br/>temperature + pax density]
  R --> C[(sensor_current<br/>upsert latest)]
  R --> H[(sensor_readings<br/>append · pruned to 24 h)]
  R --> B[15-min buffer] -- window closes / SIGTERM --> V[(raw_telemetry/sensors)]
  C -- poll 2 s --> W[WebSocket sensor_update]
  H -- /api/sensors/trend · /history --> D[Dashboard 15-min trend]
```

## 5. Arrival lifecycle

```mermaid
stateDiagram-v2
  [*] --> SCHEDULED
  SCHEDULED --> ARRIVING: touchdown time reached + runway clear<br/>reserve gate · route · event TOUCHDOWN
  ARRIVING --> PARKED: reached stand · event GATE_IN
  PARKED --> GONE: dwell elapsed · event GATE_OUT
  PARKED --> GONE: linked departure takes over · event TURNAROUND_HANDOVER
  GONE --> [*]: row removed · gate released
```

## 6. Departure lifecycle

```mermaid
stateDiagram-v2
  [*] --> SCHEDULED
  SCHEDULED --> GATE_PARKED: 30–45 min before departure<br/>claim gate · event AT_GATE
  GATE_PARKED --> ACTIVE: scheduled time · choose 34R/34L · event PUSHBACK
  ACTIVE --> ACTIVE: taxi · queue · event HOLD_SHORT
  ACTIVE --> ACTIVE: runway clear · event RUNWAY_ENTER
  ACTIVE --> DONE: rotation · event TAKEOFF
  DONE --> [*]: row removed
```

## 7. Turnaround handover (data/turnarounds.json)

```mermaid
sequenceDiagram
  participant A as Arrival (e.g. TK780)
  participant T as turnarounds.py
  participant D as Departure (TK781)
  A->>A: PARKED at stand
  loop each tick
    A->>T: arrival_should_hold?
    T-->>A: yes while TK781 is SCHEDULED (and pair is valid)
  end
  D->>T: departure_gate_action (at gate-park time)
  alt arrival PARKED
    T-->>D: takeover(gate)
    D->>A: hand_over_to_departure → arrival GONE
    D->>D: claim the same gate · GATE_PARKED
  else arrival still inbound
    T-->>D: wait (unlink if pushback time passes first)
  end
```

## 8. Ask Genie request

```mermaid
sequenceDiagram
  participant U as Ask Genie tab
  participant A as App API (/api/genie)
  participant G as Genie Conversation API
  participant W as SQL warehouse
  U->>A: POST /api/genie/ask {question, conversation_id?}
  A->>G: start-conversation | create message (as app SP)
  G-->>A: conversation_id, message_id
  A-->>U: ids
  loop every 1.5 s until COMPLETED / FAILED
    U->>A: GET /api/genie/message
    A->>G: get message
    G->>W: generated SQL on silver tables
    A->>G: query-result (when COMPLETED)
    A-->>U: status · text · SQL · rows · suggested questions
  end
```

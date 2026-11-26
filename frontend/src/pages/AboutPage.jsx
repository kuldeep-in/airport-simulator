import { useEffect, useMemo, useState } from 'react';
import { Info, Plane, Workflow, Table2, Database } from 'lucide-react';
import { AIRPORT } from '../lib/airport.js';
import { useSimStore } from '../store/useSimStore';
import { PAGE_BG } from '../components/boardTheme.jsx';

const HEALTH_MS = 5000;     // app + generator liveness
const SCHEDULE_MS = 30000;  // flight dataset row counts

// ── Where things live (mirrors resources/*.yml, app.yaml, config/airports.json) ──
const UC_CATALOG = 'classic_stable_kuldeep';
const LAKEBASE = { instance: 'airport-simulator-db', database: 'hia_sim' };
const INGEST_JOB = '[dev] airport-simulator-ingest';
const INGEST_CRON = 'every 4h · 00/04/08/12/16/20 UTC';
const PIPELINE = '[dev] airport-simulator-flights';
const APP_NAME = 'airport-simulator';
const WAREHOUSE = 'SQL-xsmall (serverless)';
const SENSOR_INTERVAL_S = 5;
const AIRPORTS = [
  { iata: 'DOH', simulated: true },
  { iata: 'LHR', simulated: false },
].map((a) => ({ ...a, sfx: a.iata.toLowerCase(), schema: `airport_simulator_${a.iata.toLowerCase()}` }));
const ACTIVE = AIRPORTS.find((a) => a.iata === AIRPORT.iata) || AIRPORTS[0];

const STATUS = {
  ok:      { color: '#22c55e', label: 'Live' },
  warn:    { color: '#fbbf24', label: 'Degraded' },
  down:    { color: '#f87171', label: 'Down' },
  batch:   { color: '#818cf8', label: 'Batch (every 4h) / on demand' },
  unknown: { color: '#64748b', label: 'Unknown' },
};
// Tile "kind" chips and boundary colours, by platform.
const KIND = {
  EXTERNAL: '#94a3b8', NOTEBOOK: '#f59e0b', 'UC VOLUME': '#a78bfa', DELTA: '#38bdf8', SDP: '#38bdf8',
  TABLES: '#34d399', TASK: '#fb7185', PROCESS: '#fb7185', WEB: '#fb7185', IDENTITY: '#fda4af',
  USER: '#e2e8f0', 'AI/BI': '#facc15', JOB: '#f59e0b', 'ML MODEL': '#c084fc',
};
const ZONE = { app: '#fb7185', lakebase: '#34d399', lakehouse: '#818cf8' };

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

// About page — one architecture view with three BOUNDARIES (App, Lakebase, Lakehouse)
// and the outside parties (user, AeroDataBox, Genie). Every tile sits inside the system
// that runs it; every arrow is a data flow, coloured live (solid, moving dots) or batch
// (dashed). Below it: a catalog of every table/volume and the component schedule.
export default function AboutPage() {
  const [health, setHealth] = useState(null);      // null = not yet known, false = unreachable
  const [sched, setSched] = useState(null);
  const [space, setSpace] = useState(null);

  useEffect(() => {
    const load = () => fetchJSON('/api/health').then(setHealth).catch(() => setHealth(false));
    load();
    const t = setInterval(load, HEALTH_MS);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    const load = () => fetchJSON('/api/schedule')
      .then((d) => setSched({ inbound: (d.inbound || []).length, outbound: (d.outbound || []).length }))
      .catch(() => setSched(null));
    load();
    const t = setInterval(load, SCHEDULE_MS);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { fetchJSON('/api/genie/space').then(setSpace).catch(() => setSpace({ error: true })); }, []);

  const wsConnected = useSimStore((s) => s.wsConnected);
  const positions = useSimStore((s) => s.positions);
  const sensors = useSimStore((s) => s.sensorReadings);

  const m = useMemo(() => {
    const sr = Object.values(sensors);
    const avgTemp = sr.length ? sr.reduce((a, r) => a + (r.temperature || 0), 0) / sr.length : null;
    const avgPax = sr.length ? sr.reduce((a, r) => a + (r.pax_density || 0), 0) / sr.length : null;
    const newest = sr.reduce((t, r) => (r.updated_at && r.updated_at > t ? r.updated_at : t), '');
    return {
      flights: Object.keys(positions).length, sensors: sr.length, avgTemp, avgPax,
      sensorAgeS: newest ? Math.max(0, Math.round((Date.now() - new Date(newest).getTime()) / 1000)) : null,
    };
  }, [positions, sensors]);

  // ── Live statuses ─────────────────────────────────────────────────────────
  const feed = health ? health.feed : null;
  const gen = health ? health.generator : null;
  const appS = health === null ? 'unknown' : health ? 'ok' : 'down';
  const lbS = !health ? (health === false ? 'down' : 'unknown') : health.demo_mode ? 'down' : 'ok';
  const genS = !feed ? 'unknown' : feed.alive ? 'ok' : 'down';
  const supS = !gen ? 'unknown' : gen.running ? 'ok' : 'down';
  const schedTotal = sched ? sched.inbound + sched.outbound : null;
  const ingestS = schedTotal == null ? 'unknown' : schedTotal > 0 ? 'ok' : 'warn';
  const uiS = wsConnected ? 'ok' : 'down';
  const sensorS = lbS !== 'ok' ? lbS : !m.sensors ? 'warn' : (m.sensorAgeS != null && m.sensorAgeS > 30 ? 'warn' : 'ok');
  const genieS = !space ? 'unknown' : space.title ? 'ok' : 'down';
  const on = (...ss) => ss.every((s) => s === 'ok');
  const fmt = (v, d = 1) => (v == null ? '—' : v.toFixed(d));

  // ── Tiles: [x, y, w, h] in a 1160 × 620 canvas ───────────────────────────────
  const tiles = [
    // outside
    { id: 'user', x: 10, y: 455, w: 95, h: 90, kind: 'USER', s: uiS, title: 'User', lines: ['browser', 'desktop / tablet'] },
    { id: 'adb', x: 1060, y: 52, w: 90, h: 88, kind: 'EXTERNAL', s: ingestS === 'ok' ? 'ok' : 'unknown', title: 'AeroData-', lines: ['Box API', 'HTTPS', '1 call / 4 h'] },
    { id: 'genie', x: 480, y: 470, w: 220, h: 104, kind: 'AI/BI', s: genieS, title: 'Genie space',
      lines: [space?.title ? trunc(space.title, 34) : 'natural-language SQL', `warehouse ${WAREHOUSE}`, 'runs only when asked'] },
    // APP
    { id: 'sup', x: 130, y: 76, w: 142, h: 76, kind: 'PROCESS', s: supS, title: 'Supervisor',
      lines: ['spawns + restarts', gen?.pid ? `generator pid ${gen.pid}` : 'generator.py'] },
    { id: 'idn', x: 130, y: 170, w: 142, h: 200, kind: 'IDENTITY', s: appS, title: 'App identity',
      lines: ['service principal', 'Lakebase: airport_app', 'raw_telemetry: WRITE', 'Genie space: CAN_RUN', 'warehouse: CAN_USE', 'Delta tables: SELECT'] },
    { id: 'sen', x: 293, y: 76, w: 146, h: 74, kind: 'TASK', s: sensorS, title: 'Sensor task',
      lines: [`every ${SENSOR_INTERVAL_S} s · 26 sensors`, m.sensors ? `${fmt(m.avgTemp)}°C · ${fmt(m.avgPax, 2)} pax/m²` : 'temp + pax density'] },
    { id: 'flt', x: 293, y: 164, w: 146, h: 74, kind: 'TASK', s: genS, title: 'Flights task',
      lines: ['every 1 s · positions', feed?.live_flights != null ? `${feed.live_flights} live · gates · events` : 'gates · runway · events'] },
    { id: 'api', x: 285, y: 270, w: 160, h: 100, kind: 'WEB', s: appS, title: 'API server',
      lines: ['FastAPI · reader loop 1 s', 'WebSocket · REST', '/api/genie · /api/sensors'] },
    { id: 'ui', x: 130, y: 420, w: 315, h: 160, kind: 'WEB', s: uiS, title: 'App UI (React)',
      lines: ['Home map · Dashboard · Schedule · Gates', 'Ask Genie · About · delay-risk alerts',
        wsConnected ? `live via WebSocket · ${m.flights} flights on map` : 'WebSocket closed'] },
    // LAKEBASE
    { id: 'lbs', x: 492, y: 52, w: 176, h: 78, kind: 'TABLES', s: sensorS, title: 'Sensor tables',
      lines: ['sensor_current', 'sensor_readings (24 h)'], mono: [0, 1] },
    { id: 'lbl', x: 492, y: 146, w: 176, h: 104, kind: 'TABLES', s: lbS === 'ok' ? genS : lbS, title: 'Live sim tables',
      lines: ['sim_positions', 'gate_occupancy', 'sim_heartbeat', 'sim_gate_assignment'], mono: [0, 1, 2, 3] },
    { id: 'lbf', x: 492, y: 282, w: 176, h: 88, kind: 'TABLES', s: lbS === 'ok' ? ingestS : lbS, title: 'Synced tables',
      lines: ['flight_schedule_doh | _lhr', `delay_risk_${ACTIVE.sfx} (ML scores)`, 'TRIGGERED · read-only'], mono: [0, 1] },
    // LAKEHOUSE
    { id: 'vol', x: 712, y: 52, w: 150, h: 88, kind: 'UC VOLUME', s: 'batch', title: 'Volumes',
      lines: ['raw_api/', 'raw_telemetry/', 'JSON · JSONL files'], mono: [0, 1] },
    { id: 'fet', x: 885, y: 52, w: 150, h: 88, kind: 'NOTEBOOK', s: 'batch', title: 'Fetch',
      lines: ['fetch_aerodatabox.py', 'for each airport', 'every 4 h'], mono: [0] },
    { id: 'sdp', x: 712, y: 168, w: 150, h: 82, kind: 'SDP', s: 'batch', title: 'Pipeline',
      lines: ['airport-simulator-', 'flights · serverless', 'Auto Loader + AUTO CDC'], mono: [0, 1] },
    { id: 'dlt', x: 885, y: 168, w: 150, h: 202, kind: 'DELTA', s: 'batch', title: 'Delta tables',
      lines: ['bronze_aerodatabox_raw_*', 'silver_…_observation_*', 'silver_flight_schedule_*', 'silver_…_history_*',
        'sensor_readings_*', 'sim_events_*', 'gold_delay_risk_doh (ML)'], mono: [0, 1, 2, 3, 4, 5, 6], small: true },
    // INTELLIGENCE — the delay-risk model (trained weekly, scored hourly in its own job)
    { id: 'ml', x: 885, y: 420, w: 150, h: 150, kind: 'ML MODEL', s: 'batch', title: 'Delay-risk model',
      lines: ['UC model @champion', 'P(dep. 15+ min late)', 'scored hourly, 2 h+ ahead', 'retrained weekly', 'MLflow · AUC 0.76'], mono: [0] },
    { id: 'syn', x: 712, y: 282, w: 150, h: 88, kind: 'NOTEBOOK', s: 'batch', title: 'Lakebase sync',
      lines: ['sync_to_lakebase.py', 'synced tables', '+ re-grant SELECT'], mono: [0] },
  ];

  const edges = [
    // lakehouse (batch)
    { id: 'e-adb', d: 'M1060,96 L1037,96', batch: true },
    { id: 'e-fet', d: 'M885,96 L864,96', batch: true },
    { id: 'e-vol', d: 'M787,140 L787,166', batch: true },
    { id: 'e-sdp', d: 'M862,209 L883,209', batch: true },
    { id: 'e-dlt', d: 'M885,326 L864,326', batch: true },
    { id: 'e-syn', d: 'M712,326 L670,326', label: 'sync', lx: 691, ly: 318, batch: true },
    // generator → files (batch), over the top of Lakebase
    { id: 'e-files', d: 'M366,52 L366,8 L787,8 L787,50', label: 'telemetry files · every 15 min · Files API', lx: 576, ly: 6, batch: true, small: true },
    // live — generator ↔ Lakebase
    { id: 'e-sen', d: 'M439,104 L490,104', label: '5 s', lx: 465, ly: 98, dur: 2, on: on(sensorS, lbS) },
    { id: 'e-flt', d: 'M439,190 L490,190', label: '1 s', lx: 465, ly: 184, dur: 1.2, on: on(genS, lbS) },
    { id: 'e-ros', d: 'M492,300 L468,300 L468,222 L441,222', label: '60 s', lx: 472, ly: 266, anchor: 'start', dur: 3, on: on(genS, lbS) },
    // live — app reads Lakebase, serves the UI
    { id: 'e-poll', d: 'M480,352 L447,352', label: 'read', lx: 463, ly: 346, dur: 1.2, on: on(lbS, appS) },
    { id: 'e-sup', d: 'M272,113 L291,113', dur: 2, on: supS === 'ok' },
    { id: 'e-ws', d: 'M365,370 L365,418', label: 'WebSocket · REST', lx: 372, ly: 398, anchor: 'start', dur: 1.2, on: on(appS, uiS) },
    { id: 'e-usr', d: 'M105,500 L128,500', dur: 2, on: uiS === 'ok' },
    // Genie (outside the app) — asked by the UI via /api/genie, queries Delta
    { id: 'e-ask', d: 'M445,522 L478,522', label: 'ask', lx: 461, ly: 514, dur: 2, on: on(appS, genieS) },
    { id: 'e-sql', d: 'M700,486 L873,486 L873,392 L910,392 L910,372', label: 'SQL on Delta', lx: 786, ly: 478, batch: true },
    // ML: governed silver → features → model; scores → gold Delta (then the existing sync → Lakebase)
    { id: 'e-mlin', d: 'M960,372 L960,418', label: 'features', lx: 955, ly: 402, anchor: 'end', batch: true, small: true },
    { id: 'e-mlout', d: 'M995,420 L995,374', label: 'scores', lx: 1001, ly: 402, anchor: 'start', batch: true, small: true },
  ];

  const components = [
    { s: 'batch', what: 'Ingest job', type: 'Lakeflow Job (DAB)', name: INGEST_JOB, freq: INGEST_CRON,
      io: 'fetch (for each airport) → transform → sync_lakebase (for each airport)' },
    { s: 'batch', what: 'Fetch', type: 'Notebook task', name: 'notebooks/fetch_aerodatabox.py', freq: 'each job run · 1 API call / airport / slot',
      io: 'AeroDataBox → raw_api volume' },
    { s: 'batch', what: 'Transform', type: 'SDP pipeline · serverless · triggered', name: PIPELINE, freq: 'each job run · or on demand',
      io: 'raw_api → bronze → silver (SCD1 + SCD2); raw_telemetry → sensor_readings_* + sim_events_*' },
    { s: 'batch', what: 'Delay-risk scoring', type: 'Job [dev] airport-simulator-score-delay-risk', name: 'ml/score_delay_risk.py → sync_to_lakebase.py', freq: 'hourly at :15 UTC',
      io: 'UC model @champion → gold_delay_risk_doh (current) + gold_delay_risk_log_doh + v_delay_risk_outcomes_doh' },
    { s: 'batch', what: 'Delay-risk training', type: 'Lakeflow Job (DAB) · MLflow · UC registry', name: '[dev] airport-simulator-train-delay-risk', freq: 'weekly · Sun 02:00 UTC',
      io: 'silver + archive → gold_departure_features_doh → delay_risk_model (@champion only if it beats the baseline)' },
    { s: 'batch', what: 'Sync', type: 'Notebook task + synced tables', name: 'notebooks/sync_to_lakebase.py', freq: 'each job run (after transform / scoring)',
      io: 'silver_flight_schedule_* → flight_schedule_*; gold_delay_risk_doh → delay_risk_doh · re-grants SELECT to airport_app' },
    { s: genS, what: 'Generator', type: 'App child process · supervised · auto-restart', name: 'generator.py', freq: `flights 1 s (roster 60 s) · sensors ${SENSOR_INTERVAL_S} s`,
      io: 'flight_schedule_* → live + sensor tables; telemetry files → raw_telemetry' },
    { s: appS, what: 'API server + UI', type: 'Databricks App · MEDIUM · only always-on compute', name: APP_NAME, freq: 'positions 1 s · sensors 2 s · REST 5–15 s',
      io: 'Lakebase → WebSocket + REST → React UI; /api/genie → Genie' },
    { s: genieS, what: 'Genie', type: 'AI/BI Genie space · SQL warehouse', name: space?.title || 'Genie space', freq: 'on demand (each question)',
      io: 'question → SQL over Unity Catalog tables → answer, SQL and result rows in the Ask Genie tab' },
  ];

  return (
    <div style={{ height: '100%', width: '100%', overflow: 'auto', background: PAGE_BG }}>
      <div className="panel" style={{
        height: 56, display: 'flex', alignItems: 'center', padding: '0 16px',
        borderRadius: 0, borderLeft: 'none', borderRight: 'none', borderTop: 'none',
        position: 'sticky', top: 0, zIndex: 1,
      }}>
        <Info size={20} className="accent" />
        <div style={{ marginLeft: 10 }}>
          <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1 }}>About</div>
        </div>
      </div>

      <div style={{ padding: 16, maxWidth: 1200, margin: '0 auto' }}>
        <SectionLabel icon={<Plane size={13} />}>Airport</SectionLabel>
        <div className="card" style={{
          padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 14,
          borderLeft: '3px solid var(--accent-blue)', marginBottom: 24,
        }}>
          <div style={{
            width: 44, height: 44, borderRadius: 10, flexShrink: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'var(--accent-blue)', color: '#0a0e1a', fontWeight: 800, fontSize: 15, letterSpacing: 0.5,
          }}>
            {AIRPORT.iata}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>
              {AIRPORT.name}
              <span className="muted" style={{ fontWeight: 400, fontSize: 12, marginLeft: 6 }}>{AIRPORT.icao}</span>
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              {AIRPORT.city}, {AIRPORT.country} · {AIRPORT.tz} ({AIRPORT.tz_abbr})
            </div>
          </div>
        </div>

        <SectionLabel icon={<Workflow size={13} />}>Architecture &amp; live data flow</SectionLabel>
        <div className="card" style={{ padding: 16, marginBottom: 24 }}>
          <Legend />
          <svg viewBox="0 0 1160 620" width="100%" role="img" style={{ display: 'block', fontFamily: 'var(--font-sans)' }}
               aria-label={`Architecture. Inside the App boundary: a supervisor running the generator (sensor task every ${SENSOR_INTERVAL_S} s, flights task every second), an API server and the React UI. The generator writes sensor and live simulation tables in Lakebase and reads the synced flight tables; the API server reads Lakebase and serves the UI over WebSocket and REST. The Lakehouse boundary holds the 4-hourly ingest: fetch notebook, volumes, pipeline, Delta tables and the Lakebase sync. The generator also lands telemetry files in the Lakehouse volumes. Outside: the user, the AeroDataBox API, and a Genie space that the UI asks via the app and that queries the Delta tables on a SQL warehouse.`}>
            <defs>
              {[['on', '#38bdf8'], ['off', '#64748b'], ['batch', STATUS.batch.color]].map(([k, c]) => (
                <marker key={k} id={`arch-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill={c} />
                </marker>
              ))}
            </defs>
            <Boundary x={115} y={20} w={340} h={580} color={ZONE.app} title="APP" sub={`${APP_NAME} · Databricks App · MEDIUM`} />
            <Boundary x={285} y={52} w={162} h={196} color={ZONE.app} title="GENERATOR" sub="child process" inner />
            <Boundary x={480} y={20} w={200} h={380} color={ZONE.lakebase} title="LAKEBASE" sub={LAKEBASE.database} />
            <Boundary x={705} y={20} w={340} h={562} color={ZONE.lakehouse} title="LAKEHOUSE" sub={`Unity Catalog · ${UC_CATALOG}`} />
            {edges.map((e) => <Edge key={e.id} e={e} />)}
            {tiles.map((t) => <Tile key={t.id} {...t} />)}
          </svg>
        </div>

        <SectionLabel icon={<Database size={13} />}>Tables &amp; volumes in the flow</SectionLabel>
        <TableCatalog />

        <SectionLabel icon={<Table2 size={13} />}>Components &amp; schedule</SectionLabel>
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table style={TABLE}>
            <thead><tr>{['Component', 'Type', 'Name', 'Frequency', 'Reads → writes'].map((h) => <th key={h} style={TH}>{h}</th>)}</tr></thead>
            <tbody>
              {components.map((c) => (
                <tr key={c.what}>
                  <td style={TD}><span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 700 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: STATUS[c.s].color }} />{c.what}</span></td>
                  <td style={TD}><span className="muted">{c.type}</span></td>
                  <td style={{ ...TD, fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>{c.name}</td>
                  <td style={TD}>{c.freq}</td>
                  <td style={{ ...TD, fontSize: 11 }} className="muted">{c.io}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

const trunc = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function Boundary({ x, y, w, h, color, title, sub, inner }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={inner ? 9 : 14} fill={inner ? 'rgba(251,113,133,0.05)' : `${color}0d`}
            stroke={color} strokeOpacity={inner ? 0.45 : 0.7} strokeWidth={inner ? 1 : 1.6} strokeDasharray={inner ? '4 3' : undefined} />
      <text x={x + 10} y={y + (inner ? 13 : 16)} fill={color} fontSize={inner ? 8.5 : 10.5} fontWeight="800" letterSpacing="0.1em">{title}</text>
      {sub && <text x={x + w - 10} y={y + (inner ? 13 : 16)} textAnchor="end" fill="var(--text-muted)" fontSize={inner ? 8.5 : 9}
                    fontFamily="var(--font-mono)">{sub}</text>}
    </g>
  );
}

function Edge({ e }) {
  const k = e.batch ? 'batch' : e.on ? 'on' : 'off';
  const stroke = e.batch ? STATUS.batch.color : e.on ? '#38bdf8' : '#475569';
  return (
    <g>
      <path id={e.id} d={e.d} fill="none" stroke={stroke} strokeWidth="1.6" strokeOpacity="0.85" strokeLinejoin="round"
            strokeDasharray={e.batch ? '6 4' : undefined} markerEnd={`url(#arch-${k})`} />
      {!e.batch && e.on && [0, 0.5].map((off) => (
        <circle key={off} r="3" fill="#7dd3fc">
          <animateMotion dur={`${e.dur}s`} begin={`${-off * e.dur}s`} repeatCount="indefinite">
            <mpath href={`#${e.id}`} />
          </animateMotion>
        </circle>
      ))}
      {e.label && (
        <text x={e.lx} y={e.ly} textAnchor={e.anchor || 'middle'} fill="var(--text-muted)"
              fontSize={e.small ? 9 : 9.5} fontFamily="var(--font-mono)">{e.label}</text>
      )}
    </g>
  );
}

function Tile({ x, y, w, h, kind, s, title, lines = [], mono = [], small }) {
  const kc = KIND[kind] || '#94a3b8';
  const sc = STATUS[s].color;
  const lh = small ? 13.5 : 14.5;
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx="9" fill="var(--bg-elevated)" stroke={sc} strokeOpacity="0.55" />
      <rect x={x} y={y} width={w} height="3" rx="1.5" fill={kc} opacity="0.9" />
      <text x={x + 10} y={y + 17} fill={kc} fontSize="8" fontWeight="800" letterSpacing="0.1em">{kind}</text>
      <StatusDot x={x + w - 12} y={y + 14} s={s} />
      <text x={x + 10} y={y + 34} fill="var(--text-primary)" fontSize="12.5" fontWeight="700">{title}</text>
      {lines.filter(Boolean).map((l, i) => (
        <text key={i} x={x + 10} y={y + 51 + i * lh} fill="var(--text-muted)"
              fontSize={mono.includes(i) ? 9 : 10} fontFamily={mono.includes(i) ? 'var(--font-mono)' : undefined}>{l}</text>
      ))}
    </g>
  );
}

// ── Table catalog ─────────────────────────────────────────────────────────────
const UC = (t) => AIRPORTS.map((a) => `${UC_CATALOG}.${a.schema}.${t.replace('<iata>', a.sfx)}`);
const CATALOG = [
  {
    store: 'Unity Catalog — files & Delta', sub: `${UC_CATALOG}.airport_simulator_<iata>  (one schema per airport)`,
    items: [
      { name: 'raw_api', type: 'UC volume', writer: 'Fetch notebook (ingest job)', cadence: '1 JSON file / airport / 4 h slot',
        key: 'path aerodatabox/date=…/<IATA>_<slot>Z.json', retention: 'kept (replay source for bronze)',
        readers: 'Pipeline (Auto Loader)', names: AIRPORTS.map((a) => `/Volumes/${UC_CATALOG}/${a.schema}/raw_api`),
        cols: ['airport', 'slot_start', 'fetched_at', 'request {url, params}', 'response (raw AeroDataBox body)'] },
      { name: 'raw_telemetry', type: 'UC volume', writer: 'Generator (Files API, app service principal)', cadence: '1 JSONL file / stream / 15 min (flushed on graceful stop)',
        key: 'path {sensors,events}/date=…/<stream>_<IATA>_<window>Z_<run>.jsonl', retention: 'kept (replay source)',
        readers: 'Pipeline (Auto Loader)', names: AIRPORTS.map((a) => `/Volumes/${UC_CATALOG}/${a.schema}/raw_telemetry`),
        cols: ['sensors: airport, sensor_id, ts, lon, lat, indoor, temperature, pax_density', 'events: airport, ts, event, flight_id, direction, callsign, airline, aircraft_type, aircraft_reg, origin, destination, gate, runway, detail'] },
      { name: 'bronze_aerodatabox_raw_<iata>', type: 'Delta · streaming table', writer: PIPELINE, cadence: '1 row / landed file',
        key: 'source_file', retention: 'all', readers: 'silver_flight_observation', names: UC('bronze_aerodatabox_raw_<iata>'),
        cols: ['airport', 'slot_start', 'fetched_at', 'request VARIANT', 'response VARIANT', 'source_file', 'ingested_at'] },
      { name: 'silver_flight_observation_<iata>', type: 'Delta · streaming table', writer: PIPELINE, cadence: '1 row / flight / fetch',
        key: 'airport, flight_date, flight_number, direction (+ fetched_at)', retention: 'all', readers: 'current + history tables',
        names: UC('silver_flight_observation_<iata>'),
        cols: ['airport', 'flight_date', 'flight_number', 'direction', 'callsign', 'airline', 'aircraft_type', 'aircraft_model', 'origin', 'destination', 'origin_name', 'destination_name', 'scheduled_utc (effective)', 'sched_time_utc', 'revised_time_utc', 'delay_minutes', 'api_gate', 'aircraft_reg', 'baggage_belt', 'api_status', 'fetched_at', 'slot_start', 'source_file'] },
      { name: 'silver_flight_schedule_<iata>', type: 'Delta · AUTO CDC SCD1 · CDF on', writer: PIPELINE, cadence: 'each pipeline run',
        key: 'PK airport, flight_date, flight_number, direction', retention: 'one current row per flight, all flights ever',
        readers: 'Lakebase synced table · Genie / AI-BI', names: UC('silver_flight_schedule_<iata>'),
        cols: ['same columns as observation (minus slot_start, source_file)', 'missing tail/belt/name never overwrite a known value'] },
      { name: 'silver_flight_schedule_history_<iata>', type: 'Delta · AUTO CDC SCD2', writer: PIPELINE, cadence: 'new version when status / times / gate / tail / type change',
        key: 'airport, flight_date, flight_number, direction + __START_AT', retention: 'every revision', readers: 'Genie / AI-BI',
        names: UC('silver_flight_schedule_history_<iata>'), cols: ['observation columns', '__START_AT', '__END_AT (NULL = current)'] },
      { name: 'sensor_readings_<iata>', type: 'Delta · streaming table', writer: PIPELINE, cadence: `${SENSOR_INTERVAL_S} s × 26 sensors (~449k rows/day/airport)`,
        key: 'airport, sensor_id, ts', retention: 'all', readers: 'Genie / AI-BI', names: UC('sensor_readings_<iata>'),
        cols: ['airport', 'sensor_id', 'ts', 'lon', 'lat', 'indoor', 'temperature (°C)', 'pax_density (pax/m²)', 'source_file', 'ingested_at'] },
      { name: 'sim_events_<iata>', type: 'Delta · streaming table', writer: PIPELINE, cadence: 'one row per lifecycle transition',
        key: 'airport, flight_id, event, ts', retention: 'all', readers: 'Genie / AI-BI (taxi, dwell, runway queue, turnaround)',
        names: UC('sim_events_<iata>'),
        cols: ['airport', 'ts', 'event (TOUCHDOWN · GATE_IN · GATE_OUT · TURNAROUND_HANDOVER · AT_GATE · PUSHBACK · HOLD_SHORT · RUNWAY_ENTER · TAKEOFF)', 'flight_id', 'direction', 'callsign', 'airline', 'aircraft_type', 'aircraft_reg', 'origin', 'destination', 'gate', 'runway', 'detail VARIANT (late_s, dwell_s, queue_wait_s, taxi_m, …)'] },
      { name: 'gold_departure_features_doh', type: 'Delta · ML features', writer: 'ml/train_delay_risk.py (weekly)', cadence: 'rebuilt each training run',
        key: 'airport, flight_date, flight_number', retention: 'latest training set', readers: 'model training',
        names: [`${UC_CATALOG}.airport_simulator_doh.gold_departure_features_doh`],
        cols: ['features as known 2 h before departure: hour_local, dow, is_wide, is_a380', 'airline / route / hour late rate + volume (28 d)', 'airport_dep_late_rate_prev2h', 'airport_arr_avg_delay_prev2h', 'deps_same_hour', 'has_inbound_link, inbound_delay_min', 'label (left 15+ min late)'] },
      { name: 'delay_risk_model', type: 'UC registered model (MLflow)', writer: 'ml/train_delay_risk.py', cadence: 'new version weekly',
        key: 'alias @champion', retention: 'all versions', readers: 'ml/score_delay_risk.py',
        names: [`${UC_CATALOG}.airport_simulator_doh.delay_risk_model`],
        cols: ['HistGradientBoosting, P(departure 15+ min late)', 'v2: AUC 0.761 vs baseline 0.556 · 79% precision at ≥ 50% (last 14 days)', 'point-in-time features: an outcome counts only after it happened', 'champion moves only if it beats the baseline'] },
      { name: 'gold_delay_risk_doh', type: 'Delta · ML scores (MERGE, CDF on)', writer: 'ml/score_delay_risk.py (hourly job)', cadence: 'hourly',
        key: 'PK airport, flight_date, flight_number, direction', retention: 'upcoming departures (next 6 h)', readers: 'Lakebase delay_risk_doh · Genie',
        names: [`${UC_CATALOG}.airport_simulator_doh.gold_delay_risk_doh`],
        cols: ['flight_number', 'airline', 'destination', 'sched_time_utc', 'risk (0–1)', 'risk_band (high ≥ 50% · medium ≥ 25%)', 'reasons (top 3 drivers)', 'model_version', 'scored_at', '+ the input features'] },
      { name: 'gold_delay_risk_log_doh / v_delay_risk_outcomes_doh', type: 'Delta log + view', writer: 'ml/score_delay_risk.py', cadence: 'hourly (append)',
        key: 'flight + scored_at', retention: 'all predictions', readers: 'Genie · model monitoring',
        names: [`${UC_CATALOG}.airport_simulator_doh.gold_delay_risk_log_doh`, `${UC_CATALOG}.airport_simulator_doh.v_delay_risk_outcomes_doh`],
        cols: ['every prediction ever made', 'view: 2-h-ahead prediction vs actual_delay_minutes / actually_late'] },
      { name: 'flight_schedule_legacy_doh', type: 'Delta · archive', writer: 'one-off (2026-11-26)', cadence: 'static',
        key: '—', retention: '98,302 rows · 2026-07-27 → 2026-11-26', readers: 'Genie / AI-BI (long history)',
        names: [`${UC_CATALOG}.airport_simulator_doh.flight_schedule_legacy_doh`], cols: ['the retired Lakebase flight_schedule, incl. sim_gate'] },
    ],
  },
  {
    store: 'Lakebase Postgres — serving', sub: `${LAKEBASE.instance} / ${LAKEBASE.database}`,
    items: [
      { name: 'flight_schedule_<iata>', type: 'Synced table (TRIGGERED, read-only)', writer: 'sync_to_lakebase.py (ingest job)', cadence: 'after each pipeline run (every 4 h)',
        key: 'PK airport, flight_date, flight_number, direction', retention: 'mirrors silver current', readers: `Generator (roster, 60 s) · App (board, dashboard, gates, 15 s)`,
        names: AIRPORTS.map((a) => `${a.schema}.flight_schedule_${a.sfx}`), cols: ['same columns as silver_flight_schedule_<iata>', 'airport_app has SELECT (re-granted every sync)'] },
      { name: 'sim_positions', type: 'Table (app-owned)', writer: 'Generator · flights task', cadence: '1 Hz upsert per live flight',
        key: 'PK flight_id · filtered by airport', retention: 'current state only (row deleted when the flight leaves)', readers: 'App reader loop (1 s) → WebSocket',
        names: ['public.sim_positions'], cols: ['flight_id', 'airport', 'callsign', 'airline', 'aircraft_type', 'aircraft_reg', 'origin', 'destination', 'direction', 'assigned_gate', 'lat', 'lon', 'heading_deg', 'speed_kt', 'phase', 'scheduled_utc', 'gate_arrival', 'runway', 'api_status', 'timeline jsonb (departure stages)', 'updated_at'] },
      { name: 'gate_occupancy', type: 'Table (app-owned)', writer: 'Generator · flights task', cadence: '1 Hz (full snapshot)',
        key: 'PK airport, gate_id', retention: 'current state', readers: 'App /api/gates (15 s), map snapshot',
        names: ['public.gate_occupancy'], cols: ['airport', 'gate_id', 'capacity_class (NARROW · WIDE · A380)', 'status', 'occupied_by', 'occupied_since', 'updated_at'] },
      { name: 'sim_heartbeat', type: 'Table (app-owned)', writer: 'Generator · flights task', cadence: '1 Hz',
        key: 'PK airport', retention: 'one row per airport', readers: 'App feed health (stale after 15 s)',
        names: ['public.sim_heartbeat'], cols: ['airport', 'updated_at', 'tick_count', 'live_flights', 'detail'] },
      { name: 'sim_gate_assignment', type: 'Table (app-owned)', writer: 'Generator · flights task', cadence: 'on each gate assignment (~hundreds/day)',
        key: 'PK airport, flight_date, flight_number, direction', retention: 'kept', readers: 'App (board gate, gate history, utilisation, heatmap) · Generator restart',
        names: ['public.sim_gate_assignment'], cols: ['airport', 'flight_date', 'flight_number', 'direction', 'sim_gate', 'assigned_at'] },
      { name: 'sensor_current', type: 'Table (app-owned)', writer: 'Generator · sensor task', cadence: `every ${SENSOR_INTERVAL_S} s`,
        key: 'PK airport, sensor_id', retention: 'latest reading per sensor', readers: 'App (2 s) → sensor_update',
        names: ['public.sensor_current'], cols: ['airport', 'sensor_id', 'lon', 'lat', 'temperature', 'pax_density', 'updated_at'] },
      { name: 'delay_risk_doh', type: 'Synced table (TRIGGERED, read-only)', writer: 'sync_to_lakebase.py (hourly scoring job)', cadence: 'after each scoring run (hourly)',
        key: 'PK airport, flight_date, flight_number, direction', retention: 'mirrors gold_delay_risk_doh', readers: 'App: schedule RISK column, dashboard panel, alert banner',
        names: ['airport_simulator_doh.delay_risk_doh'], cols: ['same columns as gold_delay_risk_doh'] },
      { name: 'sensor_readings', type: 'Table (app-owned)', writer: 'Generator · sensor task', cadence: `every ${SENSOR_INTERVAL_S} s × 26 sensors`,
        key: 'PK airport, sensor_id, ts', retention: 'rolling 24 h (pruned every 5 min)', readers: 'App /api/sensors/history (live per-sensor charts)',
        names: ['public.sensor_readings'], cols: ['airport', 'sensor_id', 'ts', 'temperature', 'pax_density', 'indoor'] },
    ],
  },
];

function TableCatalog() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginBottom: 24 }}>
      {CATALOG.map((g) => (
        <div key={g.store} className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 700, fontSize: 13 }}>{g.store}</span>
            <span className="muted mono" style={{ fontSize: 11 }}>{g.sub}</span>
          </div>
          {g.items.map((t) => (
            <details key={t.name} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
              <summary style={{ cursor: 'pointer', padding: '9px 14px', display: 'grid',
                                gridTemplateColumns: 'minmax(220px, 1.4fr) minmax(160px, 1fr) minmax(200px, 1.3fr) minmax(200px, 1.4fr)',
                                gap: 12, alignItems: 'baseline', fontSize: 12, listStyle: 'none' }}>
                <span className="mono" style={{ fontWeight: 700 }}>▸ {t.name}</span>
                <span className="muted">{t.type}</span>
                <span>{t.cadence}</span>
                <span className="muted" style={{ fontSize: 11 }}>{t.key}</span>
              </summary>
              <div style={{ padding: '4px 14px 14px 30px', display: 'grid', gridTemplateColumns: '120px 1fr', rowGap: 6, columnGap: 12, fontSize: 12 }}>
                <span className="muted">Written by</span><span>{t.writer}</span>
                <span className="muted">Read by</span><span>{t.readers}</span>
                <span className="muted">Retention</span><span>{t.retention}</span>
                <span className="muted">Objects</span>
                <span className="mono" style={{ fontSize: 11 }}>{t.names.map((n) => <div key={n}>{n}</div>)}</span>
                <span className="muted">Columns</span>
                <span style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                  {t.cols.map((c) => (
                    <span key={c} className="mono" style={{ fontSize: 10.5, padding: '2px 7px', borderRadius: 5,
                          background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border-subtle)' }}>{c}</span>
                  ))}
                </span>
              </div>
            </details>
          ))}
        </div>
      ))}
      <div className="muted" style={{ fontSize: 11 }}>
        <span className="mono">&lt;iata&gt;</span> = lower-case airport code (doh, lhr). Click a row for columns, writers and readers.
        Live tables are shared across airports and keyed by <span className="mono">airport</span>; only {ACTIVE.iata} is simulated today.
      </div>
    </div>
  );
}

const TABLE = { width: '100%', borderCollapse: 'collapse', fontSize: 12 };
const TH = { textAlign: 'left', padding: '9px 12px', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase',
             color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', fontWeight: 600 };
const TD = { padding: '9px 12px', borderBottom: '1px solid var(--border-subtle)', verticalAlign: 'top', lineHeight: 1.45 };

function SectionLabel({ icon, children }) {
  return (
    <div className="muted" style={{ fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
      {icon} {children}
    </div>
  );
}

function Legend() {
  return (
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 11, marginBottom: 12, alignItems: 'center' }}>
      {Object.entries(STATUS).map(([k, v]) => (
        <span key={k} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: v.color }} />
          <span className="muted">{v.label}</span>
        </span>
      ))}
      <span className="muted" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <svg width="26" height="6"><line x1="0" y1="3" x2="26" y2="3" stroke="#38bdf8" strokeWidth="1.6" /></svg> live
        <svg width="26" height="6"><line x1="0" y1="3" x2="26" y2="3" stroke={STATUS.batch.color} strokeWidth="1.6" strokeDasharray="6 4" /></svg> batch
      </span>
      <span className="muted" style={{ marginLeft: 'auto' }}>Moving dots = data flowing now · refreshes every {HEALTH_MS / 1000}s</span>
    </div>
  );
}

function StatusDot({ x, y, s }) {
  const c = STATUS[s].color;
  return (
    <g>
      {s === 'ok' && (
        <circle cx={x} cy={y} r="4" fill={c} opacity="0.35">
          <animate attributeName="r" values="4;8;4" dur="2s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.35;0;0.35" dur="2s" repeatCount="indefinite" />
        </circle>
      )}
      <circle cx={x} cy={y} r="3.5" fill={c} />
    </g>
  );
}

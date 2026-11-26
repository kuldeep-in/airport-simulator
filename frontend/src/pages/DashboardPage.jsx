import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LayoutDashboard } from 'lucide-react';
import { useSimStore } from '../store/useSimStore';
import { AIRPORT } from '../lib/airport.js';
import { isWidebody } from '../lib/gateStats.js';
import StatTile from '../components/charts/StatTile';
import Sparkline from '../components/charts/Sparkline';
import Gauge from '../components/charts/Gauge';
import Donut from '../components/charts/Donut';
import Histogram from '../components/charts/Histogram';
import LineTrend from '../components/charts/LineTrend';
import TimeTrend from '../components/charts/TimeTrend';
import Heatmap from '../components/charts/Heatmap';
import DemandCurve from '../components/charts/DemandCurve';
import DepartureWaterfall from '../components/charts/DepartureWaterfall';
import HBarList from '../components/charts/HBarList';
import { PAGE_BG } from '../components/boardTheme.jsx';

const POLL_MS = 15000;              // Lakebase-derived charts
const LIVE_MS = 60000;              // "as of now" recompute — every minute
const QUEUE_MS = 5000;              // departure-queue waterfall (stage timings move each second)
const RUNWAY_CAPACITY_PER_HR = 45;  // single-runway departure ceiling (see docs)
const MAX_SPARK = 30;               // rolling points kept per live sparkline
const SENSOR_TREND_MS = 5000;       // live tracking poll — matches the 5 s sensor cadence
const TREND_MIN = 15, TREND_BUCKET_MIN = 5;   // last 15 minutes, in 5-minute windows

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

// A dashboard card.
//   `wide`  — span the full row (grid-column: 1/-1).
//   `span`  — span N tracks, clamped by the grid so it never overflows on narrow
//             viewports (`span N` is capped implicitly because the explicit grid
//             defines the track count per breakpoint).
// The card is a column flexbox and the body gets flex:1, so cards sitting in the
// same row stretch their CONTENT to equal height instead of leaving a ragged gap
// under the shorter one (grid already equalises the boxes; this fills them).
function Section({ title, children, wide, span, tail, right }) {
  // The span is published as a data attribute rather than an inline style so the
  // stylesheet owns every breakpoint (inline styles would win over media queries
  // and freeze the desktop layout on small screens). See `.dash-grid` in index.css.
  // `tail` marks the last card of a three-across row: at the 2-track tablet
  // breakpoint it stretches full width so the triad doesn't leave a half-empty row.
  const size = wide ? 'full' : String(span || 2);
  return (
    <section className="card dash-cell" data-span={size} data-tail={tail ? 'true' : undefined}
             aria-label={title}
             style={{ padding: 'var(--space-xl)', display: 'flex', flexDirection: 'column' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                       marginBottom: 'var(--space-lg)', gap: 'var(--space-md)' }}>
        <h3 className="dash-title" style={{ margin: 0 }}>{title}</h3>
        {right}
      </header>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
        {children}
      </div>
    </section>
  );
}

// A live stat tile: big number + rolling sparkline underneath. The metric colour
// drives a left accent rail (--kpi-accent) rather than tinting the number itself,
// so the value keeps full white contrast while the row stays scannable by hue.
function LiveTile({ label, value, unit, color, sub, spark }) {
  return (
    <div className="card kpi-tile" style={{ padding: '10px 12px 10px 15px', minWidth: 140,
                                            flex: '1 1 140px', '--kpi-accent': color || 'var(--accent-blue)' }}>
      <div className="dash-title" style={{ fontSize: 10 }}>{label}</div>
      <div style={{ marginTop: 'var(--space-sm)', display: 'flex', alignItems: 'baseline', gap: 3 }}>
        <span className="kpi-value" style={{ fontSize: 23, fontWeight: 600, color: color || 'var(--text-primary)', lineHeight: 1.1 }}>{value}</span>
        {unit && <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{unit}</span>}
      </div>
      {spark && spark.length > 1 && <div style={{ marginTop: 'var(--space-sm)' }}><Sparkline data={spark} color={color || '#38bdf8'} /></div>}
      {sub && <div style={{ fontSize: 10, marginTop: 3, color: 'var(--text-dim)' }}>{sub}</div>}
    </div>
  );
}

const DELAYED = new Set(['Delayed']);
const CANCELLED = new Set(['Canceled', 'Cancelled', 'Diverted']);
function statusBucket(s) {
  if (CANCELLED.has(s)) return 'cancelled';
  if (DELAYED.has(s)) return 'delayed';
  return 'ontime';
}

const TAXI_PHASES = new Set(['TAXIING_OUT', 'HOLDING_SHORT']);

export default function DashboardPage() {
  const [schedule, setSchedule] = useState({ inbound: [], outbound: [] });
  const [gates, setGates] = useState([]);
  const [movementRows, setMovementRows] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [depQueue, setDepQueue] = useState([]);
  const [nowTick, setNowTick] = useState(0);   // forces a 60s "as of now" recompute

  // Live store (real-time, per WS tick)
  const positions = useSimStore((s) => s.positions);
  const storeGates = useSimStore((s) => s.gates);
  const sensorReadings = useSimStore((s) => s.sensorReadings);
  const simTimeIso = useSimStore((s) => s.simTime);
  const { tz, tz_abbr: tzAbbr } = AIRPORT;
  const now = simTimeIso ? new Date(simTimeIso).getTime() : Date.now();

  const refresh = useCallback(() => {
    fetchJSON('/api/schedule').then(setSchedule).catch(() => {});
    fetchJSON('/api/gates').then((d) => setGates(d.gates || [])).catch(() => {});
    fetchJSON('/api/movements').then((d) => setMovementRows(d.movements || [])).catch(() => {});
    fetchJSON('/api/analytics').then(setAnalytics).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const poll = setInterval(refresh, POLL_MS);
    return () => clearInterval(poll);
  }, [refresh]);

  useEffect(() => {
    const load = () => fetchJSON('/api/departure-queue').then((d) => setDepQueue(d.departures || [])).catch(() => {});
    load();
    const poll = setInterval(load, QUEUE_MS);
    return () => clearInterval(poll);
  }, []);

  // "As of now" clock recompute every minute (windows shift, now-marker sweeps).
  useEffect(() => {
    const t = setInterval(() => setNowTick((n) => n + 1), LIVE_MS);
    return () => clearInterval(t);
  }, []);

  const inbound = schedule.inbound || [];
  const outbound = schedule.outbound || [];
  const flights = useMemo(() => [...inbound, ...outbound], [inbound, outbound]);

  // ── LIVE: ground movement counts from the WS store (per tick) ──────────────
  const posList = useMemo(() => Object.values(positions), [positions]);
  const live = useMemo(() => {
    let taxiing = 0, holding = 0, runway = 0, arriving = 0, parked = 0;
    let runwayOccupant = null;
    for (const p of posList) {
      if (p.phase === 'TAXIING_OUT') taxiing++;
      else if (p.phase === 'HOLDING_SHORT') holding++;
      else if (p.phase === 'RUNWAY') { runway++; runwayOccupant = p.flight_id; }
      else if (p.phase === 'ARRIVING') arriving++;
      else if (p.phase === 'PARKED' || p.phase === 'GATE_PARKED') parked++;
    }
    const queueDepth = taxiing + holding;
    const gatesArr = Object.values(storeGates);
    const gatesOcc = gatesArr.filter((g) => g.status === 'OCCUPIED').length;
    return { taxiing, holding, runway, arriving, parked, queueDepth, runwayOccupant,
             onGround: posList.length, gatesOcc, gatesTotal: gatesArr.length || 61 };
  }, [posList, storeGates]);

  // ── LIVE: terminal environment from the sensor feed (sensor_update, ~5 s) ──
  // Overall average plus the indoor (terminal, AC) vs outdoor (apron/tarmac)
  // split — the two populations sit ~5°C apart, so a single blended number hides
  // what's actually happening on either side.
  const env = useMemo(() => {
    const all = Object.values(sensorReadings);
    const avg = (list, k) => (list.length ? list.reduce((s, r) => s + (r[k] || 0), 0) / list.length : null);
    const inside = all.filter((r) => r.indoor);
    const outside = all.filter((r) => !r.indoor);
    const busiest = all.reduce((b, r) => (!b || (r.pax_density || 0) > (b.pax_density || 0) ? r : b), null);
    const hottest = all.reduce((b, r) => (!b || (r.temperature || 0) > (b.temperature || 0) ? r : b), null);
    return {
      n: all.length,
      pax: avg(all, 'pax_density'), paxIn: avg(inside, 'pax_density'), paxOut: avg(outside, 'pax_density'),
      temp: avg(all, 'temperature'), tempIn: avg(inside, 'temperature'), tempOut: avg(outside, 'temperature'),
      busiest, hottest,
    };
  }, [sensorReadings]);

  // Live tracking: the last 15 minutes of readings from the SERVER (Lakebase
  // sensor_readings, averaged per 5 s reading) + 5-minute window averages. Server-side
  // so the trend survives reloads and every viewer sees the same history.
  const [trend, setTrend] = useState({ points: [], buckets: [] });
  useEffect(() => {
    const load = () => fetchJSON(`/api/sensors/trend?minutes=${TREND_MIN}&bucket_minutes=${TREND_BUCKET_MIN}`)
      .then(setTrend).catch(() => {});
    load();
    const t = setInterval(load, SENSOR_TREND_MS);
    return () => clearInterval(t);
  }, []);

  // Rolling sparkline buffers for live tiles (client-side; reset on reload).
  const buffers = useRef({ onGround: [], queueDepth: [], gatesOcc: [] });
  useEffect(() => {
    const b = buffers.current;
    const push = (k, v) => { b[k].push(v); if (b[k].length > MAX_SPARK) b[k].shift(); };
    push('onGround', live.onGround);
    push('queueDepth', live.queueDepth);
    push('gatesOcc', live.gatesOcc);
  }, [live]);

  // ── LIVE "as of now" windows from the schedule (time columns vs sim clock) ──
  const asOfNow = useMemo(() => {
    void nowTick; // recompute each minute
    const H = 3600000;
    let depNext = 0, arrNext = 0, depThisHour = 0, overdue = 0, launched60 = 0;
    let delaySum = 0, delayN = 0, onTimeWin = 0, winN = 0, worst = null;
    for (const f of flights) {
      const isArr = (f.destination === 'DOH') || (f.direction === 'arrival');
      const iso = isArr ? f.scheduled_arr : f.scheduled_dep;
      if (!iso) continue;
      const t = new Date(iso).getTime();
      const dt = t - now;
      if (!isArr) {
        if (dt >= 0 && dt <= H) depNext++;
        if (dt >= 0 && dt <= H) depThisHour++;
        if (dt < 0 && dt >= -H) launched60++;
        if (dt < 0 && f.phase == null && !['Departed'].includes(f.api_status)) overdue++;
      } else if (dt >= 0 && dt <= H) arrNext++;
      // delay from revised vs scheduled, for flights within ±1h of now
      if (Math.abs(dt) <= H && f.revised_time_utc && f.sched_time_utc) {
        const d = (new Date(f.revised_time_utc).getTime() - new Date(f.sched_time_utc).getTime()) / 60000;
        delaySum += d; delayN++; winN++;
        if (d <= 15) onTimeWin++;
        if (!worst || d > worst.min) worst = { id: f.callsign || f.id, min: Math.round(d) };
      }
    }
    return {
      depNext, arrNext, depThisHour, overdue, launched60,
      avgDelay: delayN ? Math.round(delaySum / delayN) : 0,
      onTimePct: winN ? Math.round((onTimeWin / winN) * 100) : 100,
      worst,
    };
  }, [flights, now, nowTick]);

  // ── LIVE demand curve: arrivals/departures per 30-min bucket, now−1h→now+3h ──
  // Sourced from Lakebase (`/api/analytics.demand_curve`, over the persisted
  // flight_schedule dataset). It CANNOT be derived from /api/schedule: those
  // rosters are deliberately forward-looking (past flights are dropped ~10-15 min
  // after their slot), so every bucket left of NOW came out zero — the flat line
  // on the left half of the chart. The client-side computation is kept only as a
  // fallback for the first render before /api/analytics resolves.
  const demand = useMemo(() => {
    void nowTick;
    const BUCKET = 30 * 60000, N = 8, START = now - 1 * 3600000;
    const fmt = (ms) => new Date(ms).toLocaleTimeString('en-GB',
      { hour: '2-digit', minute: '2-digit', timeZone: tz });
    const capacity = Math.round(RUNWAY_CAPACITY_PER_HR / 2); // per 30-min bucket

    const dc = analytics?.demand_curve;
    if (dc?.length) {
      // Backend buckets already span the full window (zeros included) and are
      // anchored to stable :00/:30 boundaries. Relabel in Qatar time here so the
      // axis matches the rest of the dashboard.
      const rows = dc.map((r) => ({ label: fmt(new Date(r.start).getTime()),
                                    a: r.arrivals, b: r.departures }));
      // Place the NOW marker at its true position within the returned window
      // rather than assuming the midpoint (the first bucket is floored to a :00/:30
      // boundary, so `now` sits slightly right of centre).
      const first = new Date(dc[0].start).getTime();
      const span = dc.length * BUCKET;
      const nowFrac = Math.min(1, Math.max(0, (now - first) / span));
      return { rows, nowFrac, capacity };
    }

    const rows = Array.from({ length: N }, (_, i) => (
      { label: fmt(START + i * BUCKET), a: 0, b: 0 }));
    for (const f of flights) {
      const isArr = (f.destination === 'DOH') || (f.direction === 'arrival');
      const iso = isArr ? f.scheduled_arr : f.scheduled_dep;
      if (!iso) continue;
      const idx = Math.floor((new Date(iso).getTime() - START) / BUCKET);
      if (idx >= 0 && idx < N) rows[idx][isArr ? 'a' : 'b']++;
    }
    return { rows, nowFrac: 0.25, capacity };
  }, [analytics, flights, now, nowTick]);

  // ── Near-live status donut / fleet (from live window) ──────────────────────
  const onTime = useMemo(() => {
    const c = { ontime: 0, delayed: 0, cancelled: 0 };
    for (const f of flights) c[statusBucket(f.api_status)]++;
    const total = c.ontime + c.delayed + c.cancelled;
    return { ...c, total, pct: total ? Math.round((c.ontime / total) * 100) : 0 };
  }, [flights]);

  const fleet = useMemo(() => {
    let wide = 0, narrow = 0;
    for (const f of flights) { if (isWidebody(f.aircraft_type)) wide++; else narrow++; }
    return { wide, narrow };
  }, [flights]);

  const movements = useMemo(
    () => movementRows.map((r) => ({ label: r.label, a: r.arrivals, b: r.departures })), [movementRows]);

  // ── Historical analytics (Lakebase) → chart shapes ─────────────────────────
  const hist = useMemo(() => {
    if (!analytics) return null;
    const curHourLabel = new Date(now).toISOString().slice(11, 13);
    // delay heatmap: rows = ARR/DEP, cols = hours
    const dbh = analytics.delay_by_hour || [];
    const cols = dbh.map((r) => ({ key: r.label, label: r.label }));
    const delayVals = {};
    let delayMax = 1;
    for (const r of dbh) {
      delayVals[`ARR|${r.label}`] = Math.max(0, r.arr_delay);
      delayVals[`DEP|${r.label}`] = Math.max(0, r.dep_delay);
      delayMax = Math.max(delayMax, r.arr_delay, r.dep_delay);
    }
    // gate heatmap: rows = concourse, cols = hours
    const gh = analytics.gate_hour_heatmap || [];
    const concourses = [...new Set(gh.map((r) => r.concourse))].sort();
    const ghCols = [...new Set(gh.map((r) => r.label))].sort();
    const ghVals = {}; let ghMax = 1;
    for (const r of gh) { ghVals[`${r.concourse}|${r.label}`] = r.n; ghMax = Math.max(ghMax, r.n); }
    return {
      curHourLabel,
      delayDist: analytics.delay_distribution || [],
      // /api/analytics returns {label, pct, n}, but LineTrend plots `d.value` —
      // without this mapping every point read as undefined→0 and the chart drew a
      // flat line along the axis while the underlying data was 55-100%.
      onTimeTrend: (analytics.ontime_trend || []).map((r) => ({ label: r.label, value: r.pct })),
      delayRows: [{ key: 'ARR', label: 'ARR' }, { key: 'DEP', label: 'DEP' }],
      delayCols: cols, delayVals, delayMax,
      ghRows: concourses.map((c) => ({ key: c, label: c })),
      ghCols: ghCols.map((c) => ({ key: c, label: c })), ghVals, ghMax,
      topRoutes: analytics.top_routes || [],
      airlines: analytics.airline_share || [],
    };
  }, [analytics, now]);

  const clock = simTimeIso
    ? new Date(simTimeIso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: tz })
    : '—';
  const hasData = flights.length > 0 || gates.length > 0;

  const DELAY_COLORS = ['#22c55e', '#4ade80', '#fbbf24', '#f97316', '#ef4444'];

  return (
    <div style={{ height: '100%', width: '100%', overflow: 'auto', background: PAGE_BG }}>
      {/* Sticky header gets a backdrop blur + shadow so dense content scrolling
          under it stays visually separated instead of colliding with the title. */}
      <header className="panel" style={{
        height: 58, display: 'flex', alignItems: 'center', padding: '0 var(--space-xl)', borderRadius: 0,
        borderLeft: 'none', borderRight: 'none', borderTop: 'none', position: 'sticky', top: 0, zIndex: 2,
        background: 'rgba(17,24,39,0.86)', backdropFilter: 'blur(10px)',
        boxShadow: '0 1px 0 rgba(255,255,255,0.04), 0 4px 16px rgba(0,0,0,0.28)',
      }}>
        <LayoutDashboard size={20} className="accent" aria-hidden="true" />
        <div style={{ marginLeft: 'var(--space-lg)', flex: 1 }}>
          <h1 style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.2, margin: 0, letterSpacing: '-0.01em' }}>
            Operations Dashboard
          </h1>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
            <span className="mono">{inbound.length}</span> inbound
            <span style={{ opacity: 0.4 }}> · </span>
            <span className="mono">{outbound.length}</span> outbound
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)' }}>
          <span className="live-dot" aria-hidden="true" />
          <span className="muted" style={{ fontSize: 10, letterSpacing: '0.08em', fontWeight: 600 }}>LIVE</span>
          <span className="mono" style={{ fontSize: 14, color: 'var(--text-primary)', marginLeft: 'var(--space-sm)' }}>
            {clock}
            <span style={{ fontSize: 9, color: 'var(--text-muted)', marginLeft: 3 }}>{tzAbbr}</span>
          </span>
        </div>
      </header>

      {/* 1400px per the generated dashboard page override (was 1280) — buys back
          ~120px for the 24-column heatmaps without pushing into ultrawide sprawl. */}
      <div style={{ padding: 'var(--space-xl)', maxWidth: 1400, margin: '0 auto' }}>
        {!hasData && <div className="muted" style={{ padding: 24, textAlign: 'center' }}>Loading operations data…</div>}

        {/* ── ROW 1: LIVE PULSE ── */}
        <div style={{ display: 'flex', gap: 'var(--space-md)', flexWrap: 'wrap', marginBottom: 'var(--space-lg)' }}>
          <LiveTile label="On ground" value={live.onGround} color="#34d399" spark={buffers.current.onGround} />
          <LiveTile label="Dep queue" value={live.queueDepth} color="#fbbf24" sub={`${live.taxiing} taxi · ${live.holding} hold`} spark={buffers.current.queueDepth} />
          <LiveTile label="Dep next 1h" value={asOfNow.depNext} color="#f97316" />
          <LiveTile label="Arr next 1h" value={asOfNow.arrNext} color="#38bdf8" />
          <LiveTile label="Avg delay now" value={asOfNow.avgDelay} unit="min" color={asOfNow.avgDelay > 15 ? '#ef4444' : '#22c55e'} />
          <LiveTile label="On-time (window)" value={`${asOfNow.onTimePct}%`} color={asOfNow.onTimePct >= 80 ? '#22c55e' : '#fbbf24'} />
          <LiveTile label="Overdue" value={asOfNow.overdue} color={asOfNow.overdue > 0 ? '#ef4444' : 'var(--text)'} />
          <LiveTile label="Pax density" value={env.pax == null ? '—' : env.pax.toFixed(2)} unit="pax/m²" color="#a78bfa"
                    sub={env.n ? `${env.n} sensors` : 'awaiting sensors'} spark={trend.points.map((d) => d.pax)} />
          <LiveTile label="Avg temp" value={env.temp == null ? '—' : env.temp.toFixed(1)} unit="°C" color="#fb7185"
                    sub={env.tempIn != null ? `in ${env.tempIn.toFixed(1)} · out ${env.tempOut.toFixed(1)}` : undefined}
                    spark={trend.points.map((d) => d.temp)} />
        </div>

        {/* A fixed 6-track grid rather than `auto-fit, minmax(320px, 1fr)`.
            auto-fit derived the column count from the container width, so the
            full-width (`1 / -1`) cards landed mid-row and pushed the remaining
            tracks empty — that's where the blank patches came from (e.g. beside
            "Delay distribution" and the large void next to "Fleet mix"). With an
            explicit track count, each card declares its own span and every row is
            authored to sum to exactly 6: 6 | 2+2+2 | 6 | 3+3 | 2+2+2 | 6.
            6 divides by both 2 and 3, so halves and thirds both tile cleanly.
            `dense` packing backfills any hole a wrapped span would leave. */}
        <div className="dash-grid">
          {/* ── HERO: live demand curve (full width) ── */}
          <Section title="Live demand — arrivals vs departures (now −1h / +3h)" wide>
            <DemandCurve data={demand.rows} nowFrac={demand.nowFrac} capacity={demand.capacity} />
          </Section>

          {/* ── Departure queue waterfall (full width) ── */}
          <Section title="Departure queue — current & upcoming" wide>
            <DepartureWaterfall rows={depQueue} now={now} tz={tz} />
          </Section>

          {/* ── Row: live terminal environment (sensor feed), equal halves ── */}
          <Section span={3} title="Pax density — live · last 15 min" right={<LiveBadge n={env.n} />}>
            <EnvPanel color="#a78bfa" unit=" pax/m²" digits={2} trend={trend} metric="pax" tz={tz}
              overall={env.pax} inside={env.paxIn} outside={env.paxOut}
              peak={env.busiest && { id: env.busiest.sensor_id, value: env.busiest.pax_density, label: 'Busiest' }} />
          </Section>

          <Section span={3} title="Average temperature — live · last 15 min" right={<LiveBadge n={env.n} />}>
            <EnvPanel color="#fb7185" unit="°C" digits={1} trend={trend} metric="temp" tz={tz}
              overall={env.temp} inside={env.tempIn} outside={env.tempOut}
              peak={env.hottest && { id: env.hottest.sensor_id, value: env.hottest.temperature, label: 'Hottest' }} />
          </Section>

          {/* ── Row: the three "right now" summaries, equal thirds ── */}
          <Section span={2} title="Runway 34R pressure (this hour)"
            right={<span style={{ fontSize: 10, color: live.runway ? '#ef4444' : '#22c55e', fontWeight: 700 }}>
              {live.runway ? `● ${live.runwayOccupant || 'ROLLING'}` : '○ IDLE'}</span>}>
            <Gauge value={asOfNow.depThisHour} max={RUNWAY_CAPACITY_PER_HR} unit={`/ ${RUNWAY_CAPACITY_PER_HR} cap`} label={`${asOfNow.launched60} launched last 60m`} />
          </Section>

          <Section span={2} title="Status mix (live window)">
            <Donut centerLabel={`${onTime.pct}%`} segments={[
              { label: 'On-time', value: onTime.ontime, color: '#22c55e' },
              { label: 'Delayed', value: onTime.delayed, color: '#f97316' },
              { label: 'Cancelled', value: onTime.cancelled, color: '#ef4444' },
            ]} />
          </Section>

          <Section span={2} tail title="Fleet mix (live window)">
            <Donut centerLabel={fleet.wide + fleet.narrow} segments={[
              { label: 'Widebody', value: fleet.wide, color: '#38bdf8' },
              { label: 'Narrowbody', value: fleet.narrow, color: '#a78bfa' },
            ]} />
          </Section>

          {/* ── Delay by hour heatmap (needs the full 24 columns of width) ── */}
          <Section title="Avg delay by hour — ARR / DEP (min)" wide>
            {hist ? <Heatmap rows={hist.delayRows} cols={hist.delayCols} values={hist.delayVals}
                             max={hist.delayMax} hue="delay" highlightCol={hist.curHourLabel} /> : <Loading />}
          </Section>

          {/* ── Row: the two bar charts, equal halves (both are 12-13 bar series,
                 so they read better wide than squeezed into a third) ── */}
          <Section span={3} title="Movements per hour — last 12h">
            <MovementsBars data={movements} />
          </Section>

          <Section span={3} title="Delay distribution (24h)">
            {hist ? <Histogram data={hist.delayDist} colors={DELAY_COLORS} /> : <Loading />}
          </Section>

          {/* ── Row: trend + the two ranked lists, equal thirds ── */}
          <Section span={2} title="On-time % trend (12h)">
            {hist ? <LineTrend data={hist.onTimeTrend} unit="%" domain={[0, 100]} color="#22c55e" /> : <Loading />}
          </Section>

          <Section span={2} title="Top destinations (by delay)">
            {hist ? <HBarList items={hist.topRoutes.map((r) => ({
              label: r.label, value: r.value,
              color: r.delay > 30 ? '#ef4444' : r.delay > 15 ? '#f97316' : '#4ade80',
              sub: `${Math.round(r.delay)}m`,
            }))} /> : <Loading />}
          </Section>

          <Section span={2} tail title="Airline share (24h)">
            {hist ? <HBarList items={hist.airlines} color="#a78bfa" /> : <Loading />}
          </Section>

          {/* ── Gate × hour heatmap (full width, same 24-hour axis) ── */}
          <Section title="Gate occupancy by concourse × hour" wide>
            {hist ? <Heatmap rows={hist.ghRows} cols={hist.ghCols} values={hist.ghVals}
                             max={hist.ghMax} hue="load" highlightCol={hist.curHourLabel} /> : <Loading />}
          </Section>
        </div>
      </div>
    </div>
  );
}

function LiveBadge({ n }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, color: 'var(--text-muted)' }}>
      <span className="live-dot" aria-hidden="true" />{n ? `${n} sensors` : 'no feed'}
    </span>
  );
}

// Current overall value + terminal/apron split + peak sensor, over the server-side
// live trend: last 15 min of 5 s readings (overall bold, terminal/apron thin) with
// the three 5-minute window averages labelled in their bands.
function EnvPanel({ overall, inside, outside, peak, trend, metric, tz, color, unit, digits }) {
  const fmt = (v) => (v == null ? '—' : v.toFixed(digits));
  if (overall == null) return <div className="muted" style={{ fontSize: 11, padding: 12 }}>Waiting for sensor feed…</div>;
  const Stat = ({ label, value, accent }) => (
    <div style={{ minWidth: 0 }}>
      <div className="dash-title" style={{ fontSize: 10 }}>{label}</div>
      <div className="mono" style={{ fontSize: accent ? 22 : 16, fontWeight: 600, color: accent ? color : 'var(--text-primary)', lineHeight: 1.2 }}>
        {value}<span style={{ fontSize: 10, color: 'var(--text-muted)', fontWeight: 400 }}>{unit}</span>
      </div>
    </div>
  );
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 'var(--space-md)', marginBottom: 'var(--space-lg)' }}>
        <Stat label="Overall" value={fmt(overall)} accent />
        <Stat label="Terminal" value={fmt(inside)} />
        <Stat label="Apron" value={fmt(outside)} />
        {peak && <Stat label={`${peak.label} · ${peak.id}`} value={fmt(peak.value)} />}
      </div>
      <TimeTrend points={trend.points} buckets={trend.buckets} bucketKey={metric} nowIso={trend.now}
                 windowMin={TREND_MIN} bucketMin={TREND_BUCKET_MIN} unit={unit.trim() === '°C' ? '°C' : ''} digits={digits} tz={tz}
                 series={[
                   { key: metric, color, width: 2.2, label: 'All sensors' },
                   { key: `${metric}_in`, color: '#38bdf8', width: 1.1, opacity: 0.8, label: 'Terminal' },
                   { key: `${metric}_out`, color: '#fbbf24', width: 1.1, dash: '4 3', opacity: 0.8, label: 'Apron' },
                 ]} />
    </div>
  );
}

function Loading() {
  return <div className="muted" style={{ fontSize: 11, padding: 12 }}>Loading…</div>;
}

// Small stacked bars for historical movements (arrivals + departures per hour).
function MovementsBars({ data = [] }) {
  const max = Math.max(1, ...data.map((d) => (d.a || 0) + (d.b || 0)));
  const barW = 100 / Math.max(1, data.length);
  const H = 150;
  return (
    <div>
      {/* A stacked chart with no legend was ambiguous — which colour is which had
          to be inferred. Legend matches the DemandCurve colour mapping exactly
          (blue = arrivals, amber = departures) for cross-chart consistency. */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 6, fontSize: 11 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: '#38bdf8' }} />
          <span className="muted">Arrivals</span>
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: '#fbbf24' }} />
          <span className="muted">Departures</span>
        </span>
      </div>
      <svg width="100%" height={H} preserveAspectRatio="none" style={{ display: 'block' }}
           role="img" aria-label={data.map((d) => `${d.label}h: ${d.a} arrivals, ${d.b} departures`).join('; ')}>
        <line x1="0" y1={H - 18} x2="100%" y2={H - 18} stroke="rgba(255,255,255,0.07)" strokeWidth="1" />
        {data.map((d, i) => {
          const hTot = ((d.a + d.b) / max) * (H - 18);
          const hA = (d.a / max) * (H - 18);
          const x = `${i * barW + barW * 0.15}%`, w = `${barW * 0.7}%`, yTop = H - 18 - hTot;
          return (
            <g key={i}>
              {d.b > 0 && <rect x={x} y={yTop} width={w} height={hTot - hA} fill="#fbbf24" rx="1" />}
              {d.a > 0 && <rect x={x} y={H - 18 - hA} width={w} height={hA} fill="#38bdf8" rx="1" />}
              <text x={`${i * barW + barW * 0.5}%`} y={H - 5} textAnchor="middle" fill="var(--text-muted)" fontSize="8" fontFamily="var(--font-mono)">{d.label}</text>
              <rect x={x} y={0} width={w} height={H} fill="transparent">
                <title>{`${d.label}:00 — ${d.a} arr · ${d.b} dep (${d.a + d.b} total)`}</title>
              </rect>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

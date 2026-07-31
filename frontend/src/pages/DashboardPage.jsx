import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LayoutDashboard } from 'lucide-react';
import { useSimStore } from '../store/useSimStore';
import { isWidebody } from '../lib/gateStats.js';
import StatTile from '../components/charts/StatTile';
import Sparkline from '../components/charts/Sparkline';
import Gauge from '../components/charts/Gauge';
import Donut from '../components/charts/Donut';
import Histogram from '../components/charts/Histogram';
import LineTrend from '../components/charts/LineTrend';
import Heatmap from '../components/charts/Heatmap';
import DemandCurve from '../components/charts/DemandCurve';
import HBarList from '../components/charts/HBarList';

const POLL_MS = 15000;              // Lakebase-derived charts
const LIVE_MS = 60000;              // "as of now" recompute — every minute
const RUNWAY_CAPACITY_PER_HR = 45;  // single-runway departure ceiling (see docs)
const MAX_SPARK = 30;               // rolling points kept per live sparkline

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function Section({ title, children, wide, right }) {
  return (
    <div className="card" style={{ padding: 14, gridColumn: wide ? '1 / -1' : undefined }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <div className="muted" style={{ fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase' }}>{title}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

// A live stat tile: big number + rolling sparkline underneath.
function LiveTile({ label, value, unit, color, sub, spark }) {
  return (
    <div className="card" style={{ padding: '12px 14px', minWidth: 140, flex: '1 1 140px' }}>
      <div className="muted" style={{ fontSize: 10, letterSpacing: 0.5, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ marginTop: 6, display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span style={{ fontSize: 24, fontWeight: 700, color: color || 'var(--text)', lineHeight: 1 }}>{value}</span>
        {unit && <span className="muted" style={{ fontSize: 12 }}>{unit}</span>}
      </div>
      {spark && spark.length > 1 && <div style={{ marginTop: 6 }}><Sparkline data={spark} color={color || '#38bdf8'} /></div>}
      {sub && <div className="muted" style={{ fontSize: 10, marginTop: 4 }}>{sub}</div>}
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
  const [nowTick, setNowTick] = useState(0);   // forces a 60s "as of now" recompute

  // Live store (real-time, per WS tick)
  const positions = useSimStore((s) => s.positions);
  const storeGates = useSimStore((s) => s.gates);
  const sensorReadings = useSimStore((s) => s.sensorReadings);
  const simTimeIso = useSimStore((s) => s.simTime);
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
    const sensors = Object.values(sensorReadings);
    const avgPax = sensors.length ? sensors.reduce((s, r) => s + (r.pax_density || 0), 0) / sensors.length : 0;
    const avgTemp = sensors.length ? sensors.reduce((s, r) => s + (r.temperature || 0), 0) / sensors.length : 0;
    return { taxiing, holding, runway, arriving, parked, queueDepth, runwayOccupant,
             onGround: posList.length, gatesOcc, gatesTotal: gatesArr.length || 61, avgPax, avgTemp };
  }, [posList, storeGates, sensorReadings]);

  // Rolling sparkline buffers for live tiles (client-side; reset on reload).
  const buffers = useRef({ onGround: [], queueDepth: [], gatesOcc: [], pax: [] });
  useEffect(() => {
    const b = buffers.current;
    const push = (k, v) => { b[k].push(v); if (b[k].length > MAX_SPARK) b[k].shift(); };
    push('onGround', live.onGround);
    push('queueDepth', live.queueDepth);
    push('gatesOcc', live.gatesOcc);
    push('pax', +live.avgPax.toFixed(2));
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

  // ── LIVE demand curve: arrivals/departures per 30-min bucket, now−3h→now+3h ──
  const demand = useMemo(() => {
    void nowTick;
    const BUCKET = 30 * 60000, N = 12, START = now - 3 * 3600000;
    const rows = Array.from({ length: N }, (_, i) => {
      const t = new Date(START + i * BUCKET);
      return { label: t.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Qatar' }), a: 0, b: 0 };
    });
    for (const f of flights) {
      const isArr = (f.destination === 'DOH') || (f.direction === 'arrival');
      const iso = isArr ? f.scheduled_arr : f.scheduled_dep;
      if (!iso) continue;
      const idx = Math.floor((new Date(iso).getTime() - START) / BUCKET);
      if (idx >= 0 && idx < N) rows[idx][isArr ? 'a' : 'b']++;
    }
    return { rows, nowFrac: 0.5, capacity: Math.round(RUNWAY_CAPACITY_PER_HR / 2) }; // per 30-min bucket
  }, [flights, now, nowTick]);

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
      onTimeTrend: analytics.ontime_trend || [],
      delayRows: [{ key: 'ARR', label: 'ARR' }, { key: 'DEP', label: 'DEP' }],
      delayCols: cols, delayVals, delayMax,
      ghRows: concourses.map((c) => ({ key: c, label: c })),
      ghCols: ghCols.map((c) => ({ key: c, label: c })), ghVals, ghMax,
      topRoutes: analytics.top_routes || [],
      airlines: analytics.airline_share || [],
    };
  }, [analytics, now]);

  const clock = simTimeIso
    ? new Date(simTimeIso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Asia/Qatar' })
    : '—';
  const hasData = flights.length > 0 || gates.length > 0;

  const DELAY_COLORS = ['#22c55e', '#4ade80', '#fbbf24', '#f97316', '#ef4444'];

  return (
    <div style={{ height: '100%', width: '100%', overflow: 'auto', background: 'var(--bg-base)' }}>
      <div className="panel" style={{
        height: 56, display: 'flex', alignItems: 'center', padding: '0 16px', borderRadius: 0,
        borderLeft: 'none', borderRight: 'none', borderTop: 'none', position: 'sticky', top: 0, zIndex: 1,
      }}>
        <LayoutDashboard size={20} className="accent" />
        <div style={{ marginLeft: 10, flex: 1 }}>
          <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1 }}>Operations Dashboard</div>
          <div className="muted" style={{ fontSize: 10 }}>{inbound.length} inbound · {outbound.length} outbound · live</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'monospace' }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#22c55e',
            boxShadow: '0 0 6px #22c55e', animation: 'pulse 1.6s infinite' }} />
          <span style={{ fontSize: 13, color: '#93a3b8' }}>{clock} <span style={{ fontSize: 9 }}>AST</span></span>
        </div>
      </div>

      <div style={{ padding: 16, maxWidth: 1280, margin: '0 auto' }}>
        {!hasData && <div className="muted" style={{ padding: 24, textAlign: 'center' }}>Loading operations data…</div>}

        {/* ── ROW 1: LIVE PULSE ── */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
          <LiveTile label="On ground" value={live.onGround} color="#34d399" spark={buffers.current.onGround} />
          <LiveTile label="Dep queue" value={live.queueDepth} color="#fbbf24" sub={`${live.taxiing} taxi · ${live.holding} hold`} spark={buffers.current.queueDepth} />
          <LiveTile label="Dep next 1h" value={asOfNow.depNext} color="#f97316" />
          <LiveTile label="Arr next 1h" value={asOfNow.arrNext} color="#38bdf8" />
          <LiveTile label="Avg delay now" value={asOfNow.avgDelay} unit="min" color={asOfNow.avgDelay > 15 ? '#ef4444' : '#22c55e'} />
          <LiveTile label="On-time (window)" value={`${asOfNow.onTimePct}%`} color={asOfNow.onTimePct >= 80 ? '#22c55e' : '#fbbf24'} />
          <LiveTile label="Overdue" value={asOfNow.overdue} color={asOfNow.overdue > 0 ? '#ef4444' : 'var(--text)'} />
          <LiveTile label="Pax density" value={live.avgPax.toFixed(1)} unit="/m²" color="#a78bfa" spark={buffers.current.pax} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
          {/* ── HERO: live demand curve ── */}
          <Section title="Live demand — arrivals vs departures (now ±3h)" wide>
            <DemandCurve data={demand.rows} nowFrac={demand.nowFrac} capacity={demand.capacity} />
          </Section>

          {/* ── Runway pressure + live runway status ── */}
          <Section title="Runway 34R pressure (this hour)"
            right={<span style={{ fontSize: 10, color: live.runway ? '#ef4444' : '#22c55e', fontWeight: 700 }}>
              {live.runway ? `● ${live.runwayOccupant || 'ROLLING'}` : '○ IDLE'}</span>}>
            <Gauge value={asOfNow.depThisHour} max={RUNWAY_CAPACITY_PER_HR} unit={`/ ${RUNWAY_CAPACITY_PER_HR} cap`} label={`${asOfNow.launched60} launched last 60m`} />
          </Section>

          {/* ── Delay distribution ── */}
          <Section title="Delay distribution (24h)">
            {hist ? <Histogram data={hist.delayDist} colors={DELAY_COLORS} /> : <Loading />}
          </Section>

          {/* ── Delay by hour heatmap ── */}
          <Section title="Avg delay by hour — ARR / DEP (min)" wide>
            {hist ? <Heatmap rows={hist.delayRows} cols={hist.delayCols} values={hist.delayVals}
                             max={hist.delayMax} hue="delay" highlightCol={hist.curHourLabel} /> : <Loading />}
          </Section>

          {/* ── Rolling on-time trend ── */}
          <Section title="On-time % trend (12h)">
            {hist ? <LineTrend data={hist.onTimeTrend} unit="%" domain={[0, 100]} color="#22c55e" /> : <Loading />}
          </Section>

          {/* ── Movements per hour (historical) ── */}
          <Section title="Movements per hour — last 12h">
            {/* reuse DemandCurve-style stacked look via existing Histogram of totals is not ideal; keep BarChart */}
            <MovementsBars data={movements} />
          </Section>

          {/* ── On-time performance donut ── */}
          <Section title="Status mix (live window)">
            <Donut centerLabel={`${onTime.pct}%`} segments={[
              { label: 'On-time', value: onTime.ontime, color: '#22c55e' },
              { label: 'Delayed', value: onTime.delayed, color: '#f97316' },
              { label: 'Cancelled', value: onTime.cancelled, color: '#ef4444' },
            ]} />
          </Section>

          {/* ── Fleet mix donut ── */}
          <Section title="Fleet mix (live window)">
            <Donut centerLabel={fleet.wide + fleet.narrow} segments={[
              { label: 'Widebody', value: fleet.wide, color: '#38bdf8' },
              { label: 'Narrowbody', value: fleet.narrow, color: '#a78bfa' },
            ]} />
          </Section>

          {/* ── Gate × hour heatmap ── */}
          <Section title="Gate occupancy by concourse × hour" wide>
            {hist ? <Heatmap rows={hist.ghRows} cols={hist.ghCols} values={hist.ghVals}
                             max={hist.ghMax} hue="load" highlightCol={hist.curHourLabel} /> : <Loading />}
          </Section>

          {/* ── Top routes with delay tint ── */}
          <Section title="Top destinations (by delay)">
            {hist ? <HBarList items={hist.topRoutes.map((r) => ({
              label: r.label, value: r.value,
              color: r.delay > 30 ? '#ef4444' : r.delay > 15 ? '#f97316' : '#4ade80',
              sub: `${Math.round(r.delay)}m`,
            }))} /> : <Loading />}
          </Section>

          {/* ── Airline share ── */}
          <Section title="Airline share (24h)">
            {hist ? <HBarList items={hist.airlines} color="#a78bfa" /> : <Loading />}
          </Section>
        </div>
      </div>
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
    <svg width="100%" height={H} preserveAspectRatio="none" style={{ display: 'block' }}>
      {data.map((d, i) => {
        const hTot = ((d.a + d.b) / max) * (H - 18);
        const hA = (d.a / max) * (H - 18);
        const x = `${i * barW + barW * 0.15}%`, w = `${barW * 0.7}%`, yTop = H - 18 - hTot;
        return (
          <g key={i}>
            {d.b > 0 && <rect x={x} y={yTop} width={w} height={hTot - hA} fill="#fbbf24" rx="1" />}
            {d.a > 0 && <rect x={x} y={H - 18 - hA} width={w} height={hA} fill="#38bdf8" rx="1" />}
            <text x={`${i * barW + barW * 0.5}%`} y={H - 5} textAnchor="middle" fill="var(--text-muted)" fontSize="8" fontFamily="monospace">{d.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

import { useCallback, useEffect, useMemo, useState } from 'react';
import { LayoutDashboard } from 'lucide-react';
import { useSimStore } from '../store/useSimStore';
import { utilization24h, isWidebody } from '../lib/gateStats.js';
import StatTile from '../components/charts/StatTile';
import Donut from '../components/charts/Donut';
import BarChart from '../components/charts/BarChart';
import HBarList from '../components/charts/HBarList';

const POLL_MS = 15000;

// AeroDataBox api_status → on-time / delayed / cancelled bucket.
const DELAYED = new Set(['Delayed']);
const CANCELLED = new Set(['Canceled', 'Cancelled', 'Diverted']);
function statusBucket(s) {
  if (CANCELLED.has(s)) return 'cancelled';
  if (DELAYED.has(s)) return 'delayed';
  return 'ontime'; // Expected/Boarding/GateClosed/Departed/Arrived/CheckIn/Scheduled/Unknown
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function Section({ title, children, wide }) {
  return (
    <div className="card" style={{ padding: 14, gridColumn: wide ? '1 / -1' : undefined }}>
      <div className="muted" style={{ fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', marginBottom: 12 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

export default function DashboardPage() {
  const [schedule, setSchedule] = useState({ inbound: [], outbound: [] });
  const [gates, setGates] = useState([]);
  const [movementRows, setMovementRows] = useState([]);

  const simTimeIso = useSimStore((s) => s.simTime);
  const now = simTimeIso ? new Date(simTimeIso).getTime() : Date.now();

  const refresh = useCallback(() => {
    fetchJSON('/api/schedule').then(setSchedule).catch(() => {});
    fetchJSON('/api/gates').then((d) => setGates(d.gates || [])).catch(() => {});
    fetchJSON('/api/movements').then((d) => setMovementRows(d.movements || [])).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const poll = setInterval(refresh, POLL_MS);
    return () => clearInterval(poll);
  }, [refresh]);

  const inbound = schedule.inbound || [];
  const outbound = schedule.outbound || [];
  const flights = useMemo(() => [...inbound, ...outbound], [inbound, outbound]);

  // ── On-time performance ──────────────────────────────────────────────────
  const onTime = useMemo(() => {
    const c = { ontime: 0, delayed: 0, cancelled: 0 };
    for (const f of flights) c[statusBucket(f.api_status)]++;
    const total = c.ontime + c.delayed + c.cancelled;
    const pct = total ? Math.round((c.ontime / total) * 100) : 0;
    return { ...c, total, pct };
  }, [flights]);

  // ── Movements per hour — last 12h from the Lakebase flight_schedule dataset
  // (via /api/movements), so it reflects real history, not just the live board
  // window. `a` = arrivals, `b` = departures (BarChart's expected shape).
  const movements = useMemo(
    () => movementRows.map((r) => ({ label: r.label, a: r.arrivals, b: r.departures })),
    [movementRows],
  );

  // ── Gate & concourse utilization ─────────────────────────────────────────
  const gateStats = useMemo(() => {
    const withPct = gates.map((g) => ({ gate: g, pct: utilization24h(g, now) }));
    const top = withPct.filter((x) => x.pct > 0).sort((a, b) => b.pct - a.pct).slice(0, 6)
      .map((x) => ({ label: x.gate.id, value: +x.pct.toFixed(1) }));
    // occupied per concourse letter
    const byConcourse = {};
    for (const g of gates) {
      const c = g.id[0];
      byConcourse[c] = byConcourse[c] || { occ: 0, total: 0 };
      byConcourse[c].total++;
      if (g.status === 'OCCUPIED') byConcourse[c].occ++;
    }
    const concourse = Object.keys(byConcourse).sort().map((c) => ({
      label: c, value: byConcourse[c].occ, sub: `of ${byConcourse[c].total}`,
    }));
    // avg turnaround from history durations
    const durs = gates.flatMap((g) => (g.history || []).map((h) => h.duration_min).filter((d) => d != null));
    const avgTurn = durs.length ? Math.round(durs.reduce((s, d) => s + d, 0) / durs.length) : 0;
    const occupiedNow = gates.filter((g) => g.status === 'OCCUPIED').length;
    return { top, concourse, avgTurn, occupiedNow, totalGates: gates.length };
  }, [gates, now]);

  // ── Fleet & route mix ────────────────────────────────────────────────────
  const fleet = useMemo(() => {
    let wide = 0, narrow = 0;
    const routes = {}, airlines = {};
    for (const f of flights) {
      if (isWidebody(f.aircraft_type)) wide++; else narrow++;
      const ap = f.origin === 'DOH' ? f.destination : f.origin; // the non-DOH endpoint
      if (ap && ap !== '----') routes[ap] = (routes[ap] || 0) + 1;
      const al = f.airline || 'QR';
      airlines[al] = (airlines[al] || 0) + 1;
    }
    const topRoutes = Object.entries(routes).sort((a, b) => b[1] - a[1]).slice(0, 6)
      .map(([label, value]) => ({ label, value }));
    const topAirlines = Object.entries(airlines).sort((a, b) => b[1] - a[1]).slice(0, 6)
      .map(([label, value]) => ({ label, value }));
    return { wide, narrow, topRoutes, topAirlines };
  }, [flights]);

  const hasData = flights.length > 0 || gates.length > 0;

  return (
    <div style={{ height: '100%', width: '100%', overflow: 'auto', background: 'var(--bg-base)' }}>
      <div
        className="panel"
        style={{
          height: 56, display: 'flex', alignItems: 'center', padding: '0 16px',
          borderRadius: 0, borderLeft: 'none', borderRight: 'none', borderTop: 'none',
          position: 'sticky', top: 0, zIndex: 1,
        }}
      >
        <LayoutDashboard size={20} className="accent" />
        <div style={{ marginLeft: 10 }}>
          <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1 }}>Operations Dashboard</div>
          <div className="muted" style={{ fontSize: 10 }}>
            {inbound.length} inbound · {outbound.length} outbound · live 6h window
          </div>
        </div>
      </div>

      <div style={{ padding: 16, maxWidth: 1200, margin: '0 auto' }}>
        {!hasData && (
          <div className="muted" style={{ padding: 24, textAlign: 'center' }}>Loading operations data…</div>
        )}

        {/* Headline stat tiles */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16 }}>
          <StatTile label="On-time" value={`${onTime.pct}%`}
                    color={onTime.pct >= 80 ? '#22c55e' : onTime.pct >= 60 ? '#fbbf24' : '#ef4444'}
                    sub={`${onTime.total} flights tracked`} />
          <StatTile label="Delayed" value={onTime.delayed} color="#f97316" />
          <StatTile label="Cancelled" value={onTime.cancelled} color="#ef4444" />
          <StatTile label="Gates occupied" value={`${gateStats.occupiedNow}/${gateStats.totalGates}`} color="#38bdf8" />
          <StatTile label="Avg turnaround" value={gateStats.avgTurn} unit="min" color="var(--text)" sub="last 24h" />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
          <Section title="Movements per hour — last 12h (UTC)" wide>
            <BarChart data={movements} />
          </Section>

          <Section title="On-time performance">
            <Donut
              centerLabel={`${onTime.pct}%`}
              segments={[
                { label: 'On-time', value: onTime.ontime, color: '#22c55e' },
                { label: 'Delayed', value: onTime.delayed, color: '#f97316' },
                { label: 'Cancelled', value: onTime.cancelled, color: '#ef4444' },
              ]}
            />
          </Section>

          <Section title="Fleet mix">
            <Donut
              centerLabel={fleet.wide + fleet.narrow}
              segments={[
                { label: 'Widebody', value: fleet.wide, color: '#38bdf8' },
                { label: 'Narrowbody', value: fleet.narrow, color: '#a78bfa' },
              ]}
            />
          </Section>

          <Section title="Top utilized gates · 24h">
            <HBarList items={gateStats.top} color="#38bdf8" valueSuffix="%" />
          </Section>

          <Section title="Occupied by concourse">
            <HBarList items={gateStats.concourse} color="#fbbf24" />
          </Section>

          <Section title="Top routes">
            <HBarList items={fleet.topRoutes} color="#4ade80" />
          </Section>

          <Section title="Airlines">
            <HBarList items={fleet.topAirlines} color="#a78bfa" />
          </Section>
        </div>
      </div>
    </div>
  );
}

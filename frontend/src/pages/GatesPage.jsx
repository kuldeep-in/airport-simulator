import { useCallback, useEffect, useMemo, useState } from 'react';
import { DoorOpen } from 'lucide-react';
import { useSimStore } from '../store/useSimStore';
import { GATE_LONLAT } from '../data/gateData.js';
import { utilization24h } from '../lib/gateStats.js';
import { PAGE_BG } from '../components/boardTheme.jsx';

const POLL_MS = 15000;

// Same natural concourse+number sort used elsewhere (Gate Editor, Live
// Gates board), so gate order is consistent everywhere in the app.
const ALL_GATE_IDS = Object.keys(GATE_LONLAT).sort((a, b) => {
  const [, ac, an] = a.match(/([A-Z])(\d+)/);
  const [, bc, bn] = b.match(/([A-Z])(\d+)/);
  return ac < bc ? -1 : ac > bc ? 1 : parseInt(an) - parseInt(bn);
});

const STATUS_COLOR = { AVAILABLE: '#22c55e', OCCUPIED: '#fbbf24' };
// Unknown-status fallback. Was #6070a0 at only 3.17:1 on --bg-card, below the AA
// 4.5:1 floor for the 10px status text it colours.
const STATUS_FALLBACK = '#8b9bb4';
const CAPACITY_LABEL = { NARROW: 'Narrow-body', WIDE: 'Wide-body', A380: 'A380/Jumbo' };

async function fetchGates() {
  const res = await fetch('/api/gates');
  if (!res.ok) throw new Error(`gates fetch failed: ${res.status}`);
  return res.json();
}

function fmtClock(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// Whole-minute duration, e.g. "12 min" or "1h 5m" — matches the rest of the
// app's "no seconds in a duration badge" convention (ConnectionsPage).
function fmtDuration(ms) {
  const totalMin = Math.max(0, Math.round(ms / 60000));
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h === 0) return `${m} min`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

function GateRow({ gate, now, expanded, onToggle }) {
  // `current` and `history` can be absent on a gate (e.g. an available gate, or a
  // gate synthesised from GATE_POSITIONS with only {id,status}). Treat "occupied"
  // as requiring an actual `current` record, and default history to [].
  const history = gate.history ?? [];
  const isOccupied = gate.status === 'OCCUPIED' && gate.current != null;
  const color = STATUS_COLOR[gate.status] ?? STATUS_FALLBACK;
  const sinceMs = gate.current?.occupied_from ? new Date(gate.current.occupied_from).getTime() : null;

  return (
    // Same keyboard-operable disclosure treatment as the Schedule rows: this was
    // a bare <div onClick>, unreachable by keyboard and unannounced to screen
    // readers despite being the page's primary interaction.
    <div
      className="card row-interactive"
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      aria-label={`Gate ${gate.id}, ${gate.status.toLowerCase()}${isOccupied ? `, occupied by ${gate.current.flight_id}` : ''}`}
      style={{
        padding: '10px 14px',
        borderLeft: expanded ? '3px solid var(--accent-blue)' : '3px solid transparent',
        background: expanded ? 'rgba(56,189,248,0.08)' : undefined,
      }}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); }
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', rowGap: 6 }}>
        <div className="mono" style={{ width: 46, fontWeight: 600 }}>{gate.id}</div>
        <div className="muted" style={{ width: 100, fontSize: 11 }}>
          {CAPACITY_LABEL[gate.capacity_class] ?? gate.capacity_class}
        </div>
        <div style={{ width: 100 }}>
          <span style={{ fontSize: 10, color, fontWeight: 600, letterSpacing: 0.5 }}>
            {gate.status}
          </span>
        </div>
        <div style={{ flex: 1, fontSize: 12 }}>
          {isOccupied ? (
            <>
              <span style={{ fontWeight: 700 }}>{gate.current.flight_id}</span>
              <span className="muted" style={{ marginLeft: 8, fontSize: 11 }}>
                since {fmtClock(gate.current.occupied_from)}
                {sinceMs != null && ` (${fmtDuration(now - sinceMs)})`}
              </span>
            </>
          ) : (
            <span className="muted">—</span>
          )}
        </div>
        <div className="muted" style={{ fontSize: 11 }}>
          {history.length} past stay{history.length === 1 ? '' : 's'}
        </div>
      </div>

      {expanded && (
        <div style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          {history.length === 0 && (
            <div className="muted" style={{ fontSize: 11 }}>
              No past occupancy recorded yet this session.
            </div>
          )}
          {history.map((h, i) => (
            <div
              key={`${h.flight_id}-${h.occupied_from}-${i}`}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0', fontSize: 11, flexWrap: 'wrap' }}
            >
              <span style={{ fontWeight: 700, width: 70 }}>{h.flight_id}</span>
              <span
                style={{
                  width: 34, fontSize: 9, fontWeight: 700, letterSpacing: 0.5,
                  color: h.direction === 'arrival' ? '#38bdf8' : '#fbbf24',
                }}
                title={h.direction === 'arrival' ? 'Arrival' : 'Departure'}
              >
                {h.direction === 'arrival' ? 'ARR' : 'DEP'}
              </span>
              <span className="muted" style={{ width: 90 }}>{fmtClock(h.occupied_from)}</span>
              <span className="muted">→</span>
              <span className="muted" style={{ width: 90 }}>{fmtClock(h.occupied_until)}</span>
              <span style={{ color: 'var(--text-muted)' }}>
                {fmtDuration(new Date(h.occupied_until).getTime() - new Date(h.occupied_from).getTime())}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function KpiCard({ rank, gate, pct }) {
  const barColor = pct >= 66 ? '#ef4444' : pct >= 33 ? '#fbbf24' : '#22c55e';
  return (
    <div className="card" style={{ padding: '10px 12px', flex: 1, minWidth: 130 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span className="muted" style={{ fontSize: 10 }}>#{rank}</span>
        <span style={{ fontWeight: 700, fontSize: 15 }}>{gate.id}</span>
        <span style={{ marginLeft: 'auto', fontWeight: 700, fontSize: 15, color: barColor }}>
          {pct.toFixed(1)}%
        </span>
      </div>
      <div style={{ marginTop: 7, height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: barColor, transition: 'width 0.3s ease' }} />
      </div>
      <div className="muted" style={{ fontSize: 9, marginTop: 5, letterSpacing: 0.3 }}>
        {CAPACITY_LABEL[gate.capacity_class] ?? gate.capacity_class}
      </div>
    </div>
  );
}

export default function GatesPage() {
  const [gatesById, setGatesById] = useState({});
  const [expandedId, setExpandedId] = useState(null);

  const simTimeIso = useSimStore((s) => s.simTime);
  const now = simTimeIso ? new Date(simTimeIso).getTime() : Date.now();

  const refresh = useCallback(() => {
    fetchGates()
      .then((data) => {
        const byId = {};
        for (const g of data.gates || []) byId[g.id] = g;
        setGatesById(byId);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const poll = setInterval(refresh, POLL_MS);
    return () => clearInterval(poll);
  }, [refresh]);

  const gateRows = useMemo(
    () => ALL_GATE_IDS.map((id) => gatesById[id]).filter(Boolean),
    [gatesById],
  );
  const occupiedCount = gateRows.filter((g) => g.status === 'OCCUPIED').length;

  // Top 5 most-utilized gates over the last 24h. Uses the SERVER-computed
  // `utilization_pct` (all stays in the window), which fixes the old ~19% ceiling
  // from the 5-stay display cap; falls back to the client calc for back-compat if
  // the field is absent (older API).
  const topUtilized = useMemo(() => {
    return gateRows
      .map((g) => ({ gate: g, pct: g.utilization_pct ?? utilization24h(g, now) }))
      .filter((x) => x.pct > 0)
      .sort((a, b) => b.pct - a.pct)
      .slice(0, 5);
  }, [gateRows, now]);

  // Split gate list into two balanced columns (fills left column first).
  const [colA, colB] = useMemo(() => {
    const half = Math.ceil(gateRows.length / 2);
    return [gateRows.slice(0, half), gateRows.slice(half)];
  }, [gateRows]);

  const renderCol = (rows) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, flex: 1, minWidth: 320 }}>
      {rows.map((g) => (
        <GateRow
          key={g.id}
          gate={g}
          now={now}
          expanded={expandedId === g.id}
          onToggle={() => setExpandedId(expandedId === g.id ? null : g.id)}
        />
      ))}
    </div>
  );

  return (
    <div style={{ height: '100%', width: '100%', overflow: 'auto', background: PAGE_BG }}>
      <div
        className="panel"
        style={{
          height: 56, display: 'flex', alignItems: 'center', padding: '0 16px',
          borderRadius: 0, borderLeft: 'none', borderRight: 'none', borderTop: 'none',
          position: 'sticky', top: 0, zIndex: 1,
        }}
      >
        <DoorOpen size={20} className="accent" />
        <div style={{ marginLeft: 10 }}>
          <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1 }}>Gates</div>
          <div className="muted" style={{ fontSize: 10 }}>
            {occupiedCount} of {gateRows.length} occupied · click a gate for its history
          </div>
        </div>
      </div>

      <div style={{ padding: 16, maxWidth: 1200, margin: '0 auto' }}>
        {gateRows.length === 0 && (
          <div className="muted" style={{ padding: 24, textAlign: 'center' }}>
            Loading gates…
          </div>
        )}

        {/* KPIs — top 5 most-utilized gates over the last 24h */}
        {topUtilized.length > 0 && (
          <div style={{ marginBottom: 18 }}>
            <div className="muted" style={{ fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', marginBottom: 8 }}>
              Top 5 utilized gates · last 24h
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              {topUtilized.map((x, i) => (
                <KpiCard key={x.gate.id} rank={i + 1} gate={x.gate} pct={x.pct} />
              ))}
            </div>
          </div>
        )}

        {/* Two-column gate list */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          {renderCol(colA)}
          {renderCol(colB)}
        </div>
      </div>
    </div>
  );
}

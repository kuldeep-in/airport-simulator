import { useWidth } from './useWidth';
import { PHASE_LABEL, PHASE_COLOR } from '../../lib/phases.js';

// Departure-queue waterfall: one row per departure (current + upcoming, capped by
// the backend), stage bars on a shared time axis with a NOW marker.
//
// Stages come from the generator's per-flight `timeline` (/api/departure-queue):
// boundaries before NOW are ACTUAL, after NOW are PROJECTED from the real route
// and runway-lock queue. Flights not yet at their stand have no timeline — they're
// laid out from the median stage durations of the live ones (fallback constants
// below). Bars AND the status column use the Home map flight board's phases —
// same labels, same colours (lib/phases.js) — so nothing here is a new status.
// A queued aircraft is TAXIING OUT on the map, so the queue sits in that bar.
const STAGES = [
  { key: 'gate',     end: 'pushback', phase: 'GATE_PARKED' },
  { key: 'pushback', end: 'hold',     phase: 'TAXIING_OUT' },
  { key: 'hold',     end: 'runway',   phase: 'HOLDING_SHORT' },
  { key: 'runway',   end: 'airborne', phase: 'RUNWAY' },
].map((s) => ({ ...s, label: PHASE_LABEL[s.phase], color: PHASE_COLOR[s.phase] }));
const KEYS = ['gate', 'pushback', 'hold', 'runway', 'airborne'];
// Seconds per stage when no live flight exists to take a median from. At gate
// is the generator's 30–45 min stand-appearance lead.
const FALLBACK_SEC = { gate: 2250, pushback: 420, hold: 30, runway: 90 };
const MIN = 60000;
const LABEL_W = 132, RWY_W = 52, DEP_W = 96, STATUS_W = 100;

function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

// Resolve every row to absolute stage times (ms) + which are estimated.
function layout(rows) {
  const parsed = rows.map((r) => {
    const tl = r.timeline;
    if (!tl) return { r, t: null };
    const t = {};
    for (const k of KEYS) t[k] = new Date(tl.t[k]).getTime();
    // Gate time unknown (parked before the generator started) → estimated below.
    if (!tl.actual.includes('gate')) t.gate = null;
    return { r, t };
  });
  const typical = {};
  for (const s of STAGES) {
    const d = parsed.filter((p) => p.t && p.t[s.key] != null).map((p) => (p.t[s.end] - p.t[s.key]) / 1000);
    typical[s.key] = median(d) ?? FALLBACK_SEC[s.key];
  }
  return parsed.map(({ r, t }) => {
    if (t) {
      if (t.gate == null) t.gate = t.pushback - typical.gate * 1000;
      return { r, t, estimated: false };
    }
    const push = new Date(r.scheduled_dep).getTime();
    const est = { gate: push - typical.gate * 1000, pushback: push };
    for (const s of STAGES.slice(1)) est[s.end] = est[s.key] + typical[s.key] * 1000;
    return { r, t: est, estimated: true };
  });
}

export default function DepartureWaterfall({ rows = [], now, tz }) {
  const [ref, W] = useWidth(900);
  if (rows.length === 0) {
    return <div ref={ref} className="muted" style={{ fontSize: 11 }}>No departures in the queue</div>;
  }
  const fmt = (ms) => new Date(ms).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: tz });
  const laid = layout(rows);

  // Axis: a few minutes of context before NOW (or the earliest pushback, but no
  // more than 30 min back), through the last projected takeoff.
  const minPush = Math.min(...laid.map((l) => l.t.pushback));
  const start = Math.max(now - 30 * MIN, Math.min(now - 5 * MIN, minPush - 2 * MIN));
  const end = Math.max(now + 30 * MIN, Math.max(...laid.map((l) => l.t.airborne)) + 3 * MIN);
  const span = end - start;
  const trackW = Math.max(1, W - LABEL_W - RWY_W - DEP_W - STATUS_W);
  const px = (ms) => ((Math.min(end, Math.max(start, ms)) - start) / span) * trackW;
  const nowX = px(now);
  const tickStep = span > 150 * MIN ? 30 * MIN : 15 * MIN;
  const ticks = [];
  for (let t = Math.ceil(start / tickStep) * tickStep; t <= end; t += tickStep) ticks.push(t);

  return (
    <div ref={ref}>
      {/* Time axis */}
      <div style={{ display: 'flex', fontSize: 9, color: 'var(--text-muted)' }}>
        <div style={{ width: LABEL_W }}>FLIGHT</div>
        <div style={{ width: trackW, position: 'relative', height: 26 }}>
          {ticks.map((t) => (
            <span key={t} className="mono" style={{ position: 'absolute', left: px(t), bottom: 2, transform: 'translateX(-50%)' }}>{fmt(t)}</span>
          ))}
          <span style={{ position: 'absolute', left: nowX, top: 0, transform: 'translateX(-50%)', color: '#e2e8f0',
                         fontWeight: 700, letterSpacing: 1, background: 'var(--bg-card)', padding: '0 3px' }}>
            NOW {fmt(now)}
          </span>
        </div>
        <div style={{ width: RWY_W, textAlign: 'center' }}>RWY</div>
        <div style={{ width: DEP_W }}>DEPARTURE</div>
        <div style={{ width: STATUS_W, textAlign: 'center' }}>STATUS</div>
      </div>

      <div style={{ position: 'relative' }}>
        {/* NOW line across every row */}
        <div aria-hidden="true" style={{ position: 'absolute', left: LABEL_W + nowX, top: 0, bottom: 0,
                                         borderLeft: '1px dashed #e2e8f0', opacity: 0.7, zIndex: 1 }} />
        {laid.map((l) => (
          <Row key={l.r.id} l={l} px={px} trackW={trackW} fmt={fmt} />
        ))}
      </div>

      <div style={{ display: 'flex', gap: 14, marginTop: 'var(--space-md)', fontSize: 11, flexWrap: 'wrap' }}>
        {STAGES.map((s) => (
          <span key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: s.color }} />
            <span className="muted">{s.label}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Row({ l, px, trackW, fmt }) {
  const { r, t, estimated } = l;
  const status = PHASE_LABEL[r.phase] || 'SCHEDULED';
  const statusColor = PHASE_COLOR[r.phase] || 'var(--text-muted)';
  const leadEnd = px(estimated ? t.pushback : t.gate);
  return (
    <div style={{ display: 'flex', alignItems: 'center', height: 30, borderTop: '1px solid var(--border-subtle)' }}
         aria-label={`${r.callsign} to ${r.destination}: ${status}, takeoff ${estimated ? 'about ' : ''}${fmt(t.airborne)}`}>
      <div style={{ width: LABEL_W, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>{r.callsign}</span>
        <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 6 }}>
          → {r.destination || '—'}{r.assigned_gate ? ` · ${r.assigned_gate}` : ''}
        </span>
      </div>
      <div style={{ width: trackW, position: 'relative', height: 20 }}>
        {/* Lead-in from the axis start until it appears at its stand. */}
        {leadEnd > 0 && (
          <div style={{ position: 'absolute', left: 0, width: leadEnd, top: 9.5, height: 1,
                        background: 'rgba(148,163,184,0.25)' }} />
        )}
        {STAGES.map((s) => {
          // AT GATE only once the sim has parked it at a stand (has a timeline) —
          // a gate from the airport feed alone doesn't mean it's there yet.
          if (s.key === 'gate' && estimated) return null;
          const a = px(t[s.key]), b = px(t[s.end]);
          if (b <= a && t[s.end] <= t[s.key]) return null;
          const w = Math.max(b - a, 3);
          const mins = Math.round((t[s.end] - t[s.key]) / MIN);
          // Taxi bar: just the duration ("6m"); others: phase label + duration.
          const isTaxi = s.phase === 'TAXIING_OUT';
          const text = isTaxi ? (mins > 0 ? `${mins}m` : '') : `${s.label}${mins > 0 ? ` ${mins}m` : ''}`;
          const showLabel = text && w > (isTaxi ? 18 : 54);
          return (
            <div key={s.key} title={`${s.label}: ${fmt(t[s.key])}–${fmt(t[s.end])}${estimated ? ' (est.)' : ''}`}
                 style={{ position: 'absolute', left: a, width: w, top: 1, height: 18, borderRadius: 2,
                          overflow: 'hidden', background: s.color }}>
              {showLabel && (
                <span style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                               fontSize: 9.5, fontWeight: 600, color: '#f8fafc', textShadow: '0 1px 2px rgba(0,0,0,0.7)', whiteSpace: 'nowrap' }}>
                  {text}
                </span>
              )}
            </div>
          );
        })}
      </div>
      <div className="mono" style={{ width: RWY_W, textAlign: 'center', fontSize: 10, color: 'var(--text-muted)' }}>
        {r.timeline?.runway?.replace(/-B$/, '') || r.runway?.replace(/-B$/, '') || '34R'}
      </div>
      <div style={{ width: DEP_W, fontSize: 9, lineHeight: 1.3, color: 'var(--text-muted)' }}>
        <div>SCHED <span className="mono" style={{ color: 'var(--text-primary)' }}>{fmt(new Date(r.scheduled_dep).getTime())}</span></div>
        <div>{estimated ? 'EST T/O' : 'T/O'} <span className="mono" style={{ color: 'var(--text-primary)' }}>{fmt(t.airborne)}</span></div>
      </div>
      <div style={{ width: STATUS_W, textAlign: 'center', fontSize: 9, fontWeight: 600, letterSpacing: 0.3,
                    whiteSpace: 'nowrap', color: statusColor }}>
        {status}
      </div>
    </div>
  );
}

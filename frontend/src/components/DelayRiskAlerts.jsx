import { useEffect, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { fetchDelayRisk, riskPct } from '../lib/delayRisk.js';

// In-app alert banner (every page): departures the ML model scores ≥ 50 % likely to leave
// 15+ min late, departing within 90 minutes. Polls once a minute; dismissing hides the
// CURRENT set — a newly flagged flight brings the banner back.
const POLL_MS = 60000;

export default function DelayRiskAlerts() {
  const [alerts, setAlerts] = useState([]);
  const [dismissed, setDismissed] = useState(() => new Set());

  useEffect(() => {
    const load = () => fetchDelayRisk(2).then((d) => setAlerts(d.alerts || [])).catch(() => {});
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, []);

  const visible = alerts.filter((a) => !dismissed.has(a.flight_number));
  if (!visible.length) return null;
  return (
    <div role="status" aria-live="polite" style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '7px 16px', flexShrink: 0,
      background: 'rgba(248,113,113,0.12)', borderBottom: '1px solid rgba(248,113,113,0.45)', fontSize: 12.5,
    }}>
      <AlertTriangle size={16} color="#f87171" aria-hidden="true" />
      <span style={{ fontWeight: 700, color: '#fecaca' }}>
        {visible.length} departure{visible.length > 1 ? 's' : ''} at high delay risk within 90 min
      </span>
      <span style={{ display: 'flex', gap: 10, overflow: 'hidden', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>
        {visible.slice(0, 5).map((a) => (
          <a key={a.flight_number} href="/dashboard#delay-risk" title={a.reasons.join(' · ')}
             style={{ color: '#fff', textDecoration: 'none' }}>
            <b>{a.flight_number}</b> → {a.destination}{' '}
            <span className="mono" style={{ color: '#f87171', fontWeight: 700 }}>{riskPct(a.risk)}</span>
            <span style={{ color: '#94a3b8' }}> · {a.minutes_to_departure} min</span>
          </a>
        ))}
      </span>
      <button onClick={() => setDismissed(new Set(alerts.map((a) => a.flight_number)))}
              aria-label="Dismiss delay-risk alerts" title="Dismiss"
              style={{ background: 'transparent', border: 'none', color: '#94a3b8', cursor: 'pointer', padding: 2 }}>
        <X size={15} />
      </button>
    </div>
  );
}

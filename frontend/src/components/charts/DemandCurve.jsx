import { useWidth } from './useWidth';

// Live demand curve: arrivals vs departures per time bucket across a window that
// straddles the current time, with a vertical "NOW" marker at `nowFrac` (0..1)
// and an optional horizontal capacity reference line. `data`: [{label, a, b}]
// oldest→newest. Drawn in MEASURED PIXELS (no viewBox stretch) so labels stay crisp.
export default function DemandCurve({ data = [], nowFrac = 0.5, capacity, height = 190 }) {
  const [ref, W] = useWidth(700);
  if (data.length === 0) return <div ref={ref} className="muted" style={{ fontSize: 11 }}>No demand data</div>;
  const COL_A = '#38bdf8', COL_B = '#fbbf24';
  const max = Math.max(1, capacity || 0, ...data.map((d) => Math.max(d.a || 0, d.b || 0)));
  const H = height, padB = 20, padT = 14, padL = 4, padR = 4;
  const plotW = Math.max(1, W - padL - padR);
  const stepX = plotW / Math.max(1, data.length - 1);
  const x = (i) => padL + i * stepX;
  const y = (v) => H - padB - ((v || 0) / max) * (H - padB - padT);
  const path = (key) => data.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join(' ');
  const areaA = `${path('a')} L${x(data.length - 1).toFixed(1)},${H - padB} L${padL},${H - padB} Z`;
  const nowX = padL + nowFrac * plotW;
  const capY = capacity != null ? y(capacity) : null;
  const labelEvery = Math.ceil(data.length / 8);
  return (
    <div ref={ref}>
      <div style={{ display: 'flex', gap: 14, marginBottom: 6, fontSize: 11 }}>
        <Legend color={COL_A} label="Arrivals" />
        <Legend color={COL_B} label="Departures" />
        {capacity != null && <Legend color="#ef4444" label={`Capacity ${capacity}/30min`} dashed />}
      </div>
      <svg width={W} height={height} style={{ display: 'block' }}>
        <path d={areaA} fill={COL_A} opacity={0.10} />
        {capY != null && (
          <line x1={padL} y1={capY} x2={W - padR} y2={capY} stroke="#ef4444" strokeWidth="1" strokeDasharray="4 3" />
        )}
        <path d={path('a')} fill="none" stroke={COL_A} strokeWidth="2" strokeLinejoin="round" />
        <path d={path('b')} fill="none" stroke={COL_B} strokeWidth="2" strokeLinejoin="round" />
        {/* NOW marker */}
        <line x1={nowX} y1={padT} x2={nowX} y2={H - padB} stroke="#e2e8f0" strokeWidth="1" strokeDasharray="4 3" />
        <text x={nowX} y={padT - 3} textAnchor="middle" fill="#e2e8f0" fontSize="10" fontWeight="700"
              letterSpacing="1">NOW</text>
        {data.map((d, i) => (i % labelEvery === 0 || i === data.length - 1) && (
          <text key={i} x={x(i)} y={H - 6} textAnchor="middle" fill="var(--text-muted)" fontSize="9">{d.label}</text>
        ))}
      </svg>
    </div>
  );
}

function Legend({ color, label, dashed }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      <span style={{ width: 12, height: 0, borderTop: `2px ${dashed ? 'dashed' : 'solid'} ${color}` }} />
      <span className="muted">{label}</span>
    </span>
  );
}

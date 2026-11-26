import { useWidth } from './useWidth';

// Live time-axis trend for the last `windowMin` minutes, split into `bucketMin` bands.
//   series:  [{ key, color, width, dash, label }]  — fields read from each point
//   points:  [{ ts (ISO), [key]: number }]          — oldest first
//   buckets: [{ start, end, [bucketKey]: number }]  — one per band, oldest first
// The x axis is real time ([now − window, now]), so gaps in the data show as gaps and
// the chart scrolls as time passes. Each band is shaded alternately and labelled with
// its average, so "the last 15 minutes at 5-minute granularity" reads at a glance.
export default function TimeTrend({ series, points = [], buckets = [], bucketKey, nowIso, windowMin = 15,
  bucketMin = 5, unit = '', digits = 1, height = 150, tz }) {
  const [ref, W] = useWidth(500);
  const now = nowIso ? new Date(nowIso).getTime() : Date.now();
  const t0 = now - windowMin * 60000;
  const pts = points.map((p) => ({ ...p, t: new Date(p.ts).getTime() })).filter((p) => p.t >= t0);
  if (pts.length < 2) {
    return <div ref={ref} className="muted" style={{ fontSize: 11, padding: '24px 0' }}>Collecting readings (every 5 s)…</div>;
  }
  const vals = pts.flatMap((p) => series.map((s) => p[s.key])).filter((v) => v != null);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const padV = (hi - lo || Math.abs(hi) || 1) * 0.12;
  lo -= padV; hi += padV;
  const H = height, padT = 26, padB = 20, padL = 6, padR = 44;
  const plotW = Math.max(1, W - padL - padR), plotH = H - padT - padB;
  const x = (t) => padL + ((t - t0) / (now - t0)) * plotW;
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * plotH;
  const fmtT = (ms) => new Date(ms).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: tz });
  const nb = Math.round(windowMin / bucketMin);
  const last = pts[pts.length - 1];
  const main = series[0];

  return (
    <div ref={ref}>
      <svg width={W} height={H} style={{ display: 'block' }} role="img"
           aria-label={`${main.label} over the last ${windowMin} minutes, in ${bucketMin}-minute windows`}>
        {/* 5-minute bands, oldest → newest, each with its average */}
        {Array.from({ length: nb }, (_, i) => {
          const bs = t0 + i * bucketMin * 60000, be = bs + bucketMin * 60000;
          const b = buckets[i];
          const v = b ? b[bucketKey] : null;
          return (
            <g key={i}>
              <rect x={x(bs)} y={padT - 18} width={x(be) - x(bs)} height={plotH + 18}
                    fill={i % 2 ? 'rgba(255,255,255,0.035)' : 'rgba(255,255,255,0.012)'} />
              <text x={(x(bs) + x(be)) / 2} y={padT - 6} textAnchor="middle" fontSize="10" fill="var(--text-muted)">
                <tspan fontWeight="700" fill={main.color}>{v == null ? '—' : v.toFixed(digits)}{unit}</tspan>
                <tspan dx="4">{(nb - i) * bucketMin}–{(nb - i - 1) * bucketMin} min ago</tspan>
              </text>
              <line x1={x(bs)} x2={x(bs)} y1={padT - 18} y2={padT + plotH} stroke="rgba(255,255,255,0.08)" />
              <text x={x(bs)} y={H - 5} textAnchor={i === 0 ? 'start' : 'middle'} fontSize="9" fill="var(--text-muted)"
                    fontFamily="var(--font-mono)">{fmtT(bs)}</text>
            </g>
          );
        })}
        <text x={x(now)} y={H - 5} textAnchor="end" fontSize="9" fill="var(--text-muted)" fontFamily="var(--font-mono)">now</text>
        {/* series lines */}
        {series.map((s) => {
          const d = pts.filter((p) => p[s.key] != null)
            .map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p[s.key]).toFixed(1)}`).join(' ');
          return <path key={s.key} d={d} fill="none" stroke={s.color} strokeWidth={s.width || 1.5}
                       strokeDasharray={s.dash} strokeLinejoin="round" strokeLinecap="round" opacity={s.opacity ?? 1} />;
        })}
        {last[main.key] != null && (
          <g>
            <circle cx={x(last.t)} cy={y(last[main.key])} r="3.5" fill={main.color} />
            <text x={x(last.t) + 6} y={y(last[main.key]) + 4} fontSize="11" fontWeight="700" fill={main.color}>
              {last[main.key].toFixed(digits)}
            </text>
          </g>
        )}
      </svg>
      <div style={{ display: 'flex', gap: 14, fontSize: 10.5, marginTop: 4 }} className="muted">
        {series.map((s) => (
          <span key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <svg width="18" height="6"><line x1="0" y1="3" x2="18" y2="3" stroke={s.color} strokeWidth={s.width || 1.5} strokeDasharray={s.dash} /></svg>
            {s.label}
          </span>
        ))}
        <span style={{ marginLeft: 'auto' }}>{pts.length} readings · every 5 s</span>
      </div>
    </div>
  );
}

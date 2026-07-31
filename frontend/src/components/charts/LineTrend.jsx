import { useWidth } from './useWidth';

// Single-series line/area trend (e.g. rolling on-time %). `data`: [{label, value}].
// `domain`: optional [min,max] (else auto). Drawn in MEASURED PIXELS (no viewBox
// stretch) so axis labels stay crisp.
export default function LineTrend({ data = [], color = '#22c55e', height = 140, unit = '', domain }) {
  const [ref, W] = useWidth(400);
  if (data.length === 0) return <div ref={ref} className="muted" style={{ fontSize: 11 }}>No data</div>;
  const vals = data.map((d) => d.value || 0);
  const lo = domain ? domain[0] : Math.min(...vals);
  const hi = domain ? domain[1] : Math.max(...vals);
  const span = hi - lo || 1;
  const H = height, padB = 18, padT = 12, padL = 4, padR = 28;
  const plotW = Math.max(1, W - padL - padR);
  const stepX = plotW / Math.max(1, data.length - 1);
  const x = (i) => padL + i * stepX;
  const y = (v) => H - padB - (((v || 0) - lo) / span) * (H - padB - padT);
  const line = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(d.value).toFixed(1)}`).join(' ');
  const area = `${line} L${x(data.length - 1).toFixed(1)},${H - padB} L${padL},${H - padB} Z`;
  const lastX = x(data.length - 1), lastY = y(vals[vals.length - 1]);
  const labelEvery = Math.ceil(data.length / 6);
  return (
    <div ref={ref}>
      <svg width={W} height={height} style={{ display: 'block' }}>
        <path d={area} fill={color} opacity={0.12} />
        <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={lastX} cy={lastY} r={3} fill={color} />
        {data.map((d, i) => (i % labelEvery === 0 || i === data.length - 1) && (
          <text key={i} x={x(i)} y={H - 5} textAnchor="middle" fill="var(--text-muted)" fontSize="9">{d.label}</text>
        ))}
        <text x={lastX + 4} y={Math.max(12, lastY)} textAnchor="start" fill={color}
              fontSize="11" fontWeight="700">{vals[vals.length - 1]}{unit}</text>
      </svg>
    </div>
  );
}

// Inline-SVG donut with a legend. `segments`: [{label, value, color}].
export default function Donut({ segments = [], size = 120, thickness = 18, centerLabel }) {
  const total = segments.reduce((s, x) => s + (x.value || 0), 0);
  const r = (size - thickness) / 2;
  const cx = size / 2, cy = size / 2;
  const circ = 2 * Math.PI * r;

  let offset = 0;
  const arcs = total > 0 ? segments.filter((s) => s.value > 0).map((s) => {
    const frac = s.value / total;
    const seg = (
      <circle
        key={s.label}
        cx={cx} cy={cy} r={r}
        fill="none" stroke={s.color} strokeWidth={thickness}
        strokeDasharray={`${frac * circ} ${circ}`}
        strokeDashoffset={-offset * circ}
        transform={`rotate(-90 ${cx} ${cy})`}
      />
    );
    offset += frac;
    return seg;
  }) : null;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
      <svg width={size} height={size} style={{ flexShrink: 0 }}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={thickness} />
        {arcs}
        {centerLabel != null && (
          <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central"
                fill="var(--text)" fontSize="20" fontWeight="700" fontFamily="monospace">
            {centerLabel}
          </text>
        )}
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {segments.map((s) => (
          <div key={s.label} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: s.color, flexShrink: 0 }} />
            <span className="muted">{s.label}</span>
            <span style={{ marginLeft: 'auto', fontWeight: 700 }}>{s.value}</span>
            {total > 0 && (
              <span className="muted" style={{ fontSize: 10, width: 38, textAlign: 'right' }}>
                {Math.round((s.value / total) * 100)}%
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

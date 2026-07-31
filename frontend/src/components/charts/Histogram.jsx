// Vertical histogram for bucketed counts. `data`: [{label, value}]. Optional
// per-bar `colors` (else a single `color`). Used for the delay distribution.
export default function Histogram({ data = [], color = '#38bdf8', colors, height = 150 }) {
  const max = Math.max(1, ...data.map((d) => d.value || 0));
  const barW = 100 / Math.max(1, data.length);
  return (
    <svg width="100%" height={height} preserveAspectRatio="none" style={{ display: 'block' }}>
      {data.map((d, i) => {
        const h = ((d.value || 0) / max) * (height - 26);
        const x = `${i * barW + barW * 0.15}%`;
        const w = `${barW * 0.7}%`;
        const y = height - 18 - h;
        return (
          <g key={i}>
            <rect x={x} y={y} width={w} height={h} rx="2" fill={colors ? colors[i] : color} />
            <text x={`${i * barW + barW * 0.5}%`} y={y - 4} textAnchor="middle"
                  fill="var(--text-muted)" fontSize="9" fontFamily="monospace">{d.value}</text>
            <text x={`${i * barW + barW * 0.5}%`} y={height - 4} textAnchor="middle"
                  fill="var(--text-muted)" fontSize="9">{d.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

// Vertical histogram for bucketed counts. `data`: [{label, value}]. Optional
// per-bar `colors` (else a single `color`). Used for the delay distribution.
export default function Histogram({ data = [], color = '#38bdf8', colors, height = 150 }) {
  const max = Math.max(1, ...data.map((d) => d.value || 0));
  const barW = 100 / Math.max(1, data.length);
  const total = data.reduce((s, d) => s + (d.value || 0), 0);
  return (
    <svg width="100%" height={height} preserveAspectRatio="none" style={{ display: 'block' }}
         role="img" aria-label={data.map((d) => `${d.label}: ${d.value}`).join(', ')}>
      {/* Baseline so bars sit on a visible floor rather than floating. */}
      <line x1="0" y1={height - 18} x2="100%" y2={height - 18}
            stroke="rgba(255,255,255,0.07)" strokeWidth="1" />
      {data.map((d, i) => {
        const h = ((d.value || 0) / max) * (height - 26);
        const x = `${i * barW + barW * 0.15}%`;
        const w = `${barW * 0.7}%`;
        const y = height - 18 - h;
        const pct = total ? Math.round(((d.value || 0) / total) * 100) : 0;
        return (
          <g key={i} style={{ cursor: 'default' }}>
            {/* NB: bars are positioned in `%` for responsiveness, so a hand-built
                path with rounded top corners isn't possible (SVG path data can't
                mix units / use calc). Keep the plain rect with a small uniform rx. */}
            <rect x={x} y={y} width={w} height={h} rx="2" fill={colors ? colors[i] : color} />
            <text x={`${i * barW + barW * 0.5}%`} y={y - 4} textAnchor="middle"
                  fill="var(--text-primary)" fontSize="9" fontFamily="var(--font-mono)"
                  fontWeight="600">{d.value}</text>
            <text x={`${i * barW + barW * 0.5}%`} y={height - 4} textAnchor="middle"
                  fill="var(--text-muted)" fontSize="9">{d.label}</text>
            {/* Value + share as a tooltip — the share is the useful comparison and
                there's no room to print it inline at this density. */}
            <rect x={x} y={0} width={w} height={height} fill="transparent">
              <title>{`${d.label}: ${d.value} flights (${pct}%)`}</title>
            </rect>
          </g>
        );
      })}
    </svg>
  );
}

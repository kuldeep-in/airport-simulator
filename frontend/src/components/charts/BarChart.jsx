// Vertical grouped/stacked bar chart (inline SVG). `data`: [{label, a, b}] where
// `a` (arrivals) and `b` (departures) stack. `labels` optional axis captions.
const COL_A = '#38bdf8'; // arrivals
const COL_B = '#fbbf24'; // departures

export default function BarChart({ data = [], height = 150 }) {
  const max = Math.max(1, ...data.map((d) => (d.a || 0) + (d.b || 0)));
  const barW = 100 / Math.max(1, data.length); // percentage width per slot

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 8, fontSize: 11 }}>
        <Legend color={COL_A} label="Arrivals" />
        <Legend color={COL_B} label="Departures" />
      </div>
      <svg width="100%" height={height} preserveAspectRatio="none" style={{ display: 'block' }}>
        {data.map((d, i) => {
          const total = (d.a || 0) + (d.b || 0);
          const hTot = (total / max) * (height - 18);
          const hA = (( d.a || 0) / max) * (height - 18);
          const x = `${i * barW + barW * 0.15}%`;
          const w = `${barW * 0.7}%`;
          const yTop = height - 18 - hTot;
          return (
            <g key={i}>
              {d.b > 0 && (
                <rect x={x} y={yTop} width={w} height={hTot - hA} fill={COL_B} rx="1" />
              )}
              {d.a > 0 && (
                <rect x={x} y={height - 18 - hA} width={w} height={hA} fill={COL_A} rx="1" />
              )}
              {total > 0 && (
                <text x={`${i * barW + barW * 0.5}%`} y={yTop - 3} textAnchor="middle"
                      fill="var(--text-muted)" fontSize="9" fontFamily="monospace">{total}</text>
              )}
              <text x={`${i * barW + barW * 0.5}%`} y={height - 5} textAnchor="middle"
                    fill="var(--text-muted)" fontSize="9" fontFamily="monospace">{d.label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Legend({ color, label }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      <span style={{ width: 9, height: 9, borderRadius: 2, background: color }} />
      <span className="muted">{label}</span>
    </span>
  );
}

// Row × column intensity grid. `rows`: [{ key, label }]. `cols`: [{key,label}].
// `values`: { `${rowKey}|${colKey}`: number }. Colour ramps 0→max. `highlightCol`
// draws a marker on the current column. Used for delay-by-hour and gate×hour.
function ramp(t, hue) {
  // t in 0..1 → CSS colour. hue='delay' (blue→red) or 'load' (dark→cyan).
  t = Math.max(0, Math.min(1, t));
  if (hue === 'delay') {
    const stops = [[13, 71, 161], [0, 172, 130], [205, 220, 57], [255, 160, 0], [229, 57, 53]];
    const seg = t * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(seg));
    const f = seg - i;
    const c = [0, 1, 2].map((k) => Math.round(stops[i][k] + (stops[i + 1][k] - stops[i][k]) * f));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }
  return `rgba(56,189,248,${0.12 + 0.88 * t})`;
}

export default function Heatmap({ rows = [], cols = [], values = {}, max, hue = 'load',
                                  cell = 22, gap = 2, highlightCol }) {
  const vmax = max || Math.max(1, ...Object.values(values));
  const labelW = 26;
  const gridW = labelW + cols.length * (cell + gap);
  const gridH = rows.length * (cell + gap) + 16;
  return (
    <svg width="100%" viewBox={`0 0 ${gridW} ${gridH}`} style={{ display: 'block', maxWidth: gridW * 1.4 }}>
      {cols.map((c, ci) => (
        <text key={c.key} x={labelW + ci * (cell + gap) + cell / 2} y={10} textAnchor="middle"
              fontSize="7" fill={c.key === highlightCol ? '#38bdf8' : 'var(--text-muted)'}
              fontWeight={c.key === highlightCol ? 700 : 400}>{c.label}</text>
      ))}
      {rows.map((r, ri) => (
        <g key={r.key}>
          <text x={labelW - 6} y={16 + ri * (cell + gap) + cell / 2 + 3} textAnchor="end"
                fontSize="9" fill="var(--text-muted)" fontWeight={600}>{r.label}</text>
          {cols.map((c, ci) => {
            const v = values[`${r.key}|${c.key}`] || 0;
            return (
              <rect key={c.key} x={labelW + ci * (cell + gap)} y={16 + ri * (cell + gap)}
                    width={cell} height={cell} rx="2"
                    fill={v ? ramp(v / vmax, hue) : 'rgba(255,255,255,0.05)'}
                    stroke={c.key === highlightCol ? '#38bdf8' : 'none'} strokeWidth={c.key === highlightCol ? 1 : 0}>
                <title>{`${r.label} @ ${c.label}: ${v}`}</title>
              </rect>
            );
          })}
        </g>
      ))}
    </svg>
  );
}

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
    <div>
    <svg width="100%" viewBox={`0 0 ${gridW} ${gridH}`} style={{ display: 'block', maxWidth: gridW * 1.4 }}
         role="img" aria-label={`Heatmap, ${rows.length} rows by ${cols.length} columns, max value ${vmax}`}>
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
            const key = `${r.key}|${c.key}`;
            // Distinguish "no observation" from "observed zero" — both previously
            // rendered as the same empty grey, so a quiet hour and a missing hour
            // were indistinguishable.
            const has = Object.prototype.hasOwnProperty.call(values, key);
            const v = values[key] || 0;
            return (
              <rect key={c.key} x={labelW + ci * (cell + gap)} y={16 + ri * (cell + gap)}
                    width={cell} height={cell} rx="2"
                    fill={has && v ? ramp(v / vmax, hue)
                                   : has ? 'rgba(255,255,255,0.10)' : 'rgba(255,255,255,0.035)'}
                    stroke={c.key === highlightCol ? '#38bdf8' : 'none'} strokeWidth={c.key === highlightCol ? 1 : 0}>
                <title>{has ? `${r.label} @ ${c.label}: ${v}` : `${r.label} @ ${c.label}: no data`}</title>
              </rect>
            );
          })}
        </g>
      ))}
    </svg>
    {/* Scale legend: the skill's chart rules call out "relying on color alone to
        convey meaning" as the anti-pattern for heatmaps. This anchors the ramp to
        actual numbers so a colour can be read as a value. */}
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8,
                  fontSize: 9, color: 'var(--text-muted)' }}>
      <span>0</span>
      <div style={{ display: 'flex', flex: '0 0 108px', height: 6, borderRadius: 3, overflow: 'hidden' }}>
        {Array.from({ length: 12 }, (_, i) => (
          <div key={i} style={{ flex: 1, background: ramp(i / 11, hue) }} />
        ))}
      </div>
      <span className="mono">{Math.round(vmax)}</span>
      <span style={{ marginLeft: 4, opacity: 0.75 }}>
        {hue === 'delay' ? 'avg delay (min)' : 'flights'}
      </span>
      <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 8, height: 8, borderRadius: 2, background: 'rgba(255,255,255,0.035)' }} />
        no data
      </span>
    </div>
    </div>
  );
}

// Horizontal ranked bar list, matching the Gates KPI-card bar look.
// `items`: [{label, value, sub?, color?}]. Bars scale to the max value; an item's
// own `color` overrides the list `color` (used to tint routes by delay).
export default function HBarList({ items = [], color = 'var(--accent-blue)', valueSuffix = '' }) {
  const max = Math.max(1, ...items.map((x) => x.value || 0));
  if (!items.length) {
    return <div className="muted" style={{ fontSize: 11, padding: '4px 0' }}>No data yet.</div>;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {items.map((x) => (
        <div key={x.label} style={{ fontSize: 12 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ fontWeight: 700, width: 52 }}>{x.label}</span>
            {x.sub && <span className="muted" style={{ fontSize: 10 }}>{x.sub}</span>}
            <span style={{ marginLeft: 'auto', fontWeight: 700 }}>{x.value}{valueSuffix}</span>
          </div>
          <div style={{ marginTop: 4, height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
            <div style={{ width: `${(x.value / max) * 100}%`, height: '100%', background: x.color || color }} />
          </div>
        </div>
      ))}
    </div>
  );
}

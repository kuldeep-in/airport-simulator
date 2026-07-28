// Big-number stat tile in the app's ops-center card style.
export default function StatTile({ label, value, unit, color = 'var(--text)', sub }) {
  return (
    <div className="card" style={{ padding: '12px 14px', minWidth: 120 }}>
      <div className="muted" style={{ fontSize: 10, letterSpacing: 0.5, textTransform: 'uppercase' }}>
        {label}
      </div>
      <div style={{ marginTop: 6, display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span style={{ fontSize: 26, fontWeight: 700, color, lineHeight: 1 }}>{value}</span>
        {unit && <span className="muted" style={{ fontSize: 12 }}>{unit}</span>}
      </div>
      {sub && <div className="muted" style={{ fontSize: 10, marginTop: 5 }}>{sub}</div>}
    </div>
  );
}

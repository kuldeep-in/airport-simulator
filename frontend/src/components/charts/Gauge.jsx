// Radial capacity gauge (270° arc). `value` vs `max`; colour thresholds by
// fraction of capacity (green → amber → red). Used for runway/hour pressure.
export default function Gauge({ value = 0, max = 100, label, unit, size = 130 }) {
  const frac = Math.max(0, Math.min(1, max ? value / max : 0));
  const color = frac >= 0.9 ? '#ef4444' : frac >= 0.7 ? '#fbbf24' : '#22c55e';
  const R = size / 2 - 10;
  const cx = size / 2, cy = size / 2;
  const START = 135, SWEEP = 270;           // degrees; gap at the bottom
  const polar = (deg) => {
    const r = (deg * Math.PI) / 180;
    return [cx + R * Math.cos(r), cy + R * Math.sin(r)];
  };
  const arc = (fromFrac, toFrac) => {
    const a0 = START + SWEEP * fromFrac, a1 = START + SWEEP * toFrac;
    const [x0, y0] = polar(a0), [x1, y1] = polar(a1);
    const large = a1 - a0 > 180 ? 1 : 0;
    return `M${x0.toFixed(1)},${y0.toFixed(1)} A${R},${R} 0 ${large} 1 ${x1.toFixed(1)},${y1.toFixed(1)}`;
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <svg width={size} height={size * 0.82} viewBox={`0 0 ${size} ${size * 0.9}`}>
        <path d={arc(0, 1)} fill="none" stroke="rgba(255,255,255,0.10)" strokeWidth={10} strokeLinecap="round" />
        {frac > 0 && (
          <path d={arc(0, frac)} fill="none" stroke={color} strokeWidth={10} strokeLinecap="round" />
        )}
        <text x={cx} y={cy + 2} textAnchor="middle" fontSize={26} fontWeight={700} fill={color}>{value}</text>
        {unit && <text x={cx} y={cy + 20} textAnchor="middle" fontSize={10} fill="var(--text-muted)">{unit}</text>}
      </svg>
      {label && <div className="muted" style={{ fontSize: 11, marginTop: -4 }}>{label}</div>}
    </div>
  );
}

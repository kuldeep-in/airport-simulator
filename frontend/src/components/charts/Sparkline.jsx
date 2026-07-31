// Tiny inline-SVG trend line for a stat tile — a rolling buffer of recent values.
// `data`: number[]. Draws a smooth polyline + a soft area fill, auto-scaled.
export default function Sparkline({ data = [], color = '#38bdf8', width = 96, height = 26 }) {
  if (data.length < 2) return <svg width={width} height={height} />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = width / (data.length - 1);
  const pts = data.map((v, i) => [i * stepX, height - 2 - ((v - min) / span) * (height - 4)]);
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area = `${line} L${width},${height} L0,${height} Z`;
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      <path d={area} fill={color} opacity={0.12} />
      <path d={line} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r={2} fill={color} />
    </svg>
  );
}

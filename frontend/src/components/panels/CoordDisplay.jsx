import { useState } from 'react';
import { useSimStore } from '../../store/useSimStore';

export default function CoordDisplay() {
  const coord = useSimStore((s) => s.mapClickCoord);
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    if (!coord) return;
    navigator.clipboard.writeText(`[${coord.lon}, ${coord.lat}]`).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div style={{
      background: 'rgba(8, 14, 28, 0.82)',
      border: '1px solid rgba(255,160,64,0.35)',
      borderRadius: 6,
      padding: '6px 10px',
      fontFamily: 'monospace',
      fontSize: 12,
      backdropFilter: 'blur(6px)',
      display: 'flex',
      alignItems: 'center',
      gap: 10,
    }}>
      <span style={{ color: '#6070a0', fontSize: 10, letterSpacing: 1.2, textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
        Coords
      </span>
      <span style={{ color: coord ? '#ffa040' : '#3a4060', fontWeight: coord ? 700 : 400, whiteSpace: 'nowrap' }}>
        {coord ? `${coord.lat} N, ${coord.lon} E` : 'Click on map'}
      </span>
      {coord && (
        <button
          onClick={handleCopy}
          style={{
            background: copied ? 'rgba(80,200,120,0.18)' : 'rgba(255,160,64,0.12)',
            border: `1px solid ${copied ? 'rgba(80,200,120,0.5)' : 'rgba(255,160,64,0.3)'}`,
            borderRadius: 4,
            color: copied ? '#50c878' : '#ffa040',
            cursor: 'pointer',
            fontSize: 10,
            fontFamily: 'monospace',
            padding: '2px 7px',
            whiteSpace: 'nowrap',
            transition: 'all 0.15s',
            pointerEvents: 'auto',
          }}
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      )}
    </div>
  );
}

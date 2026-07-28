import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { useSimStore } from '../../store/useSimStore';
import { GATE_LONLAT, GATE_DIRS } from '../../data/gateData.js';

const CONCOURSES = ['A', 'B', 'C', 'D', 'E'];
const ALL_GATE_IDS = Object.keys(GATE_LONLAT).sort((a, b) => {
  const [, ac, an] = a.match(/([A-Z])(\d+)/);
  const [, bc, bn] = b.match(/([A-Z])(\d+)/);
  return ac < bc ? -1 : ac > bc ? 1 : parseInt(an) - parseInt(bn);
});

const DIR_PRESETS = [
  { label: '→ E', x:  1, z:  0 },
  { label: '← W', x: -1, z:  0 },
  { label: '↓ S', x:  0, z:  1 },
  { label: '↑ N', x:  0, z: -1 },
];

function fmt6(n) {
  return typeof n === 'number' ? n.toFixed(6) : n;
}

export default function GateEditor({ onClose }) {
  const [selected, setSelected] = useState('C1');
  const [copied, setCopied] = useState(false);
  // Track the "Copied!" reset timer so it can be cleared on unmount (avoids a
  // setState-after-unmount warning if the panel closes within the 2s window).
  const copyTimer = useRef(null);
  useEffect(() => () => clearTimeout(copyTimer.current), []);

  const gateEdits       = useSimStore((s) => s.gateEdits);
  const setGateEdit     = useSimStore((s) => s.setGateEdit);
  const clearGateEdits  = useSimStore((s) => s.clearGateEdits);
  const mapClickCoord   = useSimStore((s) => s.mapClickCoord);

  // Current values: edited if present, else defaults
  const defaultLon = GATE_LONLAT[selected][0];
  const defaultLat = GATE_LONLAT[selected][1];
  const defaultDirX = GATE_DIRS[selected][0];
  const defaultDirZ = GATE_DIRS[selected][2];

  const edit = gateEdits[selected];
  const lon  = edit?.lon  ?? defaultLon;
  const lat  = edit?.lat  ?? defaultLat;
  const dirX = edit?.dirX ?? defaultDirX;
  const dirZ = edit?.dirZ ?? defaultDirZ;
  const isEdited = !!edit;

  function update(field, value) {
    const parsed = parseFloat(value);
    if (isNaN(parsed)) return;
    setGateEdit(selected, {
      lon: field === 'lon' ? parsed : lon,
      lat: field === 'lat' ? parsed : lat,
      dirX: field === 'dirX' ? parsed : dirX,
      dirZ: field === 'dirZ' ? parsed : dirZ,
    });
  }

  function useMapClick() {
    if (!mapClickCoord) return;
    setGateEdit(selected, {
      lon: parseFloat(parseFloat(mapClickCoord.lon).toFixed(6)),
      lat: parseFloat(parseFloat(mapClickCoord.lat).toFixed(6)),
      dirX,
      dirZ,
    });
  }

  function resetSelected() {
    setGateEdit(selected, null);
  }

  function copyJS() {
    const lonlatLines = ALL_GATE_IDS.map((id) => {
      const e = gateEdits[id];
      const [l, t] = e ? [e.lon, e.lat] : GATE_LONLAT[id];
      return `  ${id.padEnd(3)}: [${fmt6(l)}, ${fmt6(t)}],`;
    });

    const dirLines = ALL_GATE_IDS.map((id) => {
      const e = gateEdits[id];
      const [ox, , oz] = GATE_DIRS[id];
      const dx = e ? e.dirX : ox;
      const dz = e ? e.dirZ : oz;
      return `  ${id.padEnd(3)}: [${dx.toFixed(2).padStart(5)}, 0, ${dz.toFixed(2).padStart(5)}],`;
    });

    const out = `export const GATE_LONLAT = {\n${lonlatLines.join('\n')}\n};\n\nexport const GATE_DIRS = {\n${dirLines.join('\n')}\n};`;

    navigator.clipboard.writeText(out).then(() => {
      setCopied(true);
      clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 2000);
    });
  }

  const editedCount = Object.keys(gateEdits).length;

  const panelStyle = {
    background: 'rgba(8,14,28,0.96)',
    border: '1px solid rgba(255,160,64,0.35)',
    borderRadius: 8,
    fontFamily: 'monospace',
    fontSize: 12,
    color: '#e2e8f0',
    width: 420,
    maxHeight: '80vh',
    display: 'flex',
    flexDirection: 'column',
    backdropFilter: 'blur(8px)',
    overflow: 'hidden',
  };

  return (
    <div style={panelStyle}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '10px 14px',
        borderBottom: '1px solid rgba(255,160,64,0.2)',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: '#ffa040', letterSpacing: 1 }}>
          GATE LABEL EDITOR
        </span>
        {editedCount > 0 && (
          <span style={{ fontSize: 10, color: '#fbbf24', marginLeft: 8 }}>
            {editedCount} edited
          </span>
        )}
        <div style={{ flex: 1 }} />
        <X size={16} style={{ cursor: 'pointer', color: '#64748b' }} onClick={onClose} />
      </div>

      {/* Body: gate list + form side by side */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* Gate list */}
        <div style={{
          width: 120,
          overflowY: 'auto',
          borderRight: '1px solid rgba(255,160,64,0.15)',
          padding: '6px 0',
          flexShrink: 0,
        }}>
          {CONCOURSES.map((c) => {
            const gates = ALL_GATE_IDS.filter((id) => id.startsWith(c));
            return (
              <div key={c}>
                <div style={{
                  padding: '3px 10px',
                  fontSize: 10,
                  color: '#64748b',
                  letterSpacing: 1,
                  textTransform: 'uppercase',
                }}>
                  Concourse {c}
                </div>
                {gates.map((id) => {
                  const active   = id === selected;
                  const modified = !!gateEdits[id];
                  return (
                    <div
                      key={id}
                      onClick={() => setSelected(id)}
                      style={{
                        padding: '4px 10px',
                        cursor: 'pointer',
                        background: active ? 'rgba(255,160,64,0.15)' : 'transparent',
                        color: active ? '#ffa040' : modified ? '#fbbf24' : '#e2e8f0',
                        fontWeight: active ? 700 : 400,
                        borderLeft: active ? '2px solid #ffa040' : '2px solid transparent',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                      }}
                    >
                      {id}
                      {modified && <span style={{ fontSize: 8, color: '#fbbf24' }}>●</span>}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>

        {/* Edit form */}
        <div style={{ flex: 1, padding: '12px 14px', overflowY: 'auto' }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10, color: '#ffa040' }}>
            {selected}
            {isEdited && (
              <span style={{ fontSize: 10, color: '#fbbf24', marginLeft: 8, fontWeight: 400 }}>
                modified
              </span>
            )}
          </div>

          {/* Coordinates */}
          <div style={{ marginBottom: 10 }}>
            <Label>Longitude</Label>
            <input
              type="number"
              step="0.000001"
              value={fmt6(lon)}
              onChange={(e) => update('lon', e.target.value)}
              style={inputStyle}
            />
          </div>
          <div style={{ marginBottom: 10 }}>
            <Label>Latitude</Label>
            <input
              type="number"
              step="0.000001"
              value={fmt6(lat)}
              onChange={(e) => update('lat', e.target.value)}
              style={inputStyle}
            />
          </div>

          {/* Map click */}
          <button
            onClick={useMapClick}
            disabled={!mapClickCoord}
            style={{
              ...btnStyle,
              background: mapClickCoord ? 'rgba(56,189,248,0.15)' : 'rgba(255,255,255,0.04)',
              color: mapClickCoord ? '#38bdf8' : '#475569',
              border: `1px solid ${mapClickCoord ? 'rgba(56,189,248,0.4)' : 'rgba(255,255,255,0.1)'}`,
              marginBottom: 14,
              width: '100%',
            }}
          >
            {mapClickCoord
              ? `Use map click (${parseFloat(mapClickCoord.lat).toFixed(5)}, ${parseFloat(mapClickCoord.lon).toFixed(5)})`
              : 'Click on map to capture coords'}
          </button>

          {/* Direction */}
          <Label>Direction (apron-facing)</Label>
          <div style={{ display: 'flex', gap: 4, marginBottom: 6, flexWrap: 'wrap' }}>
            {DIR_PRESETS.map(({ label, x, z }) => {
              const active = Math.abs(dirX - x) < 0.01 && Math.abs(dirZ - z) < 0.01;
              return (
                <button
                  key={label}
                  onClick={() => setGateEdit(selected, { lon, lat, dirX: x, dirZ: z })}
                  style={{
                    ...btnStyle,
                    background: active ? 'rgba(255,160,64,0.2)' : 'rgba(255,255,255,0.06)',
                    color: active ? '#ffa040' : '#94a3b8',
                    border: `1px solid ${active ? 'rgba(255,160,64,0.5)' : 'rgba(255,255,255,0.12)'}`,
                    fontWeight: active ? 700 : 400,
                  }}
                >
                  {label}
                </button>
              );
            })}
          </div>

          {/* Custom dir values */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
            <div style={{ flex: 1 }}>
              <Label>Dir X</Label>
              <input
                type="number"
                step="0.01"
                value={dirX.toFixed(2)}
                onChange={(e) => update('dirX', e.target.value)}
                style={inputStyle}
              />
            </div>
            <div style={{ flex: 1 }}>
              <Label>Dir Z</Label>
              <input
                type="number"
                step="0.01"
                value={dirZ.toFixed(2)}
                onChange={(e) => update('dirZ', e.target.value)}
                style={inputStyle}
              />
            </div>
          </div>

          {/* Current defaults (reference) */}
          <div style={{
            background: 'rgba(255,255,255,0.04)',
            borderRadius: 4,
            padding: '6px 8px',
            marginBottom: 6,
            fontSize: 10,
            color: '#64748b',
            lineHeight: 1.7,
          }}>
            Default: [{fmt6(defaultLon)}, {fmt6(defaultLat)}]<br />
            Dir: [{defaultDirX.toFixed(2)}, 0, {defaultDirZ.toFixed(2)}]
          </div>

          {isEdited && (
            <button onClick={resetSelected} style={{ ...btnStyle, color: '#f87171', border: '1px solid rgba(248,113,113,0.3)', width: '100%', marginBottom: 4 }}>
              Reset {selected} to default
            </button>
          )}
        </div>
      </div>

      {/* Footer */}
      <div style={{
        display: 'flex',
        gap: 8,
        padding: '10px 14px',
        borderTop: '1px solid rgba(255,160,64,0.2)',
        flexShrink: 0,
      }}>
        {editedCount > 0 && (
          <button onClick={clearGateEdits} style={{ ...btnStyle, color: '#f87171', border: '1px solid rgba(248,113,113,0.3)' }}>
            Clear all
          </button>
        )}
        <div style={{ flex: 1 }} />
        <button
          onClick={copyJS}
          style={{
            ...btnStyle,
            background: copied ? 'rgba(80,200,120,0.18)' : 'rgba(255,160,64,0.12)',
            color: copied ? '#50c878' : '#ffa040',
            border: `1px solid ${copied ? 'rgba(80,200,120,0.5)' : 'rgba(255,160,64,0.4)'}`,
            fontWeight: 700,
            padding: '5px 14px',
          }}
        >
          {copied ? '✓ Copied!' : 'Copy updated JS'}
        </button>
      </div>
    </div>
  );
}

function Label({ children }) {
  return (
    <div style={{ fontSize: 10, color: '#64748b', letterSpacing: 0.8, marginBottom: 3, textTransform: 'uppercase' }}>
      {children}
    </div>
  );
}

const inputStyle = {
  width: '100%',
  background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,160,64,0.25)',
  borderRadius: 4,
  color: '#e2e8f0',
  fontFamily: 'monospace',
  fontSize: 12,
  padding: '4px 7px',
  outline: 'none',
  boxSizing: 'border-box',
};

const btnStyle = {
  background: 'transparent',
  border: '1px solid rgba(255,255,255,0.15)',
  borderRadius: 4,
  color: '#94a3b8',
  cursor: 'pointer',
  fontFamily: 'monospace',
  fontSize: 11,
  padding: '4px 9px',
};

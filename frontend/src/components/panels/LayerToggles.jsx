import { Plane, Building2, Users, Thermometer } from 'lucide-react';
import { useSimStore } from '../../store/useSimStore';

function Toggle({ on, onClick }) {
  return (
    <div className={`switch ${on ? 'on' : ''}`} onClick={onClick}>
      <div className="knob" />
    </div>
  );
}

const LAYER_DEFS = [
  { key: 'gates',      label: 'Gates',       icon: Building2  },
  { key: 'aircraft',   label: 'Aircraft',    icon: Plane      },
  { key: 'paxHeatmap', label: 'Pax Density', icon: Users,
    legend: { gradient: 'linear-gradient(to right,#0d47a1,#00ac82,#4caf50,#cddc39,#ffa000,#e53935,#b71c1c)', lo: '0', hi: '4 pax/m²' } },
  { key: 'tempZones',  label: 'Temperature', icon: Thermometer,
    legend: { gradient: 'linear-gradient(to right,#0d47a1,#1e88e5,#00ac82,#4caf50,#cddc39,#ffa000,#e53935,#b71c1c)', lo: '18°C', hi: '40°C' } },
];

export default function LayerToggles() {
  const layers = useSimStore((s) => s.layers);
  const toggleLayer = useSimStore((s) => s.toggleLayer);

  return (
    <div className="panel" style={{ padding: 14, borderRadius: 0 }}>
      <div
        className="muted"
        style={{ fontSize: 10, letterSpacing: 1, marginBottom: 8 }}
      >
        LAYERS
      </div>
      {LAYER_DEFS.map(({ key, label, icon: Icon, legend }) => (
        <div key={key} style={{ padding: '4px 0' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Icon size={15} className="muted" />
              <span>{label}</span>
            </div>
            <Toggle on={layers[key]} onClick={() => toggleLayer(key)} />
          </div>
          {legend && layers[key] && (
            <div style={{ marginTop: 4, paddingLeft: 23 }}>
              <div style={{ height: 5, borderRadius: 3, background: legend.gradient }} />
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 2 }}>
                <span className="muted" style={{ fontSize: 9 }}>{legend.lo}</span>
                <span className="muted" style={{ fontSize: 9 }}>{legend.hi}</span>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

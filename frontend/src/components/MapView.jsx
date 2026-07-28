import { useMemo, useState, useCallback } from 'react';
import DeckGL from '@deck.gl/react';
import { Map } from 'react-map-gl/maplibre';
import { GeoJsonLayer } from '@deck.gl/layers';
import { useSimStore } from '../store/useSimStore';
import { buildGatesLayer, gateTooltip } from './layers/GatesLayer';
import { buildAircraftLayer, aircraftTooltip } from './layers/AircraftLayer';
import { buildPaxHeatmapLayer } from './layers/PaxHeatmapLayer';
import { buildTempZoneLayer, tempTooltip } from './layers/TempZoneLayer';

const MAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';

const INITIAL_VIEW_STATE = {
  longitude: 51.608,
  latitude: 25.273,
  zoom: 13.5,
  pitch: 0,
  bearing: 0,
};

// base geometry colors by feature type
const BASE_COLORS = {
  runway: [30, 41, 82, 255], // dark blue
  taxiway: [26, 32, 44, 255], // dark
  terminal: [45, 55, 72, 255], // dark gray
  apron: [17, 22, 34, 255], // darkest
};

export default function MapView({ baseData, gatesData, zonesData, onViewStateChange }) {
  const gates = useSimStore((s) => s.gates);
  const positions = useSimStore((s) => s.positions);
  const flights = useSimStore((s) => s.flights);
  const zoneMetrics = useSimStore((s) => s.zoneMetrics);
  const layersOn = useSimStore((s) => s.layers);
  const setSelectedEntity = useSimStore((s) => s.setSelectedEntity);

  const [viewState, setViewState] = useState(INITIAL_VIEW_STATE);

  const positionsArr = useMemo(() => Object.values(positions), [positions]);

  const handleViewState = useCallback(
    (e) => {
      setViewState(e.viewState);
      if (onViewStateChange) onViewStateChange(e.viewState);
    },
    [onViewStateChange]
  );

  const selectGate = useCallback(
    (id) => setSelectedEntity({ type: 'gate', id }),
    [setSelectedEntity]
  );
  const selectAircraft = useCallback(
    (id) => setSelectedEntity({ type: 'aircraft', id }),
    [setSelectedEntity]
  );

  const layers = useMemo(() => {
    const arr = [];
    // 1. base geometry
    if (baseData) {
      arr.push(
        new GeoJsonLayer({
          id: 'base-layer',
          data: baseData,
          stroked: true,
          filled: true,
          getFillColor: (f) => BASE_COLORS[f.properties.type] || [30, 30, 40, 255],
          getLineColor: [60, 70, 90, 200],
          getLineWidth: 1,
          lineWidthUnits: 'pixels',
          pickable: false,
        })
      );
    }
    // 2. temp zones
    if (layersOn.tempZones && zonesData) {
      arr.push(buildTempZoneLayer(zonesData, zoneMetrics));
    }
    // 3. pax heatmap
    if (layersOn.paxHeatmap && zonesData) {
      arr.push(buildPaxHeatmapLayer(zonesData, zoneMetrics));
    }
    // 4. gates
    if (layersOn.gates && gatesData) {
      arr.push(buildGatesLayer(gatesData, gates, selectGate));
    }
    // 5. aircraft
    if (layersOn.aircraft) {
      arr.push(buildAircraftLayer(positionsArr, flights, selectAircraft));
    }
    return arr;
  }, [
    baseData,
    gatesData,
    zonesData,
    layersOn,
    gates,
    zoneMetrics,
    positionsArr,
    flights,
    selectGate,
    selectAircraft,
  ]);

  const getTooltip = useCallback(
    ({ layer, object }) => {
      if (!object || !layer) return null;
      if (layer.id === 'gates-layer') return gateTooltip(object, gates);
      if (layer.id === 'aircraft-layer') return aircraftTooltip(object, flights);
      if (layer.id === 'temp-zone-layer') return tempTooltip(object, zoneMetrics);
      return null;
    },
    [gates, flights, zoneMetrics]
  );

  return (
    <DeckGL
      viewState={viewState}
      onViewStateChange={handleViewState}
      controller={{ inertia: true, dragRotate: false }}
      layers={layers}
      getTooltip={getTooltip}
      style={{ position: 'absolute', inset: 0 }}
    >
      <Map reuseMaps mapStyle={MAP_STYLE} attributionControl={false} />
    </DeckGL>
  );
}

export { INITIAL_VIEW_STATE };

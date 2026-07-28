import { useMemo } from 'react';
import DeckGL from '@deck.gl/react';
import { GeoJsonLayer, PolygonLayer } from '@deck.gl/layers';

const MINI_VIEW = {
  longitude: 51.61,
  latitude: 25.273,
  zoom: 11.6,
  pitch: 0,
  bearing: 0,
};

const BASE_COLORS = {
  runway: [56, 189, 248, 220],
  taxiway: [45, 55, 72, 255],
  terminal: [100, 116, 139, 255],
  apron: [30, 41, 59, 255],
};

/**
 * Small overview inset. Shows the airport base geometry plus a rectangle
 * approximating the main map viewport.
 */
export default function Minimap({ baseData, mainViewState }) {
  // approximate viewport rectangle from center + zoom
  const viewportPoly = useMemo(() => {
    if (!mainViewState) return null;
    // rough degrees span visible at a given zoom (empirical for ~1000px width)
    const span = 360 / Math.pow(2, mainViewState.zoom) / 1.5;
    const halfLon = span / 2;
    const halfLat = span / 3;
    const { longitude: lon, latitude: lat } = mainViewState;
    return [
      [
        [lon - halfLon, lat - halfLat],
        [lon + halfLon, lat - halfLat],
        [lon + halfLon, lat + halfLat],
        [lon - halfLon, lat + halfLat],
        [lon - halfLon, lat - halfLat],
      ],
    ];
  }, [mainViewState]);

  const layers = useMemo(() => {
    const arr = [];
    if (baseData) {
      arr.push(
        new GeoJsonLayer({
          id: 'mini-base',
          data: baseData,
          filled: true,
          stroked: false,
          getFillColor: (f) => BASE_COLORS[f.properties.type] || [40, 40, 50, 255],
        })
      );
    }
    if (viewportPoly) {
      arr.push(
        new PolygonLayer({
          id: 'mini-viewport',
          data: [{ polygon: viewportPoly }],
          getPolygon: (d) => d.polygon,
          filled: false,
          stroked: true,
          getLineColor: [251, 191, 36, 255],
          getLineWidth: 2,
          lineWidthUnits: 'pixels',
        })
      );
    }
    return arr;
  }, [baseData, viewportPoly]);

  return (
    <div
      style={{
        position: 'absolute',
        bottom: 16,
        right: 16,
        width: 200,
        height: 150,
        borderRadius: 8,
        overflow: 'hidden',
        border: '1px solid var(--border)',
        background: 'var(--bg-base)',
        boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
      }}
    >
      <DeckGL
        viewState={MINI_VIEW}
        controller={false}
        layers={layers}
        style={{ position: 'absolute', inset: 0 }}
      />
      <div
        style={{
          position: 'absolute',
          top: 4,
          left: 6,
          fontSize: 10,
          color: 'var(--text-muted)',
          pointerEvents: 'none',
        }}
      >
        OTHH
      </div>
    </div>
  );
}

import { create } from 'zustand';

export const useSimStore = create((set) => ({
  // WebSocket
  wsConnected: false,

  // Data (all keyed maps for O(1) merge)
  flights: {}, // id -> flight payload (includes lat/lon/status/phase)
  gates: {}, // id -> { id, status }
  positions: {}, // flight_id -> { lat, lon, heading, phase, ground_speed }
  zoneMetrics: {}, // zone_id -> { PAX_DENSITY, TEMPERATURE }
  sensorReadings: {}, // sensor_id -> { sensor_id, lon, lat, temperature, pax_density }

  // UI
  selectedEntity: null, // { type: 'gate'|'aircraft', id }
  mapClickCoord: null,  // { lon, lat } strings from last map click
  layers: {
    gates: true,
    aircraft: true,
    paxHeatmap: false,
    tempZones: false,
  },

  // Front-end animation speed multiplier for fixed-path aircraft (1 | 2 | 4 | 8)
  visualSpeed: 1,

  // Sim controls
  simTime: null,
  simSpeed: 1,
  simPaused: false,

  // ---- actions ----
  setWsConnected: (v) => set({ wsConnected: v }),

  updateFlights: (list) =>
    set((s) => {
      const flights = { ...s.flights };
      const positions = { ...s.positions };
      for (const f of list) {
        if (f.phase === 'INACTIVE') {
          delete flights[f.id];
          delete positions[f.id];
        } else {
          flights[f.id] = { ...flights[f.id], ...f };
          positions[f.id] = {
            flight_id: f.id,
            lat: f.lat,
            lon: f.lon,
            heading: f.heading,
            phase: f.phase,
            ground_speed: f.ground_speed,
          };
        }
      }
      return { flights, positions };
    }),

  updateGates: (list) =>
    set((s) => {
      const gates = { ...s.gates };
      for (const g of list) {
        gates[g.id] = { ...gates[g.id], ...g };
      }
      return { gates };
    }),

  updatePositions: (map) =>
    set((s) => ({ positions: { ...s.positions, ...map } })),

  updateZoneMetrics: (map) =>
    set((s) => {
      const zoneMetrics = { ...s.zoneMetrics };
      for (const zid of Object.keys(map)) {
        zoneMetrics[zid] = { ...zoneMetrics[zid], ...map[zid] };
      }
      return { zoneMetrics };
    }),

  updateSensorReadings: (list) =>
    set((s) => {
      const sr = { ...s.sensorReadings };
      for (const x of list) sr[x.sensor_id] = x;
      return { sensorReadings: sr };
    }),

  // Gate edits: gateId -> { lon, lat, dirX, dirZ } (in-memory overrides for editor)
  gateEdits: {},

  setSelectedEntity: (e) => set({ selectedEntity: e }),
  setMapClickCoord: (coord) => set({ mapClickCoord: coord }),
  setVisualSpeed: (v) => set({ visualSpeed: v }),
  setGateEdit: (id, edit) =>
    set((s) => {
      const gateEdits = { ...s.gateEdits };
      if (edit === null) {
        delete gateEdits[id];
      } else {
        gateEdits[id] = edit;
      }
      return { gateEdits };
    }),
  clearGateEdits: () => set({ gateEdits: {} }),

  toggleLayer: (name) =>
    set((s) => ({ layers: { ...s.layers, [name]: !s.layers[name] } })),

  setSimControls: ({ sim_time, speed, paused }) =>
    set((s) => ({
      simTime: sim_time !== undefined ? sim_time : s.simTime,
      simSpeed: speed !== undefined ? speed : s.simSpeed,
      simPaused: paused !== undefined ? paused : s.simPaused,
    })),

  // full snapshot -> populate everything
  loadFullState: (msg) =>
    set(() => {
      const flights = {};
      const positions = {};
      for (const f of msg.flights || []) {
        flights[f.id] = f;
        positions[f.id] = {
          flight_id: f.id,
          lat: f.lat,
          lon: f.lon,
          heading: f.heading,
          phase: f.phase,
          ground_speed: f.ground_speed,
        };
      }
      const gates = {};
      for (const g of msg.gates || []) gates[g.id] = g;
      const zoneMetrics = { ...(msg.zones || {}) };
      const sensorReadings = {};
      for (const x of msg.sensors || []) sensorReadings[x.sensor_id] = x;
      return {
        flights,
        positions,
        gates,
        zoneMetrics,
        sensorReadings,
        simTime: msg.sim_time,
        simSpeed: msg.speed,
        simPaused: msg.paused,
      };
    }),
}));

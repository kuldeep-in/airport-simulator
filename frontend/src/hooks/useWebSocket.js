import { useEffect, useRef } from 'react';
import { useSimStore } from '../store/useSimStore';

/**
 * Connect to the backend WebSocket, dispatch messages into the store, and
 * auto-reconnect with exponential backoff (capped at 10s).
 */
export function useWebSocket() {
  const wsRef = useRef(null);
  const backoffRef = useRef(500);
  const closedRef = useRef(false);

  useEffect(() => {
    closedRef.current = false;

    const {
      setWsConnected,
      updateFlights,
      updateGates,
      updateZoneMetrics,
      updateSensorReadings,
      setSimControls,
      loadFullState,
    } = useSimStore.getState();

    const connect = () => {
      if (closedRef.current) return;
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${window.location.host}/ws`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        backoffRef.current = 500;
        setWsConnected(true);
      };

      ws.onmessage = (evt) => {
        let msg;
        try {
          msg = JSON.parse(evt.data);
        } catch (e) {
          return;
        }
        switch (msg.type) {
          case 'full_state':
            loadFullState(msg);
            break;
          case 'aircraft_update':
            if (msg.flights) updateFlights(msg.flights);
            break;
          case 'gate_update':
            if (msg.gates) updateGates(msg.gates);
            break;
          case 'zone_metric':
            if (msg.zones) updateZoneMetrics(msg.zones);
            break;
          case 'sim_tick':
            setSimControls({
              sim_time: msg.sim_time,
              speed: msg.speed,
              paused: msg.paused,
            });
            break;
          case 'sensor_update':
            if (msg.sensors) updateSensorReadings(msg.sensors);
            break;
          default:
            break;
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        if (closedRef.current) return;
        const delay = Math.min(backoffRef.current, 10000);
        backoffRef.current = Math.min(backoffRef.current * 2, 10000);
        setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      closedRef.current = true;
      if (wsRef.current) wsRef.current.close();
    };
  }, []);
}

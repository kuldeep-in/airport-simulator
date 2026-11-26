import { useEffect } from 'react';
import { useSimStore } from '../store/useSimStore';

/**
 * Connect to the backend WebSocket, dispatch messages into the store, and
 * auto-reconnect with exponential backoff (capped at 10s).
 *
 * All connection state (the socket, backoff, the reconnect timer, and the
 * "cancelled" flag) is scoped to a SINGLE effect run via closure locals — NOT
 * component-lifetime refs. Under React StrictMode the effect mounts, unmounts,
 * then mounts again; with shared refs the first run's socket `onclose` could fire
 * after the second run reset a shared flag, scheduling a stray reconnect →
 * 2-3 duplicate sockets + orphaned timers. Closure-local state keeps each run's
 * lifecycle fully independent, and cleanup detaches handlers + clears the timer.
 */
export function useWebSocket() {
  useEffect(() => {
    let cancelled = false;      // set on cleanup; stops this run reconnecting
    let ws = null;
    let backoff = 500;
    let reconnectTimer = null;

    const {
      setWsConnected,
      updateFlights,
      updateZoneMetrics,
      updateSensorReadings,
      setSimControls,
      loadFullState,
    } = useSimStore.getState();

    const connect = () => {
      if (cancelled) return;
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${window.location.host}/ws`;
      ws = new WebSocket(url);

      ws.onopen = () => {
        backoff = 500;
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
        if (cancelled) return;
        const delay = Math.min(backoff, 10000);
        backoff = Math.min(backoff * 2, 10000);
        reconnectTimer = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) {
        // Detach handlers first so this (now-stale) socket's onclose can't
        // schedule a reconnect after we've torn the run down.
        ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null;
        ws.close();
      }
    };
  }, []);
}

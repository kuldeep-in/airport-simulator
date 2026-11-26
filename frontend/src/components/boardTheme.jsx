import { useState } from 'react';
import { airlineLogo } from '../lib/airlines.js';

// Airport FIDS-board look, shared by the Departures board and the Schedule page:
// royal-blue panel, white type, light row rules, status as coloured text.
export const PAGE_BG = '#071a5c';
export const BOARD_BG = 'linear-gradient(180deg, #0d2a8a 0%, #12359f 45%, #0f2f93 100%)';
export const ROW_RULE = '1px solid rgba(190, 210, 255, 0.35)';
export const HEAD_COLOR = '#9fb8ff';
export const BOARD_PANEL = {
  background: BOARD_BG, borderRadius: 10, overflow: 'hidden',
  boxShadow: '0 10px 40px rgba(0,0,0,0.45)', border: '1px solid rgba(160,190,255,0.25)',
};
// Same status values as everywhere (lib/flightStatus.js); colours brightened to
// read on the blue board.
export const STATUS_COLOR = {
  CheckIn: '#9be7ff', Boarding: '#5cf28a', GateClosed: '#ffd166',
  Departed: '#c7d2fe', Expected: '#ffffff', Delayed: '#ff7a7a',
  Arrived: '#5cf28a', Approaching: '#9be7ff',
  Diverted: '#ff7a7a', Canceled: '#ff7a7a', Cancelled: '#ff7a7a', Unknown: '#c7d2fe',
};

// The existing airline logo on the same white chip as before (logos unchanged),
// sized for the board. Missing logo → spacer so columns stay aligned.
export function BoardLogo({ code, width = 100, height = 44 }) {
  const [ok, setOk] = useState(true);
  if (!code || !ok) return <div style={{ width, height }} />;
  return (
    <img src={airlineLogo(code)} alt={code} onError={() => setOk(false)}
         style={{ width, height, objectFit: 'contain', borderRadius: 5,
                  background: '#fff', padding: '2px 5px' }} />
  );
}

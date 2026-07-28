import { Suspense, lazy } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import TopTabs from './components/TopTabs';

const Scene3D = lazy(() => import('./components/Scene3D'));
const ConnectionsPage = lazy(() => import('./pages/ConnectionsPage'));
const GatesPage = lazy(() => import('./pages/GatesPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const Radar2DPage = lazy(() => import('./pages/Radar2DPage'));

// 2D Radar is now the HOME page (`/`); the 3D scene moved to `/3d` (hidden from
// the tab bar for now). Everything else keeps its route.
const ROUTED_PAGES = {
  '/3d': Scene3D,
  '/connections': ConnectionsPage,
  '/gates': GatesPage,
  '/dashboard': DashboardPage,
};

function LoadingScreen() {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: '#030610',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#ffa040',
        fontFamily: 'monospace',
        gap: 16,
      }}
    >
      <div style={{ fontSize: 22, letterSpacing: 3, textTransform: 'uppercase' }}>
        Hamad International Airport
      </div>
      <div style={{ fontSize: 13, color: '#6070a0', letterSpacing: 2 }}>
        Loading 3D scene…
      </div>
    </div>
  );
}

export default function App() {
  useWebSocket();
  const pathname = window.location.pathname;
  const Page = ROUTED_PAGES[pathname] || Radar2DPage;   // `/` (and unknown) → 2D home
  const activeTab = ROUTED_PAGES[pathname] ? pathname : '/';

  return (
    <div style={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column' }}>
      <TopTabs active={activeTab} />
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <Suspense fallback={<LoadingScreen />}>
          <Page />
        </Suspense>
      </div>
    </div>
  );
}

import { Suspense, lazy } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import TopTabs from './components/TopTabs';
import DelayRiskAlerts from './components/DelayRiskAlerts';

const Scene3D = lazy(() => import('./components/Scene3D'));
const ConnectionsPage = lazy(() => import('./pages/ConnectionsPage'));
const GatesPage = lazy(() => import('./pages/GatesPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const Radar2DPage = lazy(() => import('./pages/Radar2DPage'));
const AboutPage = lazy(() => import('./pages/AboutPage'));
const AskGeniePage = lazy(() => import('./pages/AskGeniePage'));

// 2D Radar is now the HOME page (`/`); the 3D scene moved to `/3d` (hidden from
// the tab bar for now). Everything else keeps its route.
const ROUTED_PAGES = {
  '/3d': Scene3D,
  '/connections': ConnectionsPage,
  '/gates': GatesPage,
  '/dashboard': DashboardPage,
  '/genie': AskGeniePage,
  '/about': AboutPage,
  '/settings': AboutPage,   // old URL — kept so existing links/bookmarks still work
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
        Airport Operations
      </div>
      {/* role=status so a screen reader announces the loading state; #6070a0 was
          only ~3.2:1 against this backdrop, below AA for 13px text. */}
      <div role="status" style={{ fontSize: 13, color: '#94a3b8', letterSpacing: 2 }}>
        Loading…
      </div>
    </div>
  );
}

export default function App() {
  useWebSocket();
  const pathname = window.location.pathname;
  const Page = ROUTED_PAGES[pathname] || Radar2DPage;   // `/` (and unknown) → 2D home
  const activeTab = pathname === '/settings' ? '/about' : (ROUTED_PAGES[pathname] ? pathname : '/');

  return (
    <div style={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Skip link: lets keyboard users jump past the tab bar straight to content
          instead of tabbing through the nav on every page. Visually hidden until
          focused (see .skip-link). */}
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <TopTabs active={activeTab} />
      <DelayRiskAlerts />
      {/* <main> landmark — the app had no main region, so assistive tech had no
          way to identify where page content begins. */}
      <main id="main-content" style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <Suspense fallback={<LoadingScreen />}>
          <Page />
        </Suspense>
      </main>
    </div>
  );
}

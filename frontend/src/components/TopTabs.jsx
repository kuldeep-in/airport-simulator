// Home is now the 2D Radar map. The 3D scene is hidden from the bar (still
// reachable at /3d).
const TABS = [
  { href: '/', label: 'Home' },
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/connections', label: 'Schedule' },
  { href: '/gates', label: 'Gates' },
];

export default function TopTabs({ active }) {
  return (
    <div
      className="panel"
      style={{
        height: 40,
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        padding: '0 16px',
        borderRadius: 0,
        borderLeft: 'none',
        borderRight: 'none',
        borderTop: 'none',
      }}
    >
      {TABS.map((t) => {
        const isActive = t.href === active;
        return (
          <a
            key={t.href}
            href={t.href}
            style={{
              textDecoration: 'none',
              fontSize: 12,
              fontWeight: isActive ? 700 : 400,
              padding: '5px 12px',
              borderRadius: 6,
              color: isActive ? '#0a0e1a' : 'var(--text-muted)',
              background: isActive ? 'var(--accent-blue)' : 'transparent',
            }}
          >
            {t.label}
          </a>
        );
      })}
    </div>
  );
}

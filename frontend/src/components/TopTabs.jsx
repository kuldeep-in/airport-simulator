// Home is now the 2D Radar map. The 3D scene is hidden from the bar (still
// reachable at /3d).
const TABS = [
  { href: '/', label: 'Home' },
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/connections', label: 'Schedule' },
  { href: '/gates', label: 'Gates' },
  { href: '/genie', label: 'Ask Genie' },
  { href: '/about', label: 'About' },
];

export default function TopTabs({ active }) {
  return (
    // <nav> + aria-label makes this a real landmark screen readers can jump to;
    // it was a plain <div> with no role at all.
    <nav
      className="panel"
      aria-label="Main"
      style={{
        height: 44,           // was 40 — meets the 44px minimum touch target
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-sm)',
        padding: '0 var(--space-xl)',
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
            // aria-current is how assistive tech announces "you are here"; colour
            // and weight alone conveyed it visually only.
            aria-current={isActive ? 'page' : undefined}
            className="tab-link"
            data-active={isActive ? 'true' : undefined}
            style={{
              textDecoration: 'none',
              fontSize: 12,
              fontWeight: isActive ? 600 : 400,
              // Vertical padding grows the hit area to the full bar height rather
              // than a 22px sliver in the middle of it.
              padding: '0 var(--space-lg)',
              height: 30,
              display: 'inline-flex',
              alignItems: 'center',
              borderRadius: 'var(--radius-md)',
              color: isActive ? '#0a0e1a' : 'var(--text-muted)',
              background: isActive ? 'var(--accent-blue)' : 'transparent',
            }}
          >
            {t.label}
          </a>
        );
      })}
    </nav>
  );
}

import { useEffect, useRef, useState } from 'react';

// Measure a container's pixel width so charts can draw in real coordinates
// (avoids the horizontal-stretch distortion of viewBox + preserveAspectRatio
// "none", which mangled text labels). Returns [ref, width].
export function useWidth(fallback = 600) {
  const ref = useRef(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const width = Math.round(entries[0].contentRect.width);
      if (width > 0) setW((prev) => (prev === width ? prev : width));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

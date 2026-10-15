// Is the window narrower than the viewers' breakpoint (lib/viewerToolbar NARROW_PX)?  Initial value at mount, updated on resize.
import { useEffect, useState } from 'react';
import { isNarrow } from '../../lib/viewerToolbar';

export function useViewportWidth(): number {
  const [w, setW] = useState(() => (typeof window === 'undefined' ? 1400 : window.innerWidth));
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return w;
}

export function useNarrow(): boolean {
  return isNarrow(useViewportWidth());
}

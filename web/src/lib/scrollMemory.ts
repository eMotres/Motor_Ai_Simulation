// Remember where a scrolling panel was and put it back — user 2026-09-13:
// "можешь сделать запоминалку положения скроллинга страницы Motors, я
// постоянно её кручу".  The Motors tab is unmounted on every tab switch, so
// its scroll box comes back at the top each time.
//
// The position lives in localStorage (survives a reload too); the restore
// waits for the content to be tall enough — the catalog arrives over the
// network — and gives up as soon as the user scrolls by hand, so it never
// yanks the page from under them.
import { useEffect, type RefObject } from 'react';

const RESTORE_WINDOW_MS = 5000;

export function useScrollMemory(key: string, ref: RefObject<HTMLElement | null>,
                                target: 'self' | 'parent' = 'parent') {
  useEffect(() => {
    const own = ref.current;
    const el = (target === 'parent' ? own?.parentElement : own) as HTMLElement | null;
    if (!el) return;
    const K = 'scroll:' + key;
    let raf = 0;
    let programmatic = false;
    let userScrolled = false;
    const onScroll = () => {
      if (!programmatic) userScrolled = true;
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        try { localStorage.setItem(K, String(Math.round(el.scrollTop))); } catch { /* storage blocked */ }
      });
    };
    el.addEventListener('scroll', onScroll, { passive: true });

    let want = 0;
    try { want = Number(localStorage.getItem(K) || 0); } catch { want = 0; }
    let stopped = false;
    const t0 = Date.now();
    const tryRestore = () => {
      if (stopped || userScrolled) return;
      const room = el.scrollHeight - el.clientHeight;
      if (room >= want - 1) {
        programmatic = true;
        el.scrollTop = want;
        requestAnimationFrame(() => { programmatic = false; });
        stopped = true;
        return;
      }
      if (Date.now() - t0 < RESTORE_WINDOW_MS) { requestAnimationFrame(tryRestore); return; }
      programmatic = true;
      el.scrollTop = Math.max(0, room);            // as far down as the page goes today
      requestAnimationFrame(() => { programmatic = false; });
      stopped = true;
    };
    if (want > 0 && Number.isFinite(want)) tryRestore();

    return () => {
      stopped = true;
      el.removeEventListener('scroll', onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [key, ref, target]);
}

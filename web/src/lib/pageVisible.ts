/**
 * Poll gate: nothing is fetched while the page is hidden.
 *
 * Every tab of this app keeps a few pollers alive — solve progress (350 ms
 * while running, 1.5 s idle), the family context (5 s), the lock state (10 s),
 * the descent mirror (4 s).  Each answer is a fetch, a JSON parse and a React
 * commit, and a tab left open all day in the background paid for all of them
 * for hours (2026-09-13, after the second freeze of the day: ~16 000 requests
 * and ~80 000 console lines buffered in one tab).  A hidden page cannot show a
 * progress bar to anyone, so its pollers wait here and resume — with one
 * immediate read — the moment it is shown again.
 *
 * Nothing about the solve itself changes: the server keeps running, and a
 * result that landed while the page was hidden is picked up on the first tick
 * after it is visible.
 */

/** True unless the document is hidden (background tab, minimised window). */
export function pageVisible(): boolean {
  try { return document.visibilityState !== 'hidden'; } catch { return true; }
}

/** Resolves at once while the page is visible, else when it becomes visible. */
export function whenVisible(): Promise<void> {
  if (pageVisible()) return Promise.resolve();
  return new Promise<void>((resolve) => {
    const on = () => {
      if (!pageVisible()) return;
      document.removeEventListener('visibilitychange', on);
      resolve();
    };
    document.addEventListener('visibilitychange', on);
  });
}

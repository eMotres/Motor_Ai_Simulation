// Long per-item work (the outline of every mesh of a big STEP assembly: hundreds of bodies, millions of triangles) done in slices
// of a few milliseconds, so the model is shown and the page stays responsive while it runs (owner 2026-11-23: an uploaded board
// STEP kept the 3D tab on a spinner).

/** Run `work(item)` for every item, at most `sliceMs` per slice; the next slice waits for `defer`.  `alive()` false stops it
 *  (a new model was loaded).  `afterSlice` runs after each slice (repaint).  Returns a cancel function. */
export function runSliced<T>(items: readonly T[], work: (item: T) => void, opts: {
  sliceMs: number; alive: () => boolean; afterSlice?: () => void; now?: () => number; defer?: (fn: () => void) => unknown; cancelDefer?: (h: unknown) => void;
}): () => void {
  const now = opts.now ?? (() => performance.now());
  const defer = opts.defer ?? ((fn: () => void) => setTimeout(fn, 0));
  const cancelDefer = opts.cancelDefer ?? ((h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let i = 0;
  let handle: unknown = null;
  let stopped = false;
  const slice = () => {
    handle = null;
    if (stopped || !opts.alive()) return;
    const until = now() + opts.sliceMs;
    do { work(items[i]); i += 1; } while (i < items.length && now() < until);
    opts.afterSlice?.();
    if (i < items.length) handle = defer(slice);
  };
  if (items.length) slice();
  return () => { stopped = true; if (handle !== null) cancelDefer(handle); handle = null; };
}

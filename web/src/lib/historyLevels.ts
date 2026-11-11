/** The browser / phone Back button walks UP one level (mobile audit 2026-11, owner: «чтобы были видны и работали переходы из нижнего на
 *  верхний уровень»).
 *
 *  The app keeps its pages in memory on one address, so Back used to leave aerostator.com from any depth (a phone user inside a
 *  project's specification pressed Back and was on the previous site).  A page that opens a deeper level (a project from the list,
 *  a production from the list) calls `useHistoryLevel(open, close)`: while the level is open it owns one history entry, Back closes it
 *  (the page's own "up" action runs, exactly as its back arrow), and closing it from the page removes that entry again so Back never
 *  lands on a dead step.
 *
 *  The address bar is never changed here: an entry is pushed with the SAME url, and only `history.state.__lvl` (the depth marker)
 *  is ours.  Pages that rewrite the address with `replaceState` pass `window.history.state` through, so the marker survives.
 *
 *  Pure logic in `HistoryLevels` (node --test); the React hook and the window wiring below it. */
import { useEffect, useRef } from 'react';

export interface HistoryLike {
  readonly state: unknown;
  pushState(data: unknown, unused: string, url?: string | null): void;
  back(): void;
}

/** The depth marker of a history state (0 = not one of ours). */
export function levelOf(state: unknown): number {
  const v = state && typeof state === 'object' ? (state as { __lvl?: unknown }).__lvl : undefined;
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0;
}

/** The open levels and the history entries they own. */
export class HistoryLevels {
  private stack: Array<{ id: number; close: () => void }> = [];
  private nextId = 1;
  /** popstate events caused by our own `back()` (a level closed by the page), to be ignored */
  private skip = 0;

  private readonly history: HistoryLike;

  constructor(history: HistoryLike) { this.history = history; }

  get depth(): number { return this.stack.length; }

  /** Open a level: push one entry (same address) and remember how to close it.  Returns the release function. */
  open(close: () => void): () => void {
    const id = this.nextId++;
    this.stack.push({ id, close });
    const base = this.history.state && typeof this.history.state === 'object' ? this.history.state as Record<string, unknown> : {};
    try { this.history.pushState({ ...base, __lvl: id }, ''); } catch { /* no history API: the page's own controls still work */ }
    return () => this.release(id);
  }

  /** The level closed itself (its page's back arrow, a tab change): drop it and step the history back over its entry. */
  private release(id: number): void {
    const at = this.stack.findIndex((l) => l.id === id);
    if (at < 0) return;                       // already closed by Back
    const top = at === this.stack.length - 1;
    this.stack.splice(at, 1);
    if (top && levelOf(this.history.state) === id) {
      this.skip += 1;
      try { this.history.back(); } catch { this.skip -= 1; }
    }
  }

  /** popstate: close every level deeper than the entry we arrived at (normally the one on top). */
  onPop(state: unknown): void {
    if (this.skip > 0) { this.skip -= 1; return; }
    const here = levelOf(state);
    const closing = this.stack.filter((l) => l.id > here).reverse();
    this.stack = this.stack.filter((l) => l.id <= here);
    for (const l of closing) {
      try { l.close(); } catch { /* one page's failure must not keep the others open */ }
    }
  }
}

let shared: HistoryLevels | null = null;
function levels(): HistoryLevels | null {
  if (shared) return shared;
  if (typeof window === 'undefined' || !window.history) return null;
  shared = new HistoryLevels(window.history);
  window.addEventListener('popstate', (e) => shared?.onPop(e.state));
  return shared;
}

/** While `open` is true this level owns one history entry; the browser's Back runs `close`. */
export function useHistoryLevel(open: boolean, close: () => void): void {
  const closeRef = useRef(close);
  useEffect(() => { closeRef.current = close; });
  useEffect(() => {
    if (!open) return;
    const lv = levels();
    if (!lv) return;
    return lv.open(() => closeRef.current());
  }, [open]);
}

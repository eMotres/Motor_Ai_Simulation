/**
 * In-page flight recorder for the "the web froze" reports.
 *
 * 2026-09-13: twice in one day the user's tab stopped executing JS and
 * painting; the API had answered in 0.2 s throughout, the console showed two
 * "THREE.WebGLRenderer: Context Lost" and nothing else, and there was no way
 * to tell afterwards whether the heap had grown, the main thread had been
 * stalling for minutes before, or the GPU had simply gone.  This module writes
 * a small ring buffer to localStorage — survives the freeze and the reload
 * that ends it — so the NEXT one comes with data:
 *
 *   heap   every 60 s: used / total / limit JS heap (Chrome only)
 *   stall  a 1 s timer that fired more than 3 s late — a long task or a hang
 *          the page recovered from, with how late
 *   note   anything a component wants on the record (WebGL context lost /
 *          restored, an uncaught error, an unhandled rejection)
 *
 * Read it from the console with `__diag()` (newest last) or through the
 * `diag.log` key.  Cost: one JSON write a minute, ~40 KB at the cap.
 */

export interface DiagEntry {
  t: string;
  kind: 'heap' | 'stall' | 'note';
  tab?: string;
  used_mb?: number;
  total_mb?: number;
  limit_mb?: number;
  stall_ms?: number;
  note?: string;
}

const KEY = 'diag.log';
const MAX = 400;

function read(): DiagEntry[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(v) ? v : [];
  } catch { return []; }
}

function activeTab(): string {
  try {
    const el = document.querySelector('[role="tab"][aria-selected="true"]');
    return (el?.textContent || '').trim().slice(0, 24);
  } catch { return ''; }
}

function push(e: Omit<DiagEntry, 't' | 'tab'>): void {
  try {
    const log = read();
    log.push({ t: new Date().toISOString().slice(0, 19), tab: activeTab(), ...e });
    while (log.length > MAX) log.shift();
    localStorage.setItem(KEY, JSON.stringify(log));
  } catch { /* private mode or quota — the recorder is best-effort */ }
}

/** Put one line on the record ("webgl context lost: geometry viewer"). */
export function diagNote(note: string): void {
  push({ kind: 'note', note: String(note).slice(0, 200) });
}

let installed = false;

/** Start the recorder — once per page; safe to call again. */
export function installDiag(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  diagNote('page loaded');
  // Heap, once a minute.  `performance.memory` is Chromium-only; elsewhere the
  // heap rows are simply absent.
  window.setInterval(() => {
    const m = (performance as unknown as { memory?: {
      usedJSHeapSize: number; totalJSHeapSize: number; jsHeapSizeLimit: number } }).memory;
    if (!m) return;
    push({ kind: 'heap',
           used_mb: Math.round(m.usedJSHeapSize / 1048576),
           total_mb: Math.round(m.totalJSHeapSize / 1048576),
           limit_mb: Math.round(m.jsHeapSizeLimit / 1048576) });
  }, 60_000);
  // Stalls: a 1 s timer that comes back more than 3 s late means the main
  // thread was busy (or the page was frozen) for that long.  A background
  // tab is throttled to ~1 Hz by the browser, so hidden time is not counted.
  let last = performance.now();
  window.setInterval(() => {
    const now = performance.now();
    const late = now - last - 1000;
    last = now;
    if (late > 3000 && document.visibilityState !== 'hidden') {
      push({ kind: 'stall', stall_ms: Math.round(late) });
    }
  }, 1000);
  window.addEventListener('error', (e) => diagNote('error: ' + (e.message || 'unknown')));
  window.addEventListener('unhandledrejection', (e) => {
    const r = (e as PromiseRejectionEvent).reason;
    diagNote('unhandled rejection: ' + String(r && (r.message || r)).slice(0, 160));
  });
  (window as unknown as { __diag?: () => DiagEntry[] }).__diag = read;
}

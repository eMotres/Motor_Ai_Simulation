// Working-time heartbeat (owner 2026-10-15: "how much time does each user work with the system").
//
// While a SIGNED-IN user has the tab visible AND touched it (pointer / key / scroll) in the last
// 5 minutes, send POST /api/activity/heartbeat every 60 s with { tab, sid, ts }.  Stop when the
// tab is hidden or the user has been idle for more than 5 minutes; send one last beat when the
// tab is hidden or the page is going away.  The payload is the tab id and a random per-page-load
// session id: no die, configuration, e-mail or any content.  The server counts the time with its
// own clock (activity.py); ts is informational only.
//
// ONE beat per 60 s per tab, however the page behaves (incident 2026-10-15: hundreds of beats in a
// row).  The scheduler used to beat at once on EVERY start of a stretch - a touch after idle, a
// visibility flip back to visible, a component re-mount (React StrictMode, a sign-in state change)
// - and every re-mount also minted a new session id, which slipped past the server's per-session
// cap.  Now (a) the time of the last beat and the session id live for the whole page load (`store`),
// so a restart never beats early: it waits for what is left of the 60 s; (b) the only extra beat is
// the final one when the tab hides / the page goes away, at most once per minute and only
// while a stretch was running; (c) at most one scheduler runs per page (startActivityBeat).
//
// The decision logic is pure (injected clock, timers and transport) so
// web/src/lib/__tests__/activityBeat.test.mjs runs it under node --test.

export const BEAT_INTERVAL_MS = 60_000;
export const IDLE_LIMIT_MS = 5 * 60_000;
/** The final beat (tab hidden / page going away) is sent only if the last beat is at least this old. */
export const FINAL_MIN_GAP_MS = 15_000;

/** Lives as long as the PAGE (module scope in the browser), not as long as one scheduler instance. */
export interface BeatStore { lastSentAt: number; lastFinalAt?: number }

export interface BeatBody { tab: string; sid: string; ts: number }

export interface BeatDeps {
  now: () => number;
  visible: () => boolean;
  /** `unload` = the page is hiding / going away: use a transport that survives it. */
  send: (body: BeatBody, unload: boolean) => void;
  getTab: () => string;
  sid: string;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (h: unknown) => void;
  /** Survives re-creating the scheduler (remount); default = a fresh one per scheduler. */
  store?: BeatStore;
}

export function createActivityBeat(d: BeatDeps) {
  const store: BeatStore = d.store ?? { lastSentAt: -Infinity };
  let lastTouch = d.now();          // opening the page counts as an interaction
  let timer: unknown = null;
  let stopped = false;

  const active = () => !stopped && d.visible() && d.now() - lastTouch <= IDLE_LIMIT_MS;
  const beat = (unload = false) => {
    store.lastSentAt = d.now();
    d.send({ tab: d.getTab(), sid: d.sid, ts: d.now() }, unload);
  };

  function halt() {
    if (timer !== null) { d.clearTimer(timer); timer = null; }
  }

  function tick() {
    timer = null;
    if (!active()) return;          // idle or hidden: stop, a later touch / visibility change restarts
    beat();
    timer = d.setTimer(tick, BEAT_INTERVAL_MS);
  }

  function evaluate() {
    if (timer !== null || !active()) return;
    // Never closer than 60 s to the previous beat (a restart of the stretch waits out the rest).
    const wait = BEAT_INTERVAL_MS - (d.now() - store.lastSentAt);
    if (wait > 0) { timer = d.setTimer(tick, wait); return; }
    beat();                         // the first beat of a stretch credits nothing server-side
    timer = d.setTimer(tick, BEAT_INTERVAL_MS);
  }

  /** The single final beat of a stretch (tab hidden / page going away). */
  function finalBeat() {
    if (timer === null) return;
    halt();
    // worth sending only if it can credit something (>= 15 s since the last beat), and at most one
    // final beat per minute however often the tab flips hidden / visible
    if (d.now() - store.lastSentAt >= FINAL_MIN_GAP_MS
        && d.now() - (store.lastFinalAt ?? -Infinity) >= BEAT_INTERVAL_MS) {
      store.lastFinalAt = d.now();
      beat(true);
    }
  }

  return {
    /** Call on pointerdown / keydown / wheel / scroll / pointermove (cheap: one assignment). */
    touch() { lastTouch = d.now(); if (timer === null) evaluate(); },
    /** visibilitychange */
    onVisibility() {
      if (!d.visible()) { finalBeat(); return; }
      evaluate();
    },
    /** pagehide / beforeunload */
    onPageHide() { finalBeat(); },
    start() { stopped = false; evaluate(); },
    stop() { stopped = true; halt(); },
    isRunning: () => timer !== null,
  };
}

/** Random per-page-load session id: [A-Za-z0-9_-]{8,64}, the shape the server accepts. */
export function newSessionId(rand: () => number = Math.random): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  try { if (c?.randomUUID) return c.randomUUID(); } catch { /* fall through */ }
  let s = '';
  for (let i = 0; i < 24; i++) s += 'abcdefghijklmnopqrstuvwxyz0123456789'[Math.floor(rand() * 36)];
  return s;
}

/**
 * Browser wiring: returns a stop function.  `getToken` is read synchronously at send time so the
 * last beat works while the page is unloading (sendBeacon cannot set the Authorization header the
 * API needs, so a keepalive fetch carries it instead; it survives the page just as well).
 */
let pageSid: string | null = null;                 // one session id per page load
const pageStore: BeatStore = { lastSentAt: -Infinity };
let stopCurrent: (() => void) | null = null;       // at most ONE scheduler per page

export function startActivityBeat(opts: {
  apiBase: string; getTab: () => string; getToken: () => string | null;
}): () => void {
  if (stopCurrent) stopCurrent();                  // a re-mount replaces the old scheduler, never adds one
  const sid = (pageSid ??= newSessionId());
  const url = `${opts.apiBase.replace(/\/$/, '')}/api/activity/heartbeat`;
  const send = (body: BeatBody) => {
    try {
      const token = opts.getToken();
      if (!token) return;
      void fetch(url, {
        method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(body),
      }).catch(() => { /* a lost beat is a lost minute, never an error */ });
    } catch { /* ignore */ }
  };
  const hb = createActivityBeat({
    now: () => Date.now(),
    visible: () => document.visibilityState === 'visible',
    send,
    getTab: opts.getTab,
    sid,
    setTimer: (fn, ms) => window.setTimeout(fn, ms),
    clearTimer: (h) => window.clearTimeout(h as number),
    store: pageStore,
  });
  const touch = () => hb.touch();
  const onVis = () => hb.onVisibility();
  const onHide = () => hb.onPageHide();
  const evs = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'scroll', 'touchstart'];
  for (const e of evs) window.addEventListener(e, touch, { passive: true, capture: true });
  document.addEventListener('visibilitychange', onVis);
  window.addEventListener('pagehide', onHide);
  hb.start();
  const stop = () => {
    if (stopCurrent === stop) stopCurrent = null;
    for (const e of evs) window.removeEventListener(e, touch, true);
    document.removeEventListener('visibilitychange', onVis);
    window.removeEventListener('pagehide', onHide);
    hb.stop();
  };
  stopCurrent = stop;
  return stop;
}

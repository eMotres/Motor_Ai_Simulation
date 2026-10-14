// Working-time heartbeat (owner 2026-10-15: "how much time does each user work with the system").
//
// While a SIGNED-IN user has the tab visible AND touched it (pointer / key / scroll) in the last
// 5 minutes, send POST /api/activity/heartbeat every 60 s with { tab, sid, ts }.  Stop when the
// tab is hidden or the user has been idle for more than 5 minutes; send one last beat when the
// tab is hidden or the page is going away.  The payload is the tab id and a random per-page-load
// session id: no die, configuration, e-mail or any content.  The server counts the time with its
// own clock (activity.py); ts is informational only.
//
// The decision logic is pure (injected clock, timers and transport) so
// web/src/lib/__tests__/activityBeat.test.mjs runs it under node --test.

export const BEAT_INTERVAL_MS = 60_000;
export const IDLE_LIMIT_MS = 5 * 60_000;

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
}

export function createActivityBeat(d: BeatDeps) {
  let lastTouch = d.now();          // opening the page counts as an interaction
  let timer: unknown = null;
  let stopped = false;

  const active = () => !stopped && d.visible() && d.now() - lastTouch <= IDLE_LIMIT_MS;
  const beat = (unload = false) => d.send({ tab: d.getTab(), sid: d.sid, ts: d.now() }, unload);

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
    beat();                         // the first beat of a stretch credits nothing server-side
    timer = d.setTimer(tick, BEAT_INTERVAL_MS);
  }

  return {
    /** Call on pointerdown / keydown / wheel / scroll / pointermove (cheap: one assignment). */
    touch() { lastTouch = d.now(); if (timer === null) evaluate(); },
    /** visibilitychange */
    onVisibility() {
      if (!d.visible()) { if (timer !== null) { halt(); beat(true); } return; }
      evaluate();
    },
    /** pagehide / beforeunload */
    onPageHide() { if (timer !== null) { halt(); beat(true); } },
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
export function startActivityBeat(opts: {
  apiBase: string; getTab: () => string; getToken: () => string | null;
}): () => void {
  const sid = newSessionId();
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
  });
  const touch = () => hb.touch();
  const onVis = () => hb.onVisibility();
  const onHide = () => hb.onPageHide();
  const evs = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'scroll', 'touchstart'];
  for (const e of evs) window.addEventListener(e, touch, { passive: true, capture: true });
  document.addEventListener('visibilitychange', onVis);
  window.addEventListener('pagehide', onHide);
  hb.start();
  return () => {
    for (const e of evs) window.removeEventListener(e, touch, true);
    document.removeEventListener('visibilitychange', onVis);
    window.removeEventListener('pagehide', onHide);
    hb.stop();
  };
}

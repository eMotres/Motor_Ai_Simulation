// Session "I am here" heartbeat (owner 2026-10-20: a web session closes after 30 minutes without use).
//
// The server closes a session only when there was no user activity AND no computation of the account for
// 30 minutes (sessions.py).  Background polls never count, so this is how the app says "a person is here":
// POST /api/auth/heartbeat, at most ONCE A MINUTE, and only when the tab is VISIBLE and there was real input
// (pointer, key, wheel, touch) since the previous call.  No payload: no tab, no content.
//
// The decision logic is pure (injected clock, timers and transport) so
// web/src/lib/__tests__/sessionHeartbeat.test.mjs runs it under node --test.

export const SESSION_BEAT_MIN_GAP_MS = 60_000;

export interface SessionBeatDeps {
  now: () => number;
  visible: () => boolean;
  send: () => void;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (h: unknown) => void;
}

export function createSessionHeartbeat(d: SessionBeatDeps) {
  let lastSentAt = -Infinity;
  let pending = false;              // input seen since the last call
  let timer: unknown = null;

  function fire() {
    timer = null;
    if (!pending || !d.visible()) return;
    pending = false;
    lastSentAt = d.now();
    d.send();
  }

  function arm() {
    if (timer !== null || !pending) return;
    const wait = Math.max(0, SESSION_BEAT_MIN_GAP_MS - (d.now() - lastSentAt));
    timer = d.setTimer(fire, wait);
  }

  return {
    /** Real input (pointerdown / pointermove / keydown / wheel / touchstart).  Cheap: a flag and, at most, one timer. */
    touch() {
      if (!d.visible()) return;
      pending = true;
      arm();
    },
    /** visibilitychange: a hidden tab sends nothing; input made before hiding is not carried over. */
    onVisibility() {
      if (!d.visible()) {
        pending = false;
        if (timer !== null) { d.clearTimer(timer); timer = null; }
      }
    },
    stop() {
      pending = false;
      if (timer !== null) { d.clearTimer(timer); timer = null; }
    },
  };
}

/** Browser wiring: returns a stop function.  `getToken` is read at send time. */
let stopCurrent: (() => void) | null = null;       // at most ONE scheduler per page

export function startSessionHeartbeat(opts: { apiBase: string; getToken: () => string | null }): () => void {
  if (stopCurrent) stopCurrent();
  const url = `${opts.apiBase.replace(/\/$/, '')}/api/auth/heartbeat`;
  const send = () => {
    try {
      const token = opts.getToken();
      if (!token) return;
      // A refused beat (401 idle_timeout) is announced by the fetch interceptor (lib/apiAuth.ts) from the
      // X-Auth-Reason header; nothing to do here.
      void fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${token}` } })
        .catch(() => { /* a lost beat is retried by the next input */ });
    } catch { /* ignore */ }
  };
  const hb = createSessionHeartbeat({
    now: () => Date.now(),
    visible: () => document.visibilityState === 'visible',
    send,
    setTimer: (fn, ms) => window.setTimeout(fn, ms),
    clearTimer: (h) => window.clearTimeout(h as number),
  });
  const touch = () => hb.touch();
  const onVis = () => hb.onVisibility();
  const evs = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'];
  for (const e of evs) window.addEventListener(e, touch, { passive: true, capture: true });
  document.addEventListener('visibilitychange', onVis);
  const stop = () => {
    if (stopCurrent === stop) stopCurrent = null;
    for (const e of evs) window.removeEventListener(e, touch, true);
    document.removeEventListener('visibilitychange', onVis);
    hb.stop();
  };
  stopCurrent = stop;
  return stop;
}

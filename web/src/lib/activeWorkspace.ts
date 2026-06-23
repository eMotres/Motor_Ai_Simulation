// Per-user ACTIVE workspace (multi-user, P3).
//
// The signed-in user's live geometry, persisted to Firestore at
// users/{uid}/workspace/active. Loaded on sign-in (so the user resumes THEIR
// design instead of whatever is in the shared global backend config) and
// debounce-saved on every geometry edit. Anonymous users are not persisted
// (local-only). Reuses the same users/{uid}/** ownership rules as MyDesigns.
// See docs/MULTI_USER_PLAN.md.
import { doc, getDoc, setDoc, serverTimestamp } from 'firebase/firestore';
import { db } from './firebase';
import { useMotorStore } from '../stores/motorStore';

const activeRef = (uid: string) => doc(db!, 'users', uid, 'workspace', 'active');

export interface ActiveWorkspace {
  geometry: Record<string, number>;
}

function numericGeometry(g: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (g && typeof g === 'object') {
    for (const [k, v] of Object.entries(g as Record<string, unknown>)) {
      if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
    }
  }
  return out;
}

/** Read this user's saved active workspace, or null if none / unavailable. */
export async function loadActiveWorkspace(uid: string): Promise<ActiveWorkspace | null> {
  if (!db || !uid) return null;
  try {
    const snap = await getDoc(activeRef(uid));
    if (!snap.exists()) return null;
    const data = snap.data() as { geometry?: Record<string, number> };
    const geo = numericGeometry(data?.geometry);
    return Object.keys(geo).length ? { geometry: geo } : null;
  } catch {
    return null;          // offline / rules / no Firestore — non-fatal
  }
}

/** Persist this user's active geometry (merge). No-op for anon / empty geometry. */
export async function saveActiveWorkspace(uid: string, geometry: Record<string, number>): Promise<void> {
  if (!db || !uid) return;
  const geo = numericGeometry(geometry);
  if (!Object.keys(geo).length) return;
  try {
    await setDoc(activeRef(uid), { geometry: geo, updatedAt: serverTimestamp() }, { merge: true });
  } catch {
    /* offline / rules — non-fatal, the local store is still the source of truth */
  }
}

/** Apply a loaded workspace to backend + store so the user resumes their design. */
export async function applyActiveWorkspace(ws: ActiveWorkspace): Promise<void> {
  const st = useMotorStore.getState() as unknown as {
    updateGeometryViaApi?: (p: Record<string, number>) => Promise<void>;
  };
  if (st.updateGeometryViaApi) await st.updateGeometryViaApi(ws.geometry);
}

// ── Debounced auto-save ──────────────────────────────────────────────────────
let _uid: string | null = null;
let _unsub: (() => void) | null = null;
let _timer: ReturnType<typeof setTimeout> | null = null;
let _last = '';

/** Begin debounced auto-save of the store geometry to this user's active doc. */
export function startActiveWorkspaceSync(uid: string): void {
  stopActiveWorkspaceSync();
  _uid = uid;
  // Seed _last with the current geometry so applying the just-loaded design
  // doesn't immediately echo back a redundant write.
  _last = JSON.stringify(numericGeometry(useMotorStore.getState().geometry));
  _unsub = useMotorStore.subscribe((state) => {
    const geo = numericGeometry((state as { geometry?: unknown }).geometry);
    if (!Object.keys(geo).length) return;
    const json = JSON.stringify(geo);
    if (json === _last) return;          // no real change
    _last = json;
    if (_timer) clearTimeout(_timer);
    _timer = setTimeout(() => { if (_uid) void saveActiveWorkspace(_uid, geo); }, 1500);
  });
}

export function stopActiveWorkspaceSync(): void {
  if (_unsub) { _unsub(); _unsub = null; }
  if (_timer) { clearTimeout(_timer); _timer = null; }
  _uid = null;
  _last = '';
}

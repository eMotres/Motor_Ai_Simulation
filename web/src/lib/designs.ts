// User-owned saved motor designs in Firestore: users/{uid}/designs/{id}.
// A design captures the current geometry + operating point so it can be reloaded
// later. Apply pushes it back to the backend (same path the catalog Load uses).
import {
  collection, doc, getDocs, setDoc, deleteDoc, query, orderBy, serverTimestamp,
} from 'firebase/firestore';
import { db } from './firebase';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export interface SavedDesign {
  id: string;
  name: string;
  geometry: Record<string, number>;
  simulation: Record<string, number>;
  metrics?: { T_avg_Nm?: number; ripple_pct?: number };
  createdAt?: unknown;
}

const col = (uid: string) => collection(db!, 'users', uid, 'designs');

/** Snapshot the CURRENT backend geometry + operating point and store it. */
export async function saveCurrentDesign(uid: string, name: string): Promise<void> {
  const geoAll = await fetch(`${API}/api/geometry`).then((r) => r.json());
  const sim = await fetch(`${API}/api/simulation/config`).then((r) => r.json()).catch(() => ({}));
  // keep only plain numeric geometry params (drop derived / nested)
  const geometry: Record<string, number> = {};
  for (const [k, v] of Object.entries(geoAll)) if (typeof v === 'number') geometry[k] = v;
  const simulation: Record<string, number> = {};
  for (const k of ['max_current', 'rpm', 'phase_offset_deg']) {
    if (typeof sim[k] === 'number') simulation[k] = sim[k];
  }
  const id = `d_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  await setDoc(doc(col(uid), id), { name, geometry, simulation, createdAt: serverTimestamp() });
}

export async function listDesigns(uid: string): Promise<SavedDesign[]> {
  const snap = await getDocs(query(col(uid), orderBy('createdAt', 'desc')));
  return snap.docs.map((d) => ({ id: d.id, ...(d.data() as Omit<SavedDesign, 'id'>) }));
}

export async function deleteDesign(uid: string, id: string): Promise<void> {
  await deleteDoc(doc(col(uid), id));
}

/** Push a saved design back to the backend (geometry + load angle). */
export async function applyDesign(design: SavedDesign): Promise<void> {
  await fetch(`${API}/api/geometry`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(design.geometry),
  });
  const gamma = design.simulation?.phase_offset_deg;
  if (gamma != null) {
    await fetch(`${API}/api/simulation/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phase_offset_deg: gamma }),
    });
  }
}

// Material write actions for the three-layer library.
//
//   • mine   → the signed-in user's personal materials, client Firestore
//              (users/{uid}/materials). Any signed-in user; owner-only by rules.
//   • global → the shared admin-managed library, backend API (Admin SDK writes
//              Firestore materials_global). The global fetch interceptor
//              (apiAuth) attaches the Firebase token, so these are auto-authed;
//              the backend enforces admin via require_admin.
//
// Built-in materials are read-only; an admin "edit" of a built-in creates a
// global override (same name), and "delete" hides it.
import { doc, setDoc, deleteDoc, serverTimestamp } from 'firebase/firestore';
import { db } from './firebase';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

export type Cat = 'steel' | 'magnet' | 'conductor' | 'insulator' | 'coolant';

/** Drop the merge-added provenance tags so we never persist them. */
export function stripMeta(d: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(d || {})) {
    if (k === '_source' || k === '_editable' || k === '_docId') continue;
    out[k] = v;
  }
  return out;
}

const docId = (category: Cat, name: string) => `${category}__${name}`;

/** Sensible default props for a brand-new material of each category. */
export function blankMaterial(category: Cat): Record<string, unknown> {
  switch (category) {
    case 'steel':
      return { description: '', form: 'laminated', sigma: 0, density: 7650, stacking_factor: 0.97,
        core_loss_model: 'bertotti', core_loss_kh: 0, core_loss_kc: 0, core_loss_ke: 0,
        core_loss_curve_unit: 'w_per_kg', bh_curve: [] };
    case 'magnet':
      return { description: '', Br: 1.2, Hc: 900000, mu_rec: 1.05, sigma: 0, density: 7500,
        energy_product_kj_m3: 270, bh_curve: [] };
    case 'conductor':
      return { description: '', sigma: 58e6, resistivity: 1 / 58e6, density: 8960,
        thermal_conductivity: 400, specific_heat: 385, thermal_alpha: 0.00393,
        wire_width_mm: null, wire_height_mm: null };
    case 'insulator':
      return { description: '', sigma: 0, density: 1400, thermal_conductivity: 0.2,
        specific_heat: 1000, mu_r: 1 };
    case 'coolant':
      return { description: '', phase: 'liquid', density: 1000, specific_heat: 4186,
        thermal_conductivity: 0.6, kinematic_viscosity: 1e-6, prandtl: 7, sigma: 0 };
  }
}

// ── Per-user (client Firestore) ──────────────────────────────────────────────

export async function saveMine(uid: string, category: Cat, name: string, props: Record<string, unknown>): Promise<void> {
  if (!db) throw new Error('Firestore unavailable');
  await setDoc(
    doc(db, 'users', uid, 'materials', docId(category, name)),
    { ...stripMeta(props), category, name, updatedAt: serverTimestamp() },
    { merge: true },
  );
}

export async function deleteMine(uid: string, category: Cat, name: string): Promise<void> {
  if (!db) throw new Error('Firestore unavailable');
  await deleteDoc(doc(db, 'users', uid, 'materials', docId(category, name)));
}

/** Copy any material into the user's personal library under a new name. */
export async function copyToMine(uid: string, category: Cat, srcName: string, props: Record<string, unknown>): Promise<string> {
  const name = `${srcName} copy`;
  await saveMine(uid, category, name, props);
  return name;
}

// ── Global (admin, backend API — auto-authed by the fetch interceptor) ────────

export async function saveGlobal(category: Cat, name: string, props: Record<string, unknown>): Promise<void> {
  const r = await fetch(`${API}/api/materials/global`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category, name, props: stripMeta(props) }),
  });
  if (!r.ok) throw new Error(`Save failed (HTTP ${r.status})`);
}

export async function deleteGlobal(category: Cat, name: string): Promise<void> {
  const r = await fetch(
    `${API}/api/materials/global/${encodeURIComponent(category)}/${encodeURIComponent(name)}`,
    { method: 'DELETE' },
  );
  if (!r.ok) throw new Error(`Delete failed (HTTP ${r.status})`);
}

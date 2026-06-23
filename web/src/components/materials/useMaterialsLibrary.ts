/**
 * Hook: the materials library shown across the app.
 *
 * Three layers, merged here:
 *   • built-in  — materials_library.yaml (read-only defaults)        ┐ from the
 *   • global    — admin-managed shared layer (Firestore)             ┘ backend API
 *   • mine      — the signed-in user's personal materials            ← client Firestore
 *                 (users/{uid}/materials), copied/edited by the user
 *
 * Every entry is tagged `_source` ('builtin'|'global'|'mine') and `_editable`
 * (mine always; global only for admins — enforced server-side). `mine` overrides
 * by name, so a personal copy shadows the shared one in the tree.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { collection, getDocs } from 'firebase/firestore';
import { db } from '../../lib/firebase';
import { useAuth } from '../../contexts/AuthContext';

// ─── Types (mirror Python dataclasses) ───────────────────────────────────────

export interface BHPoint { 0: number; 1: number }   // [H or B, B or P]

export type MaterialSource = 'builtin' | 'global' | 'mine';

/** Provenance tags added by the merge (not physical properties). */
export interface MaterialMeta {
  _source?: MaterialSource;
  _editable?: boolean;
  _docId?: string;          // Firestore doc id for a 'mine' material
}

export interface SteelData extends MaterialMeta {
  description: string;
  form: string;
  sigma: number;
  density: number;
  stacking_factor: number;
  core_loss_model: string;
  core_loss_kh: number;
  core_loss_kc: number;
  core_loss_ke: number;
  core_loss_curve_unit: string;
  bh_curve: [number, number][];
  core_loss_curves?: Record<string, [number, number][]>;
}

export interface MagnetData extends MaterialMeta {
  description: string;
  Br: number;
  Hc: number;
  mu_rec: number;
  sigma: number;
  density: number;
  energy_product_kj_m3: number;
  bh_curve: [number, number][];
}

export interface ConductorData extends MaterialMeta {
  description: string;
  sigma: number;
  resistivity: number;
  density: number;
  thermal_conductivity: number | null;
  specific_heat: number | null;
  thermal_alpha: number | null;
  wire_width_mm: number | null;
  wire_height_mm: number | null;
}

export interface InsulatorData extends MaterialMeta {
  description: string;
  sigma: number;
  density: number;
  thermal_conductivity: number | null;
  specific_heat: number | null;
  mu_r: number;
}

export interface CoolantData extends MaterialMeta {
  description: string;
  phase: string;                 // 'liquid' | 'gas'
  density: number;
  specific_heat: number;
  thermal_conductivity: number;
  kinematic_viscosity: number;
  prandtl: number;
  sigma: number;
}

export interface MaterialsLibrary {
  steel: Record<string, SteelData>;
  magnet: Record<string, MagnetData>;
  conductor: Record<string, ConductorData>;
  insulator: Record<string, InsulatorData>;
  coolant: Record<string, CoolantData>;
}

export type MaterialCategory = 'steel' | 'magnet' | 'conductor' | 'insulator' | 'coolant';

export const MATERIAL_CATEGORIES: MaterialCategory[] =
  ['steel', 'magnet', 'conductor', 'insulator', 'coolant'];

export interface SelectedMaterial {
  category: MaterialCategory;
  name: string;
}

type MineLayer = Partial<Record<MaterialCategory, Record<string, Record<string, unknown>>>>;

// ─── Per-user layer (client Firestore) ───────────────────────────────────────

/** Read users/{uid}/materials → {category: {name: props (tagged 'mine')}}. */
async function readMine(uid: string): Promise<MineLayer> {
  const out: MineLayer = {};
  if (!db) return out;
  const snap = await getDocs(collection(db, 'users', uid, 'materials'));
  snap.forEach(d => {
    const data = d.data() as Record<string, unknown>;
    const cat = data.category as MaterialCategory | undefined;
    const name = data.name as string | undefined;
    if (!cat || !name || !MATERIAL_CATEGORIES.includes(cat)) return;
    const { category: _c, name: _n, ...props } = data;
    (out[cat] ??= {})[name] = { ...props, _source: 'mine', _editable: true, _docId: d.id };
  });
  return out;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useMaterialsLibrary() {
  const { user } = useAuth();
  const uid = user?.uid ?? null;

  const [base, setBase] = useState<MaterialsLibrary | null>(null);  // built-in + global
  const [mine, setMine] = useState<MineLayer>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // built-in + global (from the backend, already merged + tagged there)
  const reloadBase = useCallback(() => {
    setLoading(true);
    fetch((import.meta.env.VITE_API_URL ?? 'http://localhost:8000') + '/api/materials/library', { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => { setBase(data); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, []);

  useEffect(() => { reloadBase(); }, [reloadBase]);

  // per-user "my materials" (client Firestore) — reloadable after copy/edit/delete
  const reloadMine = useCallback(async () => {
    if (!db || !uid) { setMine({}); return; }
    try { setMine(await readMine(uid)); } catch { setMine({}); }
  }, [uid]);

  useEffect(() => { void reloadMine(); }, [reloadMine]);

  /** Refetch both layers (after an edit/add/delete to global or mine). */
  const reload = useCallback(() => { reloadBase(); void reloadMine(); }, [reloadBase, reloadMine]);

  const library = useMemo<MaterialsLibrary | null>(() => {
    if (!base) return null;
    const out = {} as MaterialsLibrary;
    for (const cat of MATERIAL_CATEGORIES) {
      const merged: Record<string, Record<string, unknown>> = {};
      for (const [n, p] of Object.entries(base[cat] ?? {})) merged[n] = p as Record<string, unknown>;
      for (const [n, p] of Object.entries(mine[cat] ?? {})) merged[n] = p;   // mine shadows/adds
      (out as Record<string, unknown>)[cat] = merged;
    }
    return out;
  }, [base, mine]);

  return { library, loading, error, reload, reloadMine, uid };
}

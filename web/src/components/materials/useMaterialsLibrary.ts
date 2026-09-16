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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { collection, getDocs } from 'firebase/firestore';
import { db } from '../../lib/firebase';
import { useAuth, useApiReady } from '../../contexts/AuthContext';

// ─── Types (mirror Python dataclasses) ───────────────────────────────────────

export interface BHPoint { 0: number; 1: number }   // [H or B, B or P]

export type MaterialSource = 'builtin' | 'global' | 'mine';

/** Provenance tags added by the merge (not physical properties). */
export interface MaterialMeta {
  _source?: MaterialSource;
  _editable?: boolean;
  _docId?: string;          // Firestore doc id for a 'mine' material
}

/** The MECHANICAL half of a card — Young's modulus, Poisson, the strengths and
 *  the expansion coefficients.  The library has carried these since the rotor
 *  stress solver was written (2026-09-05) and the API returns them; the detail
 *  card printed only the thermal and electrical half, so the numbers that size
 *  a retaining band were invisible in the app that computes with them (user
 *  2026-09-09: "где, кстати, механические свойства материалов?").
 *
 *  Orthotropic parts (a hoop-wound CFRP band) carry axis 1 = the fibre/hoop
 *  direction and axis 2 = across it. */
export interface MechanicalProps {
  youngs_modulus_gpa?: number | null;
  youngs_modulus_transverse_gpa?: number | null;
  shear_modulus_gpa?: number | null;
  poisson_ratio?: number | null;
  tensile_strength_mpa?: number | null;
  compressive_strength_mpa?: number | null;
  yield_strength_mpa?: number | null;
  cte_ppm_k_1?: number | null;
  cte_ppm_k_2?: number | null;
  cte_ppm_k?: number | null;
  max_service_temp_c?: number | null;
}

export interface SteelData extends MaterialMeta, MechanicalProps {
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

export interface MagnetData extends MaterialMeta, MechanicalProps {
  description: string;
  Br: number;
  Hc: number;
  mu_rec: number;
  sigma: number;
  density: number;
  energy_product_kj_m3: number;
  bh_curve: [number, number][];
}

export interface ConductorData extends MaterialMeta, MechanicalProps {
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

export interface InsulatorData extends MaterialMeta, MechanicalProps {
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

// ─── Retry policy (pure — covered by __tests__/materialsLibraryRetry) ────────

/** How many times a transient failure is asked again before the tab gives up
 *  and says so.  Six, with the delays below, is ~45 s of patience. */
export const MAX_LIBRARY_RETRIES = 6;

/** Is this failure worth asking again?
 *
 *  `0` is "the server never answered" — offline, DNS, a container mid-restart —
 *  and 5xx / 408 / 429 are the server saying "not now".  Everything else is a
 *  REFUSAL: 401 (not signed in), 403 (this tier does not open the library),
 *  404 (no such route on this backend) are final answers, and asking six more
 *  times only spends the visitor's battery.  Not signed in is handled by the
 *  gate below, which asks again the moment there IS a session. */
export function isRetriableStatus(status: number): boolean {
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

/** 1 s, 2 s, 4 s, 8 s, 15 s, 15 s — doubling, capped, so a backend that is
 *  down does not turn an open tab into a poller. */
export function retryDelayMs(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 15000);
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useMaterialsLibrary() {
  const { user } = useAuth();
  const ready = useApiReady();
  const uid = user?.uid ?? null;

  const [base, setBase] = useState<MaterialsLibrary | null>(null);  // built-in + global
  const [mine, setMine] = useState<MineLayer>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // built-in + global (from the backend, already merged + tagged there)
  //
  // ONE failed fetch used to be final: MaterialsLibraryTree prints `error` as a
  // grey caption and nothing ever asked again, so a single failure — the web
  // image being rebuilt under an open tab, a dropped Wi-Fi second — left the
  // Materials tab EMPTY for the rest of the session (production, 2026-09-16:
  // "materials are not displayed").  The library is a plain read, so a
  // TRANSIENT failure is asked again on a bounded backoff.
  const retriesRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reloadBaseRef = useRef<((isRetry?: boolean) => void) | null>(null);

  const reloadBase = useCallback((isRetry = false) => {
    if (timerRef.current !== null) { clearTimeout(timerRef.current); timerRef.current = null; }
    if (!isRetry) retriesRef.current = 0;   // a manual reload starts the budget over
    setLoading(true);
    // 0 until the server answers at all — a DNS/offline/CORS failure never
    // reaches the first `then`, and that is the case most worth retrying.
    let status = 0;
    fetch((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') + '/api/materials/library', { cache: 'no-store' })
      .then(r => { status = r.status; if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => { retriesRef.current = 0; setBase(data); setError(null); setLoading(false); })
      .catch(e => {
        setLoading(false);
        const attempt = retriesRef.current;
        if (isRetriableStatus(status) && attempt < MAX_LIBRARY_RETRIES) {
          retriesRef.current = attempt + 1;
          setError(`${e} — retrying (${attempt + 1}/${MAX_LIBRARY_RETRIES})`);
          timerRef.current = setTimeout(
            () => reloadBaseRef.current?.(true), retryDelayMs(attempt));
        } else {
          setError(`${e} — the materials library could not be loaded; reload the page`);
        }
      });
  }, []);
  reloadBaseRef.current = reloadBase;

  // Never leave a retry armed behind an unmounted tab.
  useEffect(() => () => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
  }, []);

  // NOT before /api/me has answered, and not while nobody is signed in: this
  // hook mounts at the App ROOT (MaterialOverrideSync), so on an anonymous
  // visit the library fetch went out with the landing's first paint and came
  // back 401 (live, 2026-09-16).  `useApiReady` is the gate the geometry and
  // schema probes already sit behind; flipping it (the sign-in) re-runs this
  // effect, so the tab fills itself the moment there is a session — no page
  // reload.  A MANUAL `reload()` is left ungated on purpose: it comes from a
  // signed-in hand.
  useEffect(() => { if (!ready) return; reloadBase(); }, [ready, reloadBase]);

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

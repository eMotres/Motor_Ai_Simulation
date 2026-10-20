// Curated reference motors for the simple tuner (Configure tab).
//
// Each reference carries a PASSPORT — one FEM-extracted base operating point —
// plus the slot/wire context needed for the fit check.  scaleMotor() in
// ./motorScaling rescales the passport INSTANTLY (no FEM) as the user tunes
// lamination length / turns / wire thickness / winding connection.
//
// Passports are produced by extract_passport.py (3 FEM solves of the active
// config: loaded, no-load, and at 1.5x length).  DO NOT hand-tune the numbers —
// re-run the extractor against a reference config to refresh them.

import type { Passport } from './motorScaling';
import { isReferenceOnlyCard } from './referenceOnlyPassport';
import { referenceCandidateProvenanceFromCatalogEntry } from './referenceCardProvenance';
import { parseReferenceBasisAnswer, type ReferenceBasisAnswer } from './configuratorReferenceTemperature';

export interface ReferenceMotor {
  id: string;
  name: string;          // short title for the picker
  subtitle: string;      // one-line spec
  /** Explicitly unqualified analytical reference; no drive variants may be offered. */
  referenceOnly?: boolean;
  poles: number;
  slots: number;
  passport: Passport;
  /** the card's name is a real configuration (or die) of the catalogue — it has a pack,
   *  a controller and computed variants; legacy duplicates of a geometry do not */
  hasMachine?: boolean;
  /** the FULL passport card (v1 record) behind this machine, from the server; null/absent = none */
  card?: { die: string; config: string; date: string } | null;
  /** The server's pinned provenance identity; used only to select matching source metadata. */
  referenceProvenance?: { candidate_sha256?: string | null } | null;
  /** The server's audited-basis block for this card (`reference_basis` of the references answer); absent = not an audited card. */
  referenceBasis?: ReferenceBasisAnswer | null;
  // slot/wire context — mirrors the backend slot-fit constraint
  // (geometry_constraints._wire_height_max, which mirrors the radial wire stack
  // in cadquery_geometry): N rows of (wire_height + wireSpacingY) must fit between
  // the two insulation layers.  wire_width is FIXED (not a knob), so only the
  // radial (height) stack varies with the tuned knobs.
  fit: {
    slotHeight_mm: number;     // radial slot height
    insulation_mm: number;     // slot-liner thickness per radial side (counted ×2)
    wireSpacingY_mm: number;   // radial gap between stacked wire rows
    slotWidth_mm: number;
    wireWidth_mm: number;      // fixed conductor width
  };
  // cross-section geometry for the schematic projections (radii in mm)
  geo: {
    statorOR_mm: number; statorIR_mm: number;
    rotorOR_mm: number; rotorIR_mm: number;
    numSlots: number; numPoles: number; magnetHeight_mm: number;
  };
}

/** Inert, SYNTHETIC stand-in so the panel's hooks always hold a well-formed reference before the
 *  server's configure_load / references answer arrives.  It is NOT a machine: it never enters
 *  the picker or `allRefs`, the render layer is gated on a matched catalogue machine, and its
 *  numbers describe no real or private design (public-bundle rule, review F4 2026-10-20: the
 *  client bundle may carry no built-in machine data; the server owns every real passport). */
export const EMPTY_REFERENCE: ReferenceMotor = {
  id: '',
  name: '—',
  subtitle: '',
  poles: 14,
  slots: 12,
  passport: {
    N0: 10, L0_mm: 20, wireH0_mm: 1,
    I0_A: 10, rpm0: 1000, nP0: 1,
    T0_Nm: 1, Vemf0_peak_V: 10, Vload0_peak_V: 12, R0_ohm: 0.1,
    endWindFrac: 0.3, Pfe0_W: 1, Pmag0_W: 0.1, mass0_kg: 1,
  },
  fit: { slotHeight_mm: 10, insulation_mm: 0.2, wireSpacingY_mm: 0.1, slotWidth_mm: 5, wireWidth_mm: 4 },
  geo: { statorOR_mm: 20, statorIR_mm: 12, rotorOR_mm: 11, rotorIR_mm: 6, numSlots: 12, numPoles: 14, magnetHeight_mm: 2 },
};

// Winding connection options, derived from the SLOT COUNT.  For a 3-phase
// single-layer winding the coils per phase C = numSlots/6, and every factor pair
// (nS series × nP parallel = C) is a valid connection (nP = parallel paths).
//   12 slots → C=2 → 2S, 2P ;  24 → 4S, 2S-2P, 4P ;
//   36 → 6S, 3S-2P, 2S-3P, 6P ;  48 → 8S, 4S-2P, 2S-4P, 8P.
export interface WindingConn { label: string; nP: number; nS: number; hint: string; }

export function windingConnections(numSlots: number): WindingConn[] {
  const C = Math.max(1, Math.round((numSlots || 0) / 6));
  const out: WindingConn[] = [];
  for (let nP = 1; nP <= C; nP++) {
    if (C % nP) continue;
    const nS = C / nP;
    const label = nP === 1 ? `${C}S` : nS === 1 ? `${C}P` : `${nS}S-${nP}P`;
    const hint = nP === 1 ? 'all series — highest voltage, lowest current'
      : nS === 1 ? 'all parallel — lowest voltage, highest current'
      : `${nS} series × ${nP} parallel`;
    out.push({ label, nP, nS, hint });
  }
  return out;
}

/** Label for a parallel-path count nP at the given slot count. */
export const connLabel = (nP: number, numSlots: number): string =>
  windingConnections(numSlots).find((c) => c.nP === nP)?.label ?? `${nP}P`;

// ── Catalog-derived references ────────────────────────────────────────────────
// Motors published into the MOTORS catalog with a FEM-generated passport (admin
// "Generate passport" → POST /api/catalog/{id}/passport).  These are the real,
// simulation-backed references; there is no built-in machine in the client bundle.
const _API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

/** What the references fetch says: `ok` = the server ANSWERED (an empty list is then a
 *  real "no characterised motor"); `!ok` = it did not, so nothing is known yet. */
export interface ReferencesAnswer { ok: boolean; refs: ReferenceMotor[]; }

/** Keep only the server-pinned candidate identity used by reviewed source metadata. */

/** The lean route first (`GET /api/catalog/references`: only the cards with a passport,
 *  ~100 KB); an older server without it falls back to the whole catalogue (12 MB of
 *  thumbnails on the live one).  Configure used to wait for all of that on every open. */
async function fetchCatalogJson(): Promise<{ motors?: Array<Record<string, any>> } | null> {
  for (const path of ['/api/catalog/references', '/api/catalog']) {
    try {
      const r = await fetch(`${_API}${path}`, { cache: 'no-store' });
      if (r.ok) return await r.json();
    } catch { /* try the next one */ }
  }
  return null;
}

export async function fetchCatalogReferences(): Promise<ReferenceMotor[]> {
  return (await fetchCatalogReferencesAnswer()).refs;
}

export async function fetchCatalogReferencesAnswer(): Promise<ReferencesAnswer> {
  try {
    const cat = await fetchCatalogJson();
    if (!cat) return { ok: false, refs: [] };
    const out: ReferenceMotor[] = [];
    for (const m of (cat.motors ?? []) as Array<Record<string, any>>) {
      const sp = m?.passport;                         // { passport, fit, geo, poles, slots }
      if (!sp?.passport || !sp?.geo || !sp?.fit) continue;   // only motors that were characterised
      const p = sp.passport as Passport;
      const referenceOnly = isReferenceOnlyCard(m);
      const safePassport = { ...p };
      // Computed drive variants may ride beside the passport (the record's
      // top level) instead of inside it — Configure reads them off `p`.
      if (referenceOnly) delete safePassport.pwm_variants;
      else if (!safePassport.pwm_variants && Array.isArray(sp.pwm_variants)) {
        safePassport.pwm_variants = sp.pwm_variants;
      }
      const poles = Number(sp.poles ?? sp.geo.numPoles ?? 0);
      const slots = Number(sp.slots ?? sp.geo.numSlots ?? 0);
      out.push({
        id: `cat:${m.id}`,
        name: String(m.name ?? `${m.diameter_mm ?? '?'} mm`),
        subtitle: referenceOnly ? '' : `${slots}-slot / ${poles}-pole · ~${(p.T0_Nm ?? 0).toFixed((p.T0_Nm ?? 0) < 10 ? 1 : 0)} N·m @ ${p.rpm0 ?? '?'} rpm · FEM`,
        poles, slots,
        passport: safePassport,
        referenceOnly,
        hasMachine: m.has_machine === true,
        card: m.card && typeof m.card === 'object' ? (m.card as ReferenceMotor['card']) : null,
        // The API's passport card is a wrapper: provenance sits beside the
        // nested `passport` within `m.passport`, not on the motor row itself.
        // Accept the old top-level shape too, and fail closed on conflicts.
        referenceProvenance: referenceCandidateProvenanceFromCatalogEntry(m),
        referenceBasis: parseReferenceBasisAnswer(m.reference_basis),
        fit: sp.fit,
        geo: sp.geo,
      });
    }
    return { ok: true, refs: out };
  } catch {
    return { ok: false, refs: [] };
  }
}

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

export interface ReferenceMotor {
  id: string;
  name: string;          // short title for the picker
  subtitle: string;      // one-line spec
  poles: number;
  slots: number;
  passport: Passport;
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

export const REFERENCE_PASSPORTS: ReferenceMotor[] = [
  {
    id: 'ref-200-20p24s',
    name: '200 mm · 20-pole / 24-slot',
    subtitle: 'IPM · N52UH · ~80 N·m @ 4000 rpm (4S)',
    poles: 20,
    slots: 24,
    passport: {
      N0: 12, L0_mm: 45, wireH0_mm: 0.9,
      I0_A: 110, rpm0: 4000, nP0: 1,
      T0_Nm: 80.235, Vemf0_peak_V: 174.76, Vload0_peak_V: 210.7, R0_ohm: 0.02457,
      endWindFrac: 0.33, Pfe0_W: 168.5, Pmag0_W: 6.2, mass0_kg: 10.141,
      // FEM speed-sweep (sliding-band, I=110 A, γ=28°, L=45 mm): iron + magnet
      // eddy loss vs rpm.  scaleMotor interpolates these instead of f^1.5 / f²
      // (the FEM iron loss is ~f^1.1, not f^1.5 — the analytical law over-estimated it).
      speed: {
        rpm:    [1000, 2000, 3000, 4000, 5000, 6000],
        Pfe_W:  [37.0, 78.2, 122.2, 168.5, 217.1, 267.6],
        Pmag_W: [0.4, 1.6, 3.5, 6.2, 9.7, 14.0],
      },
    },
    fit: { slotHeight_mm: 19.8, insulation_mm: 0.2, wireSpacingY_mm: 0.13, slotWidth_mm: 7.6, wireWidth_mm: 7.0 },
    geo: { statorOR_mm: 100, statorIR_mm: 73, rotorOR_mm: 72.35, rotorIR_mm: 49.35, numSlots: 24, numPoles: 20, magnetHeight_mm: 21.4 },
  },
];

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
// simulation-backed references; REFERENCE_PASSPORTS above is the built-in seed /
// fallback used when the catalog has no characterised motor.
const _API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

export async function fetchCatalogReferences(): Promise<ReferenceMotor[]> {
  try {
    const r = await fetch(`${_API}/api/catalog`, { cache: 'no-store' });
    if (!r.ok) return [];
    const cat = await r.json();
    const out: ReferenceMotor[] = [];
    for (const m of (cat.motors ?? []) as Array<Record<string, any>>) {
      const sp = m?.passport;                         // { passport, fit, geo, poles, slots }
      if (!sp?.passport || !sp?.geo || !sp?.fit) continue;   // only motors that were characterised
      const p = sp.passport as Passport;
      const poles = Number(sp.poles ?? sp.geo.numPoles ?? 0);
      const slots = Number(sp.slots ?? sp.geo.numSlots ?? 0);
      out.push({
        id: `cat:${m.id}`,
        name: String(m.name ?? `${m.diameter_mm ?? '?'} mm`),
        subtitle: `${slots}-slot / ${poles}-pole · ~${(p.T0_Nm ?? 0).toFixed((p.T0_Nm ?? 0) < 10 ? 1 : 0)} N·m @ ${p.rpm0 ?? '?'} rpm · FEM`,
        poles, slots,
        passport: p,
        fit: sp.fit,
        geo: sp.geo,
      });
    }
    return out;
  } catch {
    return [];
  }
}

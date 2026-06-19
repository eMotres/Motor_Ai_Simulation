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
  // slot/wire context for the fit gauge (wire_width is FIXED — never a knob).
  // Conductors stack in a single column (wire_width ~ slot_width), so the
  // binding constraint is the stack HEIGHT: N * wire_height <= slotHeight*max.
  fit: {
    slotHeight_mm: number;
    slotWidth_mm: number;
    wireWidth_mm: number;    // fixed conductor width
    maxStackFrac: number;    // N*wireH / slotHeight must stay below this to fit
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
      endWindFrac: 0.33, Pfe0_W: 164.4, Pmag0_W: 5.7, mass0_kg: 10.141,
    },
    fit: { slotHeight_mm: 19.8, slotWidth_mm: 7.6, wireWidth_mm: 7.0, maxStackFrac: 0.92 },
  },
];

// Winding connection options (4 coils/phase).  nP = parallel paths; the series
// count is 4/nP.  Pure electrical re-wiring — voltage <-> current trade.
export const CONNECTIONS: { label: string; nP: number; hint: string }[] = [
  { label: '4S',    nP: 1, hint: 'all series — highest voltage, lowest current' },
  { label: '2P·2S', nP: 2, hint: 'balanced' },
  { label: '4P',    nP: 4, hint: 'all parallel — lowest voltage, highest current' },
];

export const connLabel = (nP: number) =>
  CONNECTIONS.find((c) => c.nP === nP)?.label ?? `${nP}P`;

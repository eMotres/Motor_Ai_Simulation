/**
 * Supplier lookup table for the stranded silicone lead cable actually used
 * (0.08 mm strands, "TS"), supplied by the user 2026-08-26.
 *
 * Everything here is DATA FROM THE SHEET — nothing is derived or guessed, so a
 * selection can be handed to purchasing as-is.  Two entries in the sheet look
 * like typos and are marked `suspect` rather than silently corrected:
 *   • 10 AWG resistance 3.53 Ω/km — it must sit ABOVE the 9 AWG value (5.5),
 *     since a thinner conductor has more resistance;
 *   • the second "2/0" row (75 mm²) carries conductor O.D. 1.5 mm, which is
 *     impossible for that area (its neighbours are ~11 mm).
 * Neither is used for selection (selection needs area / diameters), and the
 * UI never quotes a suspect number without saying so.
 */
export interface CableRow {
  /** sheet label: "12awg", "1/0awg", "120mm" … */
  awg: string;
  /** strand count × strand diameter, as ordered */
  strands: string;
  /** bare conductor bundle diameter [mm] */
  d_mm: number;
  /** conductor cross-section [mm²] — the selection key */
  area_mm2: number;
  /** OUTER diameter over the insulation [mm] (sheet gives ±0.1) */
  od_mm: number;
  /** insulation wall thickness [mm] */
  thk_mm: number;
  /** DC resistance [Ω/km] */
  r_ohm_km: number;
  /** continuous current [A] */
  i_rated_A: number;
  /** peak / short-time current [A] */
  i_max_A: number;
  /** standard roll length [m] */
  roll_m: number;
  /** sheet values that contradict physics — quoted only with a warning */
  suspect?: string;
}

export const CABLE_TABLE: CableRow[] = [
  { awg: '30awg', strands: '11/0.08TS',    d_mm: 0.31,  area_mm2: 0.055, od_mm: 0.8,  thk_mm: 0.30, r_ohm_km: 335,  i_rated_A: 0.6, i_max_A: 0.8,  roll_m: 2500 },
  { awg: '28awg', strands: '16/0.08TS',    d_mm: 0.37,  area_mm2: 0.08,  od_mm: 1.2,  thk_mm: 0.40, r_ohm_km: 225,  i_rated_A: 0.8, i_max_A: 1.3,  roll_m: 1500 },
  { awg: '26awg', strands: '30/0.08TS',    d_mm: 0.51,  area_mm2: 0.15,  od_mm: 1.5,  thk_mm: 0.50, r_ohm_km: 125,  i_rated_A: 1.5, i_max_A: 3.5,  roll_m: 400 },
  { awg: '24awg', strands: '40/0.08TS',    d_mm: 0.58,  area_mm2: 0.2,   od_mm: 1.6,  thk_mm: 0.50, r_ohm_km: 97.6, i_rated_A: 2,   i_max_A: 5,    roll_m: 400 },
  { awg: '22awg', strands: '60/0.08TS',    d_mm: 0.72,  area_mm2: 0.3,   od_mm: 1.7,  thk_mm: 0.45, r_ohm_km: 88.5, i_rated_A: 3,   i_max_A: 8,    roll_m: 400 },
  { awg: '20awg', strands: '100/0.08TS',   d_mm: 0.94,  area_mm2: 0.5,   od_mm: 1.8,  thk_mm: 0.45, r_ohm_km: 63,   i_rated_A: 5,   i_max_A: 13,   roll_m: 400 },
  { awg: '18awg', strands: '150/0.08TS',   d_mm: 1.14,  area_mm2: 0.75,  od_mm: 2.3,  thk_mm: 0.55, r_ohm_km: 39.5, i_rated_A: 6,   i_max_A: 22,   roll_m: 400 },
  { awg: '17awg', strands: '210/0.08TS',   d_mm: 1.34,  area_mm2: 1.05,  od_mm: 2.7,  thk_mm: 0.68, r_ohm_km: 30,   i_rated_A: 8,   i_max_A: 30,   roll_m: 200 },
  { awg: '16awg', strands: '252/0.08TS',   d_mm: 1.46,  area_mm2: 1.27,  od_mm: 3.0,  thk_mm: 0.75, r_ohm_km: 25,   i_rated_A: 10,  i_max_A: 35,   roll_m: 200 },
  { awg: '15awg', strands: '300/0.08TS',   d_mm: 1.6,   area_mm2: 1.5,   od_mm: 3.2,  thk_mm: 0.80, r_ohm_km: 20,   i_rated_A: 15,  i_max_A: 42,   roll_m: 200 },
  { awg: '14awg', strands: '400/0.08TS',   d_mm: 1.85,  area_mm2: 2.0,   od_mm: 3.5,  thk_mm: 0.90, r_ohm_km: 15,   i_rated_A: 20,  i_max_A: 55,   roll_m: 200 },
  { awg: '13awg', strands: '500/0.08TS',   d_mm: 2.07,  area_mm2: 2.5,   od_mm: 4.0,  thk_mm: 1.00, r_ohm_km: 12.5, i_rated_A: 25,  i_max_A: 70,   roll_m: 100 },
  { awg: '12awg', strands: '680/0.08TS',   d_mm: 2.41,  area_mm2: 3.4,   od_mm: 4.5,  thk_mm: 1.05, r_ohm_km: 10,   i_rated_A: 30,  i_max_A: 88,   roll_m: 100 },
  { awg: '11awg', strands: '750/0.08TS',   d_mm: 2.54,  area_mm2: 3.8,   od_mm: 5.0,  thk_mm: 1.20, r_ohm_km: 8.3,  i_rated_A: 40,  i_max_A: 105,  roll_m: 100 },
  { awg: '10awg', strands: '1050/0.08TS',  d_mm: 3.0,   area_mm2: 5.3,   od_mm: 5.5,  thk_mm: 1.30, r_ohm_km: 3.53, i_rated_A: 50,  i_max_A: 140,  roll_m: 100,
    suspect: 'sheet resistance 3.53 Ω/km is below the 9 AWG value (5.5) — a thinner conductor cannot have less resistance; treat ~6 Ω/km as the working figure' },
  { awg: '9awg',  strands: '1200/0.08TS',  d_mm: 3.21,  area_mm2: 6.0,   od_mm: 5.8,  thk_mm: 1.24, r_ohm_km: 5.5,  i_rated_A: 60,  i_max_A: 160,  roll_m: 100 },
  { awg: '8awg',  strands: '1650/0.08TS',  d_mm: 3.75,  area_mm2: 8.3,   od_mm: 6.3,  thk_mm: 1.50, r_ohm_km: 3.7,  i_rated_A: 80,  i_max_A: 190,  roll_m: 100 },
  { awg: '7awg',  strands: '2000/0.08TS',  d_mm: 4.18,  area_mm2: 10,    od_mm: 7.2,  thk_mm: 1.53, r_ohm_km: 3.1,  i_rated_A: 90,  i_max_A: 200,  roll_m: 100 },
  { awg: '6awg',  strands: '3200/0.08TS',  d_mm: 5.23,  area_mm2: 16,    od_mm: 8.5,  thk_mm: 1.65, r_ohm_km: 1.9,  i_rated_A: 140, i_max_A: 300,  roll_m: 100 },
  { awg: '5awg',  strands: '4000/0.08TS',  d_mm: 5.85,  area_mm2: 20,    od_mm: 9.5,  thk_mm: 1.83, r_ohm_km: 1.5,  i_rated_A: 160, i_max_A: 350,  roll_m: 100 },
  { awg: '4awg',  strands: '5000/0.08TS',  d_mm: 6.53,  area_mm2: 25,    od_mm: 11.5, thk_mm: 2.40, r_ohm_km: 1.25, i_rated_A: 180, i_max_A: 400,  roll_m: 50 },
  { awg: '2awg',  strands: '6700/0.08TS',  d_mm: 7.74,  area_mm2: 35,    od_mm: 13.0, thk_mm: 2.60, r_ohm_km: 0.89, i_rated_A: 200, i_max_A: 500,  roll_m: 50 },
  { awg: '1/0awg', strands: '10000/0.08TS', d_mm: 9.23, area_mm2: 50,    od_mm: 14.0, thk_mm: 2.40, r_ohm_km: 0.63, i_rated_A: 300, i_max_A: 630,  roll_m: 50 },
  { awg: '2/0awg', strands: '14000/0.08TS', d_mm: 10.93, area_mm2: 70,   od_mm: 15.5, thk_mm: 2.05, r_ohm_km: 0.48, i_rated_A: 350, i_max_A: 750,  roll_m: 50 },
  { awg: '2/0awg', strands: '15000/0.08TS', d_mm: 11.5, area_mm2: 75,    od_mm: 15.5, thk_mm: 2.30, r_ohm_km: 0.45, i_rated_A: 400, i_max_A: 840,  roll_m: 50,
    suspect: 'sheet conductor O.D. reads 1.5 mm for a 75 mm² cable — impossible; 11.5 mm assumed (its neighbours are 10.9 and 12.7)' },
  { awg: '4/0awg', strands: '19000/0.08TS', d_mm: 12.74, area_mm2: 95.5, od_mm: 18.0, thk_mm: 2.64, r_ohm_km: 0.18, i_rated_A: 500, i_max_A: 955,  roll_m: 50 },
  { awg: '120mm',  strands: '24000/0.08TS', d_mm: 14.5,  area_mm2: 120,  od_mm: 21.0, thk_mm: 2.85, r_ohm_km: 0.15, i_rated_A: 600, i_max_A: 1200, roll_m: 50 },
  { awg: '150mm',  strands: '30000/0.08TS', d_mm: 16.6,  area_mm2: 150,  od_mm: 22.5, thk_mm: 4.00, r_ohm_km: 0.12, i_rated_A: 750, i_max_A: 1500, roll_m: 50 },
];

/**
 * The catalogue cable for a required copper section — the first row whose area
 * is **≥ required** (user's rule 2026-08-26: never round a conductor down).
 * `null` when even the largest roll is too small (then it is parallel cables).
 */
export function pickCable(area_mm2: number): CableRow | null {
  if (!Number.isFinite(area_mm2) || area_mm2 <= 0) return null;
  for (const row of CABLE_TABLE) {             // ordered thin → thick
    if (row.area_mm2 >= area_mm2 * (1 - 1e-9)) return row;
  }
  return null;
}

/** Same, but chosen by CURRENT against the sheet's continuous rating. */
export function pickCableByCurrent(i_A: number): CableRow | null {
  if (!Number.isFinite(i_A) || i_A <= 0) return null;
  for (const row of CABLE_TABLE) {
    if (row.i_rated_A >= i_A) return row;
  }
  return null;
}

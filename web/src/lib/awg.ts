/**
 * AWG pick for a copper cross-section.
 *
 * Sizes come from the definition, not a typed-in table: the AWG ladder is
 * geometric, d(n) = 0.127 mm × 92^((36−n)/39), so every gauge from 4/0 to 30
 * is exact and nothing can drift out of step with a datasheet.
 *
 * SELECTION RULE (user 2026-08-26): take the gauge whose copper area is
 * **≥ the required** one — a cable is chosen to carry the current, so rounding
 * down is never acceptable; the next size up is.
 *
 * INSULATED O.D. is the enamelled-wire overall diameter.  The exact value is a
 * class + supplier property (IEC 60317-0-1 / NEMA MW 1000 tabulate a MAXIMUM
 * per grade); the build used here is the standard fit ΔD ≈ c·√d that matches
 * those tables to a few hundredths of a millimetre:
 *     grade 1 (single build)  c = 0.048
 *     grade 2 (heavy  build)  c = 0.073
 * A silicone or PTFE LEAD is a different animal — its jacket adds 1–2 mm, so a
 * lead is quoted by its own datasheet, not by this build.
 */
export type AwgGrade = 'bare' | 'grade1' | 'grade2';

export interface AwgPick {
  /** gauge label: "12", "1/0", "4/0" … */
  label: string;
  /** bare conductor diameter [mm] */
  d_mm: number;
  /** overall diameter with enamel [mm] for the requested grade */
  od_mm: number;
  /** bare conductor area [mm²] */
  area_mm2: number;
  /** headroom over the required area, % (≥ 0 by construction) */
  margin_pct: number;
}

const dOf = (n: number) => 0.127 * Math.pow(92, (36 - n) / 39);
const areaOf = (n: number) => (Math.PI / 4) * dOf(n) * dOf(n);
const labelOf = (n: number) => (n > 0 ? String(n) : `${1 - n}/0`);

/** Enamel build added on top of the bare conductor [mm]. */
export function insulationBuild(d_mm: number, grade: AwgGrade = 'grade2'): number {
  if (grade === 'bare') return 0;
  const c = grade === 'grade1' ? 0.048 : 0.073;
  return c * Math.sqrt(d_mm);
}

/**
 * Smallest gauge whose copper area is ≥ `area_mm2`.
 * Returns null for a nonsensical area, or when even 4/0 is too small.
 */
export function pickAWG(area_mm2: number, grade: AwgGrade = 'grade2'): AwgPick | null {
  if (!Number.isFinite(area_mm2) || area_mm2 <= 0) return null;
  for (let n = 30; n >= -3; n--) {            // 30 … 4/0, thin → thick
    const a = areaOf(n);
    if (a >= area_mm2 * (1 - 1e-9)) {
      const d = dOf(n);
      return {
        label: labelOf(n),
        d_mm: d,
        od_mm: d + insulationBuild(d, grade),
        area_mm2: a,
        margin_pct: 100 * (a - area_mm2) / area_mm2,
      };
    }
  }
  return null;                                 // beyond 4/0 — needs parallel cables
}

/** "AWG 11 · Ø2.31 / 2.42 mm" — bare / insulated, the one-line form. */
export function awgText(area_mm2: number, grade: AwgGrade = 'grade2'): string {
  const a = pickAWG(area_mm2, grade);
  return a ? `AWG ${a.label} · Ø${a.d_mm.toFixed(2)} / ${a.od_mm.toFixed(2)} mm` : '—';
}

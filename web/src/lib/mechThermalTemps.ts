/**
 * mechThermalTemps — the Thermal tab's per-part temperatures, as the Mechanical
 * tab uses them.
 *
 * User 2026-09-08: *"в механический расчёт тоже нужно делать каплинг, чтобы
 * температуры везде были одинаковы"*.  The Thermal solve already reports a
 * temperature for every solid; the Mechanical solve used to take the rotor and
 * sleeve MAXIMA and stretch the rotor's number over the core, the magnets and
 * the shaft.  That is two numbers standing in for four, and on a real machine
 * the four are not the same — the magnets run hotter than the iron they sit in,
 * and the aluminium shaft grows twice as fast as the steel it is pressed into.
 *
 * TWO DECISIONS LIVE HERE, and they are the reason this is a module and not
 * three lines inside the store:
 *
 *   • THE AVERAGE DRIVES THE FIT, THE MAXIMUM IS FOR READING.  A thermal
 *     eigenstrain is a bulk strain of a whole solid, so the temperature that
 *     belongs in it is that solid's MEAN — a hot spot on one corner of a magnet
 *     does not set how much the magnet grows.  (Same reasoning the Thermal
 *     tab's own loss model already uses for the winding: the bulk resistivity
 *     takes the winding's mean, not its peak.)  The max is carried alongside
 *     because it is what an engineer wants to see next to it, and it goes in
 *     the tooltip.
 *   • A PART THE THERMAL ANSWER DOES NOT CARRY KEEPS THE MANUAL FIELD.  The
 *     API's own fallback (`rotor_temp_c` covers core / magnet / shaft) is what
 *     the two scalars are for, so a machine whose mesh resolved no sleeve still
 *     sends a sleeve temperature the user chose rather than nothing.
 *
 * Pure and dependency-free on purpose: the request builder in
 * `stores/mechanicalStore` and the line in `components/mechanical/MechanicalPanel`
 * must agree about what is being sent, and `__tests__/mechThermalTemps.test.mjs`
 * pins it.
 */

/** One part's temperature out of a Thermal result — °C, `null` = not solved. */
export interface PartTemp {
  /** the part's MEAN temperature; this is what the eigenstrain uses */
  avg: number | null;
  /** the part's hot spot; shown, never sent */
  max: number | null;
}

/** The four rotor solids the Mechanical solve can heat, plus provenance. */
export interface MechThermalTemps {
  magnet: PartTemp;
  /** the rotor CORE (iron) — the Thermal payload calls this part `rotor` */
  rotor: PartTemp;
  shaft: PartTemp;
  sleeve: PartTemp;
  /** ISO stamp of the Thermal result these came from */
  at: string | null;
  /** the backend's verdict: was that result solved on another geometry?
   *  `null` = UNKNOWN, which is reported as not-stale — a check that cannot
   *  prove a mismatch must never claim one. */
  stale: boolean | null;
}

/** What the rotor-stress request carries.  The two scalars are always sent (they
 *  are the API's fallback); the three per-part fields only when they are known,
 *  so a request that has nothing extra to say is byte for byte the one this app
 *  has always sent. */
export interface MechTempParams {
  rotor_temp_c: number;
  sleeve_temp_c: number;
  magnet_temp_c?: number;
  rotor_core_temp_c?: number;
  shaft_temp_c?: number;
}

/** The temperature the material library is quoted at — mirrors
 *  `rotor_stress.REF_TEMP_C`.  An empty field means "no thermal load", which is
 *  this, and never `Number('') === 0` (that would be a −20 K load). */
export const REF_TEMP_C = 20;

const fin = (v: unknown): number | null =>
  (typeof v === 'number' && Number.isFinite(v)) ? v : null;

/** A temperature field's value, or the reference when it is empty / unusable. */
export function tempOr(v: string | null | undefined, ref = REF_TEMP_C): number {
  const t = (v ?? '').trim();
  const n = Number(t);
  return t !== '' && Number.isFinite(n) ? n : ref;
}

/** The four parts out of a Thermal result's `components` block.
 *
 *  `null` when that result carries none of them: the panel then has nothing to
 *  offer and keeps the manual fields, rather than a menu entry that would send
 *  a temperature nobody computed. */
export function partTempsFromComponents(
  components: Record<string, { max?: number | null; avg?: number | null } | null | undefined>
    | null | undefined,
  at: string | null | undefined,
  stale: boolean | null | undefined,
): MechThermalTemps | null {
  const c = components ?? {};
  const one = (key: string): PartTemp => {
    const e = c[key];
    return { avg: fin(e?.avg), max: fin(e?.max) };
  };
  const out: MechThermalTemps = {
    magnet: one('magnet'), rotor: one('rotor'),
    shaft: one('shaft'), sleeve: one('sleeve'),
    at: at ?? null, stale: stale ?? null,
  };
  const any = ([out.magnet, out.rotor, out.shaft, out.sleeve])
    .some((p) => p.avg !== null || p.max !== null);
  return any ? out : null;
}

/** Are the Thermal temperatures the ones a Solve should send?
 *
 *  Asked for AND fresh.  `stale === null` is UNKNOWN and counts as fresh, for
 *  the same reason the backend reports it as `null`: a staleness check that
 *  cannot prove a mismatch must not claim one. */
export function thermalTempsInUse(tempSource: 'manual' | 'thermal',
                                  temps: MechThermalTemps | null | undefined): boolean {
  return tempSource === 'thermal' && !!temps && temps.stale !== true;
}

/** THE request builder: which temperatures this Solve sends, and under which
 *  names.  Manual (or stale, or nothing solved) sends exactly the pair it always
 *  did and no new field at all. */
export function mechTempParams(
  tempSource: 'manual' | 'thermal',
  temps: MechThermalTemps | null | undefined,
  manual: { rotorTempC: string; sleeveTempC: string },
  ref = REF_TEMP_C,
): MechTempParams {
  const manualRotor = tempOr(manual.rotorTempC, ref);
  const manualSleeve = tempOr(manual.sleeveTempC, ref);
  if (!thermalTempsInUse(tempSource, temps)) {
    return { rotor_temp_c: manualRotor, sleeve_temp_c: manualSleeve };
  }
  const t = temps as MechThermalTemps;
  const out: MechTempParams = {
    rotor_temp_c: t.rotor.avg ?? manualRotor,
    sleeve_temp_c: t.sleeve.avg ?? manualSleeve,
  };
  if (t.magnet.avg !== null) out.magnet_temp_c = t.magnet.avg;
  if (t.rotor.avg !== null) out.rotor_core_temp_c = t.rotor.avg;
  if (t.shaft.avg !== null) out.shaft_temp_c = t.shaft.avg;
  return out;
}

/** The four parts, in the order the line and the tooltip name them. */
const ROWS: [keyof Pick<MechThermalTemps, 'magnet' | 'rotor' | 'shaft' | 'sleeve'>,
             string][] = [
  ['magnet', 'magnets'], ['rotor', 'core'], ['shaft', 'shaft'], ['sleeve', 'sleeve'],
];

/** `"09:38"` in the viewer's own clock, `''` when the stamp is unusable. */
export function shortClock(at: string | null | undefined): string {
  if (!at) return '';
  const d = new Date(at);
  return Number.isNaN(d.getTime())
    ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** ONE short line under the temperature source — the project's no-walls-of-text
 *  rule.  `''` when there is nothing to say. */
export function thermalTempsLine(temps: MechThermalTemps | null | undefined): string {
  if (!temps) return '';
  const parts = ROWS
    .map(([k, label]) => [label, temps[k].avg] as const)
    .filter(([, v]) => v !== null)
    .map(([label, v]) => `${label} ${Math.round(v as number)}`);
  if (!parts.length) return '';
  const when = shortClock(temps.at);
  return `from Thermal${when ? ` ${when}` : ''}: ${parts.join(' · ')} °C`;
}

/** Everything the line leaves out: which number is used, the hot spots, and the
 *  staleness warning.  Lives in the tooltip, never on the page. */
export function thermalTempsTip(temps: MechThermalTemps | null | undefined): string {
  if (!temps) {
    return 'Nothing has been solved on the Thermal tab yet, so the two fields '
      + 'above are what a Solve sends.';
  }
  const rows = ROWS
    .filter(([k]) => temps[k].avg !== null || temps[k].max !== null)
    .map(([k, label]) => {
      const p = temps[k];
      const a = p.avg === null ? '—' : `${Math.round(p.avg)} °C`;
      return `${label} ${a}${p.max === null ? '' : ` (max ${Math.round(p.max)})`}`;
    }).join(' · ');
  const stale = temps.stale === true
    ? ' ⚠ That result was solved on a DIFFERENT geometry than the one loaded '
      + 'now, so it is not used — the manual fields are, until Thermal is '
      + 're-solved.'
    : '';
  return `Each part is solved at its OWN temperature, taken from the last `
    + `Thermal result: ${rows}. The AVERAGE is what a Solve sends — a thermal `
    + `eigenstrain is a bulk strain of the whole solid, so the fit is set by the `
    + `part's mean temperature and not by a hot spot on one corner; the maximum `
    + `is in brackets for reading.${stale} A part the Thermal answer does not `
    + `carry keeps the manual field beside it. Solved `
    + `${temps.at ? temps.at.replace('T', ' ').replace('+00:00', ' UTC') : 'at an unknown time'}`
    + ` — press ↻ to re-read it.`;
}

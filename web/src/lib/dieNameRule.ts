/**
 * The naming rule for new motors (dies) - the TypeScript twin of
 * `src/motor_ai_sim/die_names.py` (owner decision 2026-10-26: "normal names, just add
 * the user's suffix").
 *
 * A variant made from "CIANO56 400_80 Ø400 48s56p opt3" is called
 * "CIANO56 400_80 vadim", then "CIANO56 400_80 vadim 2", "… vadim 3".  The diameter
 * token (Ø400), the slots/poles token (48s56p) and free words (opt3, edited, new) are
 * not part of a new name - they are visible in the parameters.
 *
 * The SERVER decides the real proposal (`GET /api/family/die_name`: it knows the account
 * and which names are taken); this pure function is only the fallback when that call
 * fails, so a dialog never opens empty.  No `import.meta` here, so Node can test it.
 */

/** letters with an optional trailing number: CIANO56, CIANO, CILN28, M1 */
const FAMILY = /^[A-Za-z][A-Za-z-]*\d*$/;
/** a plain number or numbers joined by _ - . x: 400_80, 85, 150_35 */
const SIZE = /^\d+(?:[_.\-xX]\d+)*$/;
const NUMBER = /^\d+(?:\.\d+)?$/;
const UNITS = new Set(['mm', 'cm', 'm']);

/** "CIANO56 400_80 Ø400 48s56p opt3" -> "CIANO56 400_80" (the leading family token plus one size token). */
export function familyBase(name: string): string {
  const tokens = String(name ?? '').replace(/·/g, ' ').split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return '';
  const first = tokens[0];
  if (NUMBER.test(first)) {
    // a legacy "100 mm · 24s-28p mid-torque" name keeps its size and unit
    return tokens.length > 1 && UNITS.has(tokens[1].toLowerCase()) ? `${first} ${tokens[1]}` : first;
  }
  if (FAMILY.test(first)) {
    return tokens.length > 1 && SIZE.test(tokens[1]) ? `${first} ${tokens[1]}` : first;
  }
  return first;
}

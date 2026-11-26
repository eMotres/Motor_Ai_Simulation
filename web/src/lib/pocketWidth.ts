/** The coil pocket the builders CUT into the lamination [mm], computed from the wire keys of a saved geometry.
 *
 *  `column + 2 x insulation_thickness + wire_spacing_x`, column = N x wire_width + (N - 1) x 2 x wire_spacing_x
 *  (N = wire_split).  The same formula as `motor_ai_sim.geometry.motor_geometry.pocket_width` (Python, the one
 *  implementation); not the stored `slot_width`, which is one wire_spacing_x wider (the wire pitch the slot has to
 *  accept).  null when an input is missing.  Pure; nothing stored.
 *
 *  Both implementations are pinned to the SAME cases in tests/fixtures/pocket_width_cases.json (read by the pytest
 *  and by the node test), which also holds the strip gap factor, so they cannot drift apart. */
export const STRIP_GAP_FACTOR = 2.0;

/** A finite number, or null: null / undefined / '' / whitespace / text / inf / nan are all "missing". */
function num(v: unknown): number | null {
  if (v === null || v === undefined || typeof v === 'boolean') return null;
  if (typeof v === 'string' && v.trim() === '') return null;
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
}

/** Python's round(): halves go to the even neighbour. */
function roundHalfEven(x: number): number {
  const f = Math.floor(x), d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

export function pocketWidth(g: Record<string, unknown> | null | undefined): number | null {
  if (!g) return null;
  const ww = num(g.wire_width), wsx = num(g.wire_spacing_x), ins = num(g.insulation_thickness);
  if (ww === null || wsx === null || ins === null) return null;
  const sp = num(g.wire_split);                              // missing / 0 / non-finite = one strip, as in Python
  const n = sp === null || sp === 0 ? 1 : Math.max(1, roundHalfEven(sp));
  const col = n <= 1 ? ww : n * ww + (n - 1) * STRIP_GAP_FACTOR * wsx;
  return col + 2 * ins + wsx;
}

export const POCKET_WIDTH_TIP =
  'The coil pocket cut into the iron: wire column + 2 x insulation + 1 x wire spacing. '
  + 'The stored slot width is the wire pitch the slot has to accept (one wire spacing wider).';
export const SLOT_WIDTH_TIP =
  'The stored slot width: the wire pitch the slot has to accept (pocket width + 1 x wire spacing). '
  + 'It feeds the mesh and the thermal model; the pocket cut in the iron is the pocket width.';

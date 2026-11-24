/** The coil pocket the builders CUT into the lamination [mm], computed from the wire keys of a saved geometry.
 *
 *  `column + 2 x insulation_thickness + wire_spacing_x`, column = N x wire_width + (N - 1) x 2 x wire_spacing_x
 *  (N = wire_split).  The same formula as `motor_ai_sim.geometry.motor_geometry.pocket_width` (Python, the one
 *  implementation); not the stored `slot_width`, which is one wire_spacing_x wider (the wire pitch the slot has to
 *  accept).  null when an input is missing.  Pure; nothing stored. */
const STRIP_GAP_FACTOR = 2.0;

export function pocketWidth(g: Record<string, unknown> | null | undefined): number | null {
  if (!g) return null;
  const ww = Number(g.wire_width), wsx = Number(g.wire_spacing_x), ins = Number(g.insulation_thickness);
  if (g.wire_width == null || g.wire_spacing_x == null || g.insulation_thickness == null) return null;
  if (!Number.isFinite(ww) || !Number.isFinite(wsx) || !Number.isFinite(ins)) return null;
  const sp = Number(g.wire_split);
  const n = Number.isFinite(sp) && sp > 0 ? Math.max(1, Math.round(sp)) : 1;
  const col = n <= 1 ? ww : n * ww + (n - 1) * STRIP_GAP_FACTOR * wsx;
  return col + 2 * ins + wsx;
}

export const POCKET_WIDTH_TIP =
  'The coil pocket cut into the iron: wire column + 2 x insulation + 1 x wire spacing. '
  + 'The stored slot width is the wire pitch the slot has to accept (one wire spacing wider).';
export const SLOT_WIDTH_TIP =
  'The stored slot width: the wire pitch the slot has to accept (pocket width + 1 x wire spacing). '
  + 'It feeds the mesh and the thermal model; the pocket cut in the iron is the pocket width.';

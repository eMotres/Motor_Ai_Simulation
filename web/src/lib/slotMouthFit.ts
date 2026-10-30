// SPDX-License-Identifier: Apache-2.0
// Copyright (C) MOTRES d.o.o. and contributors
/**
 * Does the winding fit ACROSS the slot?  The browser twin of
 * `geometry_validation.slot_cut_limits` / `slot_mouth_infeasibility` (the server
 * refuses the same geometry with a 422 on /api/geometry/mesh2d and at the solver
 * gate; this lets the panel say so BEFORE asking).
 *
 * The half leg's outer side lands at (Fusion: tooth/2 + coil_w + tooth2)
 *   cut_x = tooth/2 + 2·ins + column + dx + tooth2,
 *   column = w                    (no split)
 *          = N·w + (N−1)·2·dx     (wire_split = N)
 * and the half-sector wedge leaves cut_x_max = (r_in + cut)·sin(slot_angle/2).
 * cut_x >= cut_x_max  <=>  slot-mouth fill radius <= 0: the neighbouring slot
 * cutters collide and the stator would be drawn with a lobe (owner 2026-10-15).
 */

export const STRIP_GAP_FACTOR = 2.0;   // winding.STRIP_GAP_FACTOR

export interface SlotMouthLimit {
  /** false: refuse to draw / solve */
  feasible: boolean;
  /** which input is named: the wire width, or the strip count with a split */
  field: 'wire_width' | 'wire_split';
  /** the largest wire_width [mm] (fill radius = 0) with everything else held */
  maxWireWidthMm: number;
  /** the largest strip count (only meaningful with a split) */
  maxStrips: number;
  turns: number;
}

const num = (v: unknown): number | null => {
  const x = typeof v === 'number' ? v : Number(v);
  return v != null && v !== '' && Number.isFinite(x) ? x : null;
};

/** null when a needed key is missing (a partial geometry is judged by what it has). */
export function slotMouthLimit(geo: Record<string, unknown> | null | undefined): SlotMouthLimit | null {
  if (!geo) return null;
  const seg = num(geo.num_seg), sps = num(geo.num_slots_per_segment);
  let ns = seg && sps && seg > 0 && sps > 0 ? seg * sps : null;
  if (!ns) ns = num(geo.num_slots);
  const od = num(geo.stator_diameter) ?? (num(geo.stator_outer_radius) != null ? (num(geo.stator_outer_radius) as number) * 2 : null);
  const ct = num(geo.core_thickness), sh = num(geo.slot_height);
  const tw = num(geo.tooth_width), t2w = num(geo.tooth2_width);
  const ww = num(geo.wire_width), dx = num(geo.wire_spacing_x);
  const ins = num(geo.insulation_thickness), cw = num(geo.cut_width);
  if (ns == null || od == null || ct == null || sh == null || tw == null || t2w == null
      || ww == null || dx == null || ins == null || cw == null || ns < 2) return null;
  const innerR = od / 2 - ct - sh;
  if (innerR <= 0) return null;
  let split = Math.round(num(geo.wire_split) ?? 1);
  if (split < 1) split = 1;
  const gap = STRIP_GAP_FACTOR * dx;
  const column = split <= 1 ? ww : split * ww + (split - 1) * gap;
  const cutX = tw / 2 + 2 * ins + column + dx + t2w;
  const half = Math.max(1, Math.floor(Math.round(ns) / 2));
  const cutXMax = (innerR + cw) * Math.sin((Math.PI / 180) * (360 / half / 2));
  const turns = Math.round(num(geo.num_wires_per_slot) ?? 0);
  const over = cutX - cutXMax;
  const feasible = over < -1e-9;
  const maxWireWidthMm = Math.max(0, ww - over / Math.max(1, split));
  const maxStrips = split >= 2 && ww + gap > 0
    ? Math.max(1, Math.floor(split - over / (ww + gap) - 1e-9)) : 1;
  return { feasible, field: split >= 2 ? 'wire_split' : 'wire_width', maxWireWidthMm, maxStrips, turns };
}

/** Same words as the server's 422 (`geometry_validation.slot_mouth_infeasibility`). */
export function slotMouthMessage(l: SlotMouthLimit): string {
  const at = l.turns > 0 ? ` at ${l.turns} turn${l.turns === 1 ? '' : 's'}` : '';
  return l.field === 'wire_width'
    ? `wire too wide for this slot: max wire width ${l.maxWireWidthMm.toFixed(2)} mm${at}`
    : `wire split too wide for this slot: max ${l.maxStrips} strip${l.maxStrips === 1 ? '' : 's'} of the current wire width${at}`;
}

export type CrossSectionState =
  | { kind: 'ok' }
  | { kind: 'infeasible'; limit: SlotMouthLimit }   // refuse to fetch / draw
  | { kind: 'refused'; reason: string };             // the server said 422 "infeasible geometry - …"

/** What the cross-section panel shows for a geometry (client side, before the fetch). */
export function crossSectionState(geo: Record<string, unknown> | null | undefined): CrossSectionState {
  const l = slotMouthLimit(geo);
  return l && !l.feasible ? { kind: 'infeasible', limit: l } : { kind: 'ok' };
}

/** The server's 422 body -> the reason after "infeasible geometry - ", or null for any other failure.
 *  A failed construction ("stator outline not symmetric (construction failed) - ...") is an answer
 *  too: it is shown whole, never drawn as a lobe. */
export function refusedReason(body: unknown): string | null {
  const d = (body as { detail?: { error?: unknown } } | null)?.detail;
  const e = typeof d?.error === 'string' ? d.error : '';
  const m = /^infeasible geometry - (.+)$/.exec(e);
  if (m) return m[1];
  return /^stator outline not symmetric \(construction failed\) - .+$/.test(e) ? e : null;
}

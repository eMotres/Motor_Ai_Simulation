// SPDX-License-Identifier: Apache-2.0
// Copyright (C) MOTRES d.o.o. and contributors
/**
 * A geometry FLAG's value (2026-11-03): the stored boolean, or what its absence
 * means (`dflt`, true when the schema says nothing).  `stator_outer_notches` is
 * stored only when it is OFF, so an absent key is a notched stator — the same
 * reading as the server's `motor_geometry.stator_outer_notches`.
 */
export function flagValue(v: unknown, dflt?: unknown): boolean {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'number') return v !== 0;
  if (typeof v === 'string') return !['false', '0', 'off', 'no', 'none', ''].includes(v.trim().toLowerCase());
  return dflt === undefined || dflt === null ? true : !!dflt;
}

/** The geometry keys that are FLAGS (booleans on the wire, stored only when OFF).  They are not numbers, so the old numeric-only filter
 *  of the `?geo=` carrier dropped them (2026-11-27: a round-OD saved configuration, `stator_outer_notches: false`, reached the server as
 *  a NOTCHED stator on every compute request of a client whose design lives in its own copy). */
export const GEOMETRY_FLAG_KEYS = ['stator_outer_notches'] as const;

/** What the `?geo=` / kernel `geo` carrier sends of a geometry: every finite number, plus every flag that is present as a boolean (an
 *  absent flag stays absent: absent = on).  `null` when there is nothing to send. */
export function geoOverrideBody(g: Record<string, unknown> | null | undefined): Record<string, number | boolean> | null {
  if (!g) return null;
  const out: Record<string, number | boolean> = {};
  for (const [k, v] of Object.entries(g)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
    else if (typeof v === 'boolean' && (GEOMETRY_FLAG_KEYS as readonly string[]).includes(k)) out[k] = v;
  }
  return Object.keys(out).length ? out : null;
}

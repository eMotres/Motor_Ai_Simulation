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

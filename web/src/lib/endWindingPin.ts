// The end-winding factor a SWEEP / OPTIMIZE / DOE sends with its request.
//
// The Simulation field `sim.endWinding` holds the built coil's k_end for the
// CURRENT geometry (masses.end_winding_factor, #507) unless the user typed a
// value.  A study changes the geometry point by point, so the coil's number of
// the current machine must not be pinned on every point: a length sweep at one
// fixed k_end scales the end-turn copper with the stack, and 90 mm and 95 mm
// came out with the same efficiency and N·m/kg (owner 2026-10-30).  Only a
// TYPED value is the user's override for every point; otherwise the request
// carries 0 and the backend builds each point's own coil.

/** Short, stable key of a geometry signature: a typed k_end belongs to the geometry it was typed on. */
export function geoKeyHash(sig: string): string {
  let h = 5381;
  for (let i = 0; i < sig.length; i++) h = ((h * 33) ^ sig.charCodeAt(i)) >>> 0;
  return `${sig.length}:${h.toString(36)}`;
}

export interface EndWindingPin {
  /** k_end every point is billed at; 0 = each point's own built coil. */
  k: number;
  /** true = the user typed `k` in Simulation for this geometry. */
  typed: boolean;
}

function lsJson(key: string): unknown {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? undefined : JSON.parse(raw);
  } catch { return undefined; }
}

/** The k_end a study pins on its points: the typed value for THIS geometry, or 0 (per-point coil). */
export function endWindingPin(geometry: unknown): EndWindingPin {
  const typedOn = lsJson('sim.endWindingTypedOn');
  if (typeof typedOn !== 'string' || typedOn === '') return { k: 0, typed: false };
  // Typed on another geometry → not an override of this one (the Simulation
  // panel clears the flag the same way when it sees the geometry change).
  if (typedOn !== geoKeyHash(JSON.stringify(geometry ?? {}))) return { k: 0, typed: false };
  const k = Number(lsJson('sim.endWinding'));
  return Number.isFinite(k) && k > 0 ? { k, typed: true } : { k: 0, typed: false };
}

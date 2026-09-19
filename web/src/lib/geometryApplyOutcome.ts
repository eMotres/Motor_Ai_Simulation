/**
 * Turns ONE `PUT /api/geometry` outcome into a plain verdict a CALLER can act
 * on: were the fields it asked for actually written, or did the backend
 * refuse them?
 *
 * motorStore.updateGeometryViaApi already surfaces a refusal in
 * `geometryParamErrors` for the Geometry tab's own red list — but nothing
 * downstream of a PROGRAMMATIC apply (Sweep study's "Apply to geometry",
 * the descent/optimizer "Apply best design", …) ever looked at it.  A 423
 * (die/configuration lock) is a Promise<void> resolving normally, same as a
 * 200, so the caller had no way to tell "written" from "refused, nothing
 * changed" and reported success either way.
 *
 * Root cause (owner report, 2026-09-19, CIANO14 50 / L15): the sweep study's
 * "Apply to geometry" PUT the picked point's tooth_width / magnet_fill_up,
 * the backend answered 423 (die+configuration locked — see
 * routes/family.py::geometry_lock_check), and the panel still printed
 * "✓ applied" — the operating point (current/γ) DID apply (a separate PATCH
 * that has no lock), so half the picked design landed silently while the
 * other half was refused and nobody was told.
 */

export interface GeometryFieldIssue {
  field: string;
  reason: string;
}

export interface GeometryApplyOutcome {
  /** true when every field the caller asked for is now live (written to the
   *  server, or — offline — applied to the local copy and queued to replay). */
  ok: boolean;
  /** Named refusals, e.g. a locked die/configuration field. Empty when ok. */
  refused: GeometryFieldIssue[];
}

/**
 * @param status HTTP status of the PUT (0 for "the fetch itself threw" — a
 *   network death, handled the same as an outage: applied locally, queued).
 * @param requestedFields the keys this call actually asked to change.
 * @param namedIssues the backend's own `invalid_parameters` list for a 422 /
 *   423 (each item carries `field` and either `reason` or `message`) — pass
 *   null/undefined for a 500 (no per-field detail) or any other status.
 */
export function geometryApplyOutcome(
  status: number,
  requestedFields: string[],
  namedIssues: ReadonlyArray<{ field?: unknown; reason?: unknown; message?: unknown }> | null | undefined,
): GeometryApplyOutcome {
  // 2xx = the server wrote it (a value may have been CLAMPED to fit, but it
  // was still written — that nuance stays in geometryParamErrors for the
  // Geometry tab's own list, not this ok/refused verdict).
  if (status >= 200 && status < 300) return { ok: true, refused: [] };

  // 422 (bad value) / 423 (locked) / 500 (server-side failure) all mean
  // NOTHING was written for this request.
  if (status === 422 || status === 423 || status === 500) {
    const requested = new Set(requestedFields);
    // Only count an issue against a field THIS call actually asked to
    // change — a stray/global entry the backend attached (or one left over
    // from parsing) must never refuse a field nobody requested.
    const named = (namedIssues || [])
      .filter((i) => i && typeof i.field === 'string' && i.field && requested.has(String(i.field)))
      .map((i) => ({
        field: String(i.field),
        reason: String(i.reason ?? i.message ?? 'rejected'),
      }));
    // The backend usually names every refused field; if it didn't (a bare
    // 500, or a 423 whose hint wasn't per-field), fall back to refusing
    // everything the caller asked for — reporting nothing changed can never
    // overstate what actually happened, the way reporting success could.
    const refused = named.length ? named
      : requestedFields.map((f) => ({ field: f, reason: 'rejected' }));
    return { ok: refused.length === 0, refused };
  }

  // Outage (502/503/504, or the fetch threw and status is passed as 0): the
  // store applies the edit to its LOCAL copy and queues it for replay once
  // the backend returns — so as far as the caller (and the screen) is
  // concerned it IS applied, just not confirmed by the server yet.
  return { ok: true, refused: [] };
}

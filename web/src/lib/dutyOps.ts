/**
 * The per-duty catalog operations (create, rename, duplicate, delete, make the
 * configuration's default).  They lived in the catalog's duty table; the duty
 * concept now sits in the Electromagnetic Simulation tab (owner 2026-11-04) and
 * this is the one place that spells the endpoints.  Each returns the raw
 * Response so the caller can show the backend's own error text (they are
 * written for engineers).  Stored data is untouched by the move: these are the
 * same requests, to the same routes, as before.
 */
const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

const dutyPath = (die: string, cfg: string, duty: string): string =>
  `${API}/api/family/duty/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/${encodeURIComponent(duty)}`;

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

/** A new duty from the CURRENT Simulation point (current, rpm, γ, mode). */
export const createDuty = (die: string, cfg: string, name: string, mode: 'motor' | 'generator') =>
  fetch(`${API}/api/family/duty`, json('POST', { die, config: cfg, duty: { name, mode, from_current: true } }));

/** Operating point, targets, result, stored runs and every solver answer follow the new name. */
export const renameDuty = (die: string, cfg: string, duty: string, name: string) =>
  fetch(dutyPath(die, cfg, duty), json('PATCH', { name }));

/** Full copy: operating point, targets, note AND the recorded result. */
export const duplicateDuty = (die: string, cfg: string, duty: string, name: string) =>
  fetch(`${dutyPath(die, cfg, duty)}/duplicate`, json('POST', { name }));

/** The saved point and its recorded results go with it. */
export const deleteDuty = (die: string, cfg: string, duty: string) =>
  fetch(dutyPath(die, cfg, duty), { method: 'DELETE' });

/** The duty the Motors list shows and loads for this configuration. */
export const setDefaultDuty = (die: string, cfg: string, duty: string) =>
  fetch(`${API}/api/family/config/${encodeURIComponent(die)}/${encodeURIComponent(cfg)}/default_duty`,
    json('PATCH', { duty }));

/** localStorage key of "take the report's pictures from this duty" (a per-viewer choice the catalog's report button reads). */
export const reportPicturesKey = (die: string, cfg: string): string => `report.pictures:${die}/${cfg}`;

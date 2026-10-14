// Admin · 3D downloads — pure helpers (no runtime imports; node-tested in __tests__/downloads3d.test.mjs).

export interface NdaBasis {
  type: 'online' | 'admin' | 'internal';
  ref: string; label?: string; version?: string; record_id?: string; text_sha256?: string | null;
  company?: string; signatory?: string; set_by?: string; date?: string;
}
export interface DownloadRow {
  download_id?: string; iso: string; user: string; email?: string; name?: string; role?: string; company?: string;
  nda?: boolean; nda_basis?: NdaBasis; die: string; config: string; motor_id: string; kind: string; file: string;
  sha256: string; bytes: number; ip?: string; user_agent?: string;
}

const ID_RE = /\bd[0-9]{8}-[0-9a-f]{12}\b/g;

/** The download id(s) written into a STEP header (the text before ENDSEC; the first 256 KB of a file is enough). */
export function idsInHeader(text: string): string[] {
  const head = text.includes('ENDSEC;') ? text.slice(0, text.indexOf('ENDSEC;')) : text;
  return Array.from(new Set(head.match(ID_RE) ?? []));
}

/** What the trace tool sends for a file: the id found in its header plus its sha256 (the file itself never leaves the browser). */
export function traceText(headerText: string, sha256: string): string {
  return [...idsInHeader(headerText), sha256].join('\n');
}

/** "v1.0" / "admin flag 2026-10-01" / "internal" for the NDA column. */
export function ndaLabel(r: DownloadRow): string {
  const b = r.nda_basis;
  if (!b) return r.nda === false ? 'internal' : '—';
  if (b.type === 'online') return b.version ?? b.ref;
  if (b.type === 'admin') return `admin flag ${b.date ?? ''}`.trim();
  return 'internal';
}

export const shortHash = (h: string): string => (h ? `${h.slice(0, 10)}…` : '');

export async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(d)).map((x) => x.toString(16).padStart(2, '0')).join('');
}

/**
 * The name a New / Duplicate / Save-as-new die dialog opens with (editable there):
 * "<family base> <your suffix> <n>" - asked from the server, which knows the account and
 * which names are taken (`GET /api/family/die_name`, rule in `dieNameRule.ts`).
 *
 * Never throws and never returns a taken-looking empty name when the source is known: if
 * the call fails the dialog still opens, with the family base to edit.
 */
import { familyBase } from './dieNameRule';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

export async function suggestDieName(source?: string | null): Promise<string> {
  const src = String(source ?? '').trim();
  try {
    const q = src ? `?source=${encodeURIComponent(src)}` : '';
    const r = await fetch(`${API}/api/family/die_name${q}`, { cache: 'no-store' });
    if (r.ok) {
      const j = await r.json();
      if (j && typeof j.name === 'string' && j.name.trim()) return j.name;
    }
  } catch { /* offline / not signed in: fall through to the local base */ }
  return familyBase(src);
}

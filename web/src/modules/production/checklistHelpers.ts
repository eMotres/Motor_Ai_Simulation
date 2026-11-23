// Pure types and helpers of the mass-production checklist (no network, no browser): tested under node --test.
export const CHECKLIST_STATUSES = ['to_do', 'to_confirm', 'missing_info', 'optional', 'done'] as const;
export type ChecklistStatus = typeof CHECKLIST_STATUSES[number];
export const CHECKLIST_CATEGORIES = ['assembly', 'checks', 'process', 'tests', 'parts', 'fasteners', 'packaging', 'discussion', 'certification'] as const;
export type ChecklistCategory = typeof CHECKLIST_CATEGORIES[number];

export interface ChecklistEvidence { doc_id: string | null; note: string; name: string | null; file_id: string | null; hidden: boolean }
export interface ChecklistItem {
  key: string; no: number; category: ChecklistCategory;
  item: { en: string; zh: string }; requirement: { en: string; zh: string }; how: { en: string; zh: string };
  responsible: 'factory' | 'supplier' | 'MOTRES'; source: string; revision: string; revision_date: string | null;
  status: ChecklistStatus; changed: boolean; previous_status: ChecklistStatus | null; open: boolean;
  evidence: ChecklistEvidence | null; by: string | null; at: string | null;
}
export interface ChecklistData {
  die: string; config: string; revision: { number: string; date: string | null }; items: ChecklistItem[]; can_edit: boolean;
  stats: { total: number; open: number; by_status: Record<ChecklistStatus, number>; done_pct: number };
}

/** One side of a bilingual value: the viewer's language, else the other one. */
export const pick = (b: { en: string; zh: string }, lang: 'en' | 'zh'): string => (lang === 'zh' ? b.zh || b.en : b.en || b.zh);

/** The items the panel shows: all, or the open ones only; grouped by category in the server's order. */
export function groupItems(items: ChecklistItem[], openOnly: boolean): Array<{ category: ChecklistCategory; items: ChecklistItem[] }> {
  const out: Array<{ category: ChecklistCategory; items: ChecklistItem[] }> = [];
  for (const it of items) {
    if (openOnly && !it.open) continue;
    const last = out[out.length - 1];
    if (last && last.category === it.category) last.items.push(it); else out.push({ category: it.category, items: [it] });
  }
  return out;
}

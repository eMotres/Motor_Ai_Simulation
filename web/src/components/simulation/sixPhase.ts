/**
 * Six-phase winding (owner 2026-09-28): 3 phases | 6 phases = two in-phase
 * 3-phase sets.  The parallel paths are split between the sets; both sets sit
 * in the same phase belts (0 deg), each is star or delta as the duty's Y/Δ
 * says and each has its own inverter.  Pure helpers — the panel and the node
 * test (__tests__/sixPhase.test.mjs, a verbatim copy) share them.
 */

export interface SixPhaseWinding {
  phases?: number;
  set1_paths?: string;
  set_neutrals?: string;
  n_parallel?: number;
  six_phase?: {
    set1_paths: number[]; set2_paths: number[]; paths_per_set: number;
    neutrals: string; note?: string;
  };
  six_phase_error?: string;
}

export interface SixPhaseResult {
  set1_paths?: number[] | null;
  set2_paths?: number[] | null;
  sets?: { set: number; I_phase_rms_A?: number | null; V1_phase_peak_V?: number | null }[];
  inductances?: {
    Ld_set_mH?: number; Lq_set_mH?: number; Lxy_mH?: number;
    Lxy_min_mH?: number; Lxy_max_mH?: number; Lxy_pct_of_Ld?: number | null;
  };
  inductances_error?: string;
  inductances_note?: string;
}

/** Loud, before anything is sent: 6 phases need an even number of paths. */
export function sixPhaseProblem(nParallel: number): string | null {
  const n = Math.round(Number(nParallel) || 0);
  if (n < 2 || n % 2) {
    return `6 phases split the parallel paths into two sets — this connection has ${n} path${n === 1 ? '' : 's'}; pick one with an even number (e.g. 2S-2P)`;
  }
  return null;
}

/** "1, 3" → "1,3"; anything that is not a list of path numbers → null. */
export function normalizeSet1Paths(text: string): string | null {
  const t = String(text ?? '').trim();
  if (t === '') return '';
  const parts = t.split(/[\s,;]+/).filter(Boolean);
  if (!parts.every(p => /^\d+$/.test(p))) return null;
  return Array.from(new Set(parts.map(Number))).sort((a, b) => a - b).join(',');
}

/** One line under the Phases buttons: the sets, or why they cannot be wired. */
export function sixPhaseLine(w: SixPhaseWinding | null | undefined): string {
  if (!w || Number(w.phases ?? 3) !== 6) return '';
  if (w.six_phase_error) return `⚠ ${w.six_phase_error}`;
  const s = w.six_phase;
  if (!s) return '';
  return `set 1: path ${s.set1_paths.join(', ')} · set 2: path ${s.set2_paths.join(', ')} · in phase · neutrals ${s.neutrals}`;
}

const f = (v: number | null | undefined, d: number) =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(d);

/** The result cells' values: L_xy and the per-set current / voltage. */
export function sixPhaseCells(r: SixPhaseResult | null | undefined):
    { lxy: string; lxyPct: string; perSet: string } | null {
  if (!r) return null;
  const ind = r.inductances;
  const s1 = (r.sets ?? []).find(s => s.set === 1);
  return {
    lxy: ind?.Lxy_mH != null ? f(ind.Lxy_mH, 4) : '—',
    lxyPct: ind?.Lxy_pct_of_Ld != null ? `${f(ind.Lxy_pct_of_Ld, 1)} % of L_d,set` : '',
    perSet: s1 ? `${f(s1.I_phase_rms_A, 1)} A · ${f(s1.V1_phase_peak_V, 1)} V` : '—',
  };
}

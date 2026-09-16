/**
 * The report button's progress ring — the pure half.
 *
 * User, 2026-09-16: *"нужно сделать ещё минимальный прогресс-ринг генерации
 * отчёта, чтобы было видно, что работает, а не висит"*.  A Word report of a
 * 200 mm machine is about fifty seconds of matplotlib and the PDF half a
 * minute more; until now the row showed "… report" for all of it, which reads
 * as a server that died.
 *
 * The backend publishes that build into the SAME progress registry every solve
 * reports into (`src/motor_ai_sim/report_progress.py`), keyed by a run id this
 * module mints, and `GET /api/family/report/progress?run_id=…` answers the
 * usual snapshot plus `stage` / `done` / `error`.
 *
 * Only the RULES live here, not the fetching: which of the three faces the
 * ring wears, what the tooltip says, and the one clamp that matters — a
 * percentage that never goes backwards.  That is what `node --test` can
 * exercise (`__tests__/reportProgress.test.mjs`); a React component cannot be.
 *
 * THE STATE MACHINE
 *   idle          nothing clicked
 *   indeterminate clicked, no answer yet — `pct === null`.  The ring SPINS
 *                 within a frame of the click; waiting for the first poll to
 *                 draw anything would put a second of nothing where the user
 *                 pressed, which is the bug in miniature.
 *   determinate   the backend answered a fraction — `pct` 0…100
 *   error         the download or the build failed; the ring becomes the
 *                 run-notice line (`lib/runNotice`), one sentence, full text
 *                 in the tooltip
 *   idle          again, once the file has arrived
 */
import { runNoticeFor } from '../../lib/runNotice';
import type { RunNotice } from '../../lib/runNotice';

/** What `GET /api/family/report/progress` answers (the solve snapshot + 3). */
export interface ReportProgressInfo {
  running?: boolean;
  step?: number;
  total?: number;
  frac?: number;
  elapsed_s?: number;
  phase?: string;
  /** 'records' | 'figures' | 'tables' | 'docx' | 'pdf' | 'done' | 'failed'
   *  | 'unknown' (an id the server has not seen yet — the normal first poll) */
  stage?: string;
  done?: boolean;
  error?: string | null;
  run_id?: string;
  format?: string;
}

export interface ReportRing {
  /** The build this ring is about; `null` when nothing is running. */
  runId: string | null;
  /** Which button pressed it — `report:<die>/<cfg>`, pdf variants included. */
  key: string;
  /** 0…100, or `null` for "moving, but nothing has said how far yet". */
  pct: number | null;
  stage: string;
  /** One short line: what the server is doing right now. */
  label: string;
  /** Seconds since the click — the client's own clock, so it advances between
   *  polls and keeps advancing if a poll is lost. */
  elapsedS: number;
  /** Set once, and then the ring is a notice rather than a ring. */
  notice: RunNotice | null;
}

export const IDLE: ReportRing = {
  runId: null, key: '', pct: null, stage: '', label: '', elapsedS: 0,
  notice: null,
};

/** One short line per stage — the tooltip, and nothing longer. */
const STAGE_WORDS: Record<string, string> = {
  records: 'reading stored results',
  figures: 'drawing figures',
  tables: 'tables',
  docx: 'writing the Word file',
  pdf: 'typesetting the PDF',
  done: 'done',
  failed: 'failed',
  unknown: 'starting…',
};

/**
 * A fresh run id.  `crypto.randomUUID` where it exists (every browser the app
 * supports over https), and a plain random stem where it does not — this is a
 * nonce for a progress bar, not a secret, and a build that cannot mint one
 * would otherwise lose its ring for no good reason.
 */
export function newRunId(): string {
  try {
    const c: any = (globalThis as any).crypto;
    if (c?.randomUUID) return `rep-${c.randomUUID()}`;
  } catch { /* fall through */ }
  return `rep-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** The click: the ring appears immediately, indeterminate. */
export function ringStart(key: string, runId: string): ReportRing {
  return { runId, key, pct: null, stage: 'unknown', label: STAGE_WORDS.unknown,
    elapsedS: 0, notice: null };
}

/**
 * One poll answer, folded in.
 *
 * `elapsedS` is the CLIENT's clock (`sinceS`), not the server's: it has to
 * keep moving between polls, and a dropped poll must not freeze the only
 * number on screen that proves the page is alive.
 *
 * The percentage never decreases within one run.  The backend's own total can
 * legitimately grow mid-build (a machine with more figures than the last one),
 * and a ring that slides back two notches is read as a restart.
 */
export function ringPoll(prev: ReportRing, p: ReportProgressInfo | null,
                         sinceS: number): ReportRing {
  if (prev.runId == null) return prev;            // finished/cancelled meanwhile
  const next: ReportRing = { ...prev, elapsedS: Math.max(0, sinceS) };
  if (!p) return next;                            // a lost poll is not an error
  if (p.error) return ringFail(prev, String(p.error));

  const stage = String(p.stage || '') || prev.stage;
  next.stage = stage;
  next.label = p.phase && stage === 'figures'
    ? String(p.phase)                             // "figure 7 / 20"
    : (STAGE_WORDS[stage] ?? String(p.phase || '') ?? '');

  if (p.done) { next.pct = 100; return next; }
  // An id the server has not opened yet, or a bar with no completed step: the
  // ring keeps spinning rather than snapping to 0 %.
  if (stage === 'unknown') return next;
  const frac = typeof p.frac === 'number' ? p.frac
    : (p.total ? (p.step ?? 0) / p.total : NaN);
  if (!Number.isFinite(frac)) return next;
  const pct = Math.max(0, Math.min(100, Math.round(frac * 100)));
  next.pct = prev.pct == null ? pct : Math.max(prev.pct, pct);
  return next;
}

/** The build (or the download) failed: the ring becomes one short sentence. */
export function ringFail(prev: ReportRing, message: string): ReportRing {
  return { ...prev, runId: null, pct: null, stage: 'failed',
    label: STAGE_WORDS.failed,
    notice: runNoticeFor(message) ?? { text: 'Report failed', kind: 'error',
      full: String(message) } };
}

/** The file has arrived: back to idle, and the button gets its caption back. */
export function ringDone(_prev: ReportRing): ReportRing {
  return IDLE;
}

/** Is this button the one with a build behind it? */
export function ringBusy(r: ReportRing, key: string): boolean {
  return r.runId != null && r.key === key;
}

/**
 * The tooltip: the stage and how long it has been going.  One line — the whole
 * point of the ring is that it needs no paragraph beside it.
 */
export function ringTip(r: ReportRing): string {
  if (r.notice) return r.notice.full;
  if (r.runId == null) return '';
  const secs = `${Math.round(r.elapsedS)} s`;
  const pct = r.pct == null ? '' : ` · ${r.pct} %`;
  return `${r.label || 'working'}${pct} · ${secs}`;
}

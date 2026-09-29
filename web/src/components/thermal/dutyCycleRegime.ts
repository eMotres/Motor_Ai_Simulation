/**
 * THE FOUND REGIME — what the duty-cycle editor says now (2026-09-15).
 *
 * The tool used to GRADE a duty ratio the user typed: "you asked for 25 %, the
 * class allows 21.6 %".  The user's reframe is the other way round — «с помощью
 * Duty cycle мы можем подобрать такой режим работы мотора, чтобы он смог
 * уложиться в температурные лимиты — то есть мы сами находим это время / S3 ED,
 * при котором всё нормально» — so the ANSWER is the regime, and the typed ratio
 * became an optional check beside it.
 *
 * This module is the panel's arithmetic and its wording, with no React in it:
 *
 *   • `regimeLine` — the headline, one short line (standing UI rule: one line
 *     plus a tooltip, never a wall of text);
 *   • `edCycleRows` — the ED-vs-cycle-length chart's rows;
 *   • `checkVerdict` — the pass/fail chip, which exists ONLY when the optional
 *     "check ED %" field was filled;
 *   • `calibrationIssue` — the fitted-at guard.  The network's every
 *     conductance comes from ONE steady map, and the steady map of an IMPULSE
 *     duty is a temperature the machine never reaches: fitting there is not a
 *     conservative choice, it is a different machine.  A peak-looking duty is
 *     refused by this editor before the backend has to answer it; anything else
 *     that is not the rated duty is warned about and allowed.
 *
 * Dependency-free on purpose — no React, no `import.meta.env` — so node's own
 * type stripping can run the test beside it (see `dutyCycleTorque.ts`).
 */

/** The `limits` block of a cycle answer — the fields this module reads. */
export interface RegimeLimits {
  winding_limit_c?: number | null;
  magnet_limit_c?: number | null;
  ed_allowable_pct?: number | null;
  ed_requested_pct?: number | null;
  ed_limiting_part?: string | null;
  ed_found?: boolean;
  ed_cycle_s?: number | null;
  at_allowable?: {
    winding_hot_peak_c?: number | null;
    magnet_peak_c?: number | null;
    peak_c?: Record<string, number | null>;
    mean_c?: Record<string, number | null>;
  } | null;
  ed_vs_cycle?: {
    cycle_s?: number | null;
    ed_allowable_pct?: number | null;
    t_on_s?: number | null;
    limiting_part?: string | null;
    winding_hot_peak_c?: number | null;
    magnet_peak_c?: number | null;
  }[];
  s2_time_to_limit_s?: number | null;
  s2_limiting_part?: string | null;
  s2_from_rated_s?: number | null;
  s2_from_cycle_mean_s?: number | null;
}

const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') {
    return null;
  }
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
};

/** `n` at `d` decimals, with the trailing zeros of an integer dropped. */
function g(v: number | null, d = 1): string {
  if (v === null) return '—';
  const s = v.toFixed(d);
  return s.replace(/\.0+$/, '');
}

/** THE HEADLINE.  One line, and it leads with the regime that was FOUND.
 *
 *  "Allowable ED at 60 s: 21.6 % (limit: winding 200 °C) · S2 from cold 26.6 s
 *   · from rated 20.7 s · magnets at the allowable point 111 °C"
 *
 *  A number this machine does not have is left OUT of the line rather than
 *  printed as a dash: the line is what the user reads at a glance, and "—" in
 *  it reads as a failure when it is usually just a limit nobody set. */
export function regimeLine(lim: RegimeLimits | null | undefined): string {
  if (!lim) return '';
  const parts: string[] = [];
  const ed = num(lim.ed_allowable_pct);
  const cyc = num(lim.ed_cycle_s);
  if (ed !== null) {
    const part = String(lim.ed_limiting_part ?? '').trim();
    const limC = num(part === 'magnet' ? lim.magnet_limit_c
                                       : lim.winding_limit_c);
    const why = part ? ` (limit: ${part}${limC !== null ? ` ${g(limC, 0)} °C`
                                                        : ''})` : '';
    parts.push(cyc !== null
      ? `Allowable ED at ${g(cyc)} s: ${g(ed)} %${why}`
      : `Allowable ED: ${g(ed)} %${why}`);
  }
  const cold = num(lim.s2_time_to_limit_s);
  if (cold !== null) parts.push(`S2 from cold ${g(cold)} s`);
  const rated = num(lim.s2_from_rated_s);
  if (rated !== null) parts.push(`from rated ${g(rated)} s`);
  const mag = num(lim.at_allowable?.magnet_peak_c);
  if (mag !== null) parts.push(`magnets at the allowable point ${g(mag, 0)} °C`);
  return parts.join(' · ');
}

/** One row of the ED-vs-cycle-length chart. */
export interface EdCycleRow {
  cycleS: number;
  edPct: number;
  tOnS: number | null;
  windingC: number | null;
  magnetC: number | null;
  limitingPart: string | null;
}

/** The chart's rows, in cycle-length order.
 *
 *  A period with NO feasible duty ratio is dropped rather than drawn at zero:
 *  a point on the floor of an ED axis reads as "0 % is allowed", which is the
 *  opposite of "nothing is allowed here". */
export function edCycleRows(lim: RegimeLimits | null | undefined): EdCycleRow[] {
  const raw = lim?.ed_vs_cycle;
  if (!Array.isArray(raw)) return [];
  const out: EdCycleRow[] = [];
  for (const r of raw) {
    const cycleS = num(r?.cycle_s);
    const edPct = num(r?.ed_allowable_pct);
    if (cycleS === null || edPct === null) continue;
    out.push({
      cycleS,
      edPct,
      tOnS: num(r?.t_on_s),
      windingC: num(r?.winding_hot_peak_c),
      magnetC: num(r?.magnet_peak_c),
      limitingPart: (r?.limiting_part ?? null) as string | null,
    });
  }
  return out.sort((a, b) => a.cycleS - b.cycleS);
}

export interface CheckVerdict {
  ok: boolean;
  /** the chip's own words */
  text: string;
  askedPct: number;
  allowedPct: number | null;
}

/** THE OPTIONAL CHECK.  `null` unless the user filled "check ED %" — the whole
 *  point of the reframe is that a pass/fail verdict is something you ASK for,
 *  not the shape every answer has. */
export function checkVerdict(lim: RegimeLimits | null | undefined):
    CheckVerdict | null {
  const asked = num(lim?.ed_requested_pct);
  if (asked === null) return null;
  const allowed = num(lim?.ed_allowable_pct);
  if (allowed === null) {
    return { ok: false, text: `ED ${g(asked)} % asked — not judged`,
             askedPct: asked, allowedPct: null };
  }
  const ok = asked <= allowed + 1e-9;
  return {
    ok,
    text: ok
      ? `✓ ED ${g(asked)} % fits — ${g(allowed)} % allowed`
      : `✗ ED ${g(asked)} % over — ${g(allowed)} % allowed`,
    askedPct: asked,
    allowedPct: allowed,
  };
}

export interface CalibrationIssue {
  level: 'ok' | 'warn' | 'refuse';
  text: string;
}

/** Duties whose steady map is a temperature the machine NEVER sits at.
 *  Matched on the name because that is what the catalogue has: this project
 *  names its points, and «peak 200C wire 120C NdFeB» is not a state. */
const IMPULSE_RE = /(^|[^a-z])(peak|impulse|impuls|burst|boost|overload|surge)/i;

/** THE FITTED-AT GUARD.  Every conductance in the lumped network is divided out
 *  of ONE steady map, so the duty that map belongs to decides every number
 *  below it.
 *
 *    • the rated duty (or the backend's own default) — fine;
 *    • a PEAK / impulse duty — refused: its steady map is the machine after the
 *      peak has run for ever, which is a machine that would have burned; the
 *      conductances fitted there are fitted at 600 °C;
 *    • anything else — allowed, with the choice said out loud.
 */
export function calibrationIssue(picked: string | null | undefined,
                                 rated: string | null | undefined):
    CalibrationIssue {
  const name = String(picked ?? '').trim();
  if (!name || name === String(rated ?? '').trim()) {
    return { level: 'ok', text: '' };
  }
  if (IMPULSE_RE.test(name)) {
    return {
      level: 'refuse',
      text: `“${name}” is an impulse point — its steady map is a temperature `
        + 'this machine never reaches, so the conductances fitted to it are '
        + 'not this machine’s. Fit at the rated duty.',
    };
  }
  return {
    level: 'warn',
    text: `fitted at “${name}”, not the rated duty — every conductance in the `
      + 'network comes from that one map.',
  };
}

/* ── WHAT THE RUN BUTTON WILL DO, in the kind that is chosen ────────────────
   User 2026-09-16: «Нужен правильный алгоритм расчёта — что и когда нажимать.
   Если выбран S1 — идёт нормальный каплинг; если выбран S3 — по умолчанию идёт
   оптимизация времени импульса.»  The flow was real in the backend since
   `coupled: an S2/S3 duty is solved for its REGIME` and invisible in the UI:
   the kind picker sat in a panel that said nothing about the Run button three
   panels away.  One line under the picker says it, and the two sentences
   behind the ⓘ say why — the standing no-walls-of-text rule. */

/** The kinds this editor OFFERS.  S2 went out with the same message (user:
 *  «S2, я думаю, нужно выбросить, не знаю ему пока применения») — one pull is
 *  what the S3 answer already reports as "S2 from cold", so choosing it was
 *  asking for a number you were being given anyway. */
export const OFFERED_KINDS = ['S1', 'S3'] as const;

export interface RunModeText {
  /** the one line under the picker */
  line: string;
  /** the ⓘ beside it — two sentences, never more */
  tip: string;
}

/** What the COUPLED Run does in this kind.  `null` for a kind the editor no
 *  longer offers: a stored S2 or segment list is read, not run from here. */
export function runModeLine(kind: string | null | undefined): RunModeText | null {
  const k = String(kind ?? '').trim();
  if (k === 'S1') {
    return {
      line: 'Run → the coupled loop, iterated until the temperatures settle.',
      tip: 'S1 is a point the machine sits at, so the coupled Run iterates the '
        + 'electromagnetic solve and the thermal solve until neither moves and '
        + 'reports the machine there. Nothing about a cycle is searched, and '
        + 'nothing cycle-related is printed.',
    };
  }
  if (k === 'S3') {
    return {
      line: 'Run → the coupled loop, and inside it the allowable ED is searched.',
      tip: 'S3 is a ratio, not a point: every pass of the coupled loop searches '
        + 'the duty ratio the limits allow at the temperatures that pass '
        + 'reached, and feeds those temperatures back into the next one. The '
        + 'Run comes back with the allowable ED — and says "asked for more" '
        + 'when the ED this duty stores does not fit under it.',
    };
  }
  return null;
}

/** A kind that is STORED but no longer offered — read, never chosen again. */
export function retiredKindNote(kind: string | null | undefined): string | null {
  const k = String(kind ?? '').trim();
  if (k === 'S2') {
    return 'S2 is no longer offered; switch to S1 or S3';
  }
  if (k === 'segments') {
    return 'a segment list is no longer offered; switch to S1 or S3';
  }
  return null;
}

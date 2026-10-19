/**
 * SHORT-TIME OR CONTINUOUS — the web half of `motor_ai_sim/duty_class.py`
 * (owner 2026-10-19: «помечай как кратковременный»).  The class itself is
 * decided on the server from the duty's stored coupled record; these helpers
 * only NAME it, so the strip, its dropdown and the catalog row print the same
 * words the datasheet does.
 */

export interface DutyClassInfo {
  duty_class?: 'short_time' | 'continuous' | string | null;
  /** "rated — short-time: 6 min 11 s from cold (winding 200 °C)" */
  duty_label?: string | null;
  short_time?: {
    time_to_limit_s?: number | null;
    time_from_rated_s?: number | null;
    part?: string | null;
    limit_c?: number | null;
    /** "short-time: 6 min 11 s from cold (winding 200 °C)" */
    words?: string | null;
  } | null;
}

/** "6 min 11 s", "45 s", "2 h 05 min" — `duty_class.fmt_duration`. */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—';
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} min`;
}

export function isShortTime(info: DutyClassInfo | null | undefined): boolean {
  return info?.duty_class === 'short_time';
}

/** The duty as every page names it — the server's label when it sent one. */
export function dutyLabel(name: string, info: DutyClassInfo | null | undefined): string {
  if (!isShortTime(info)) return name;
  if (info?.duty_label) return info.duty_label;
  const st = info?.short_time;
  if (st?.time_to_limit_s == null) return `${name} — short-time`;
  const tail = st.part ? ` (${st.part}${st.limit_c != null ? ` ${st.limit_c} °C` : ''})` : '';
  return `${name} — short-time: ${fmtDuration(st.time_to_limit_s)} from cold${tail}`;
}

/** The short chip a dense row has room for: "short-time 6 min 11 s". */
export function shortTimeChip(info: DutyClassInfo | null | undefined): string | null {
  if (!isShortTime(info)) return null;
  const t = info?.short_time?.time_to_limit_s;
  return t == null ? 'short-time' : `short-time ${fmtDuration(t)}`;
}

/** What "Save continuous point as duty" proposes for the loaded duty `duty`:
 *  the new duty takes the source's NAME when the source is short-time (so
 *  "rated" becomes the continuous one) and the source is renamed
 *  `<duty>_short_time`; otherwise the new duty is `<duty>_continuous` and
 *  nothing is renamed.  The dialog shows both before anything is sent. */
export function continuousSavePlan(duty: string, info: DutyClassInfo | null | undefined,
    name?: string | null): { name: string; renameSourceTo: string | null } {
  const def = isShortTime(info) ? duty : `${duty}_continuous`;
  const n = (name ?? '').trim() || def;
  return { name: n, renameSourceTo: n === duty ? `${duty}_short_time` : null };
}

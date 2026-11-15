/**
 * Generator mode shows the torque as a positive magnitude (owner 2026-11-15): the duty table already reads positive,
 * the mode itself says it is the braking torque, so the Transient plot and its T_avg must not contradict the table with
 * a minus sign.  DISPLAY only - the stored result keeps the solver's sign.  Pure: `node --test` imports it as is.
 */
export function torqueDisplaySign(opMode: unknown, tAvgNm: number | null | undefined): 1 | -1 {
  return opMode === 'generator' && Number(tAvgNm) < 0 ? -1 : 1;
}

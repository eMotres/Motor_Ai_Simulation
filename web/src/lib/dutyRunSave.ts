/** Keep the computed snapshot out of the preliminary duty-definition request. */
export function dutyDefinitionOnly(duty: Record<string, unknown>): Record<string, unknown> {
  const { summary: _summary, mesh: _mesh, ...definition } = duty;
  return definition;
}

/** One request owns summary + result; waveforms are optional and omit unused field arrays. */
export function atomicDutyRunBody(
  run: { die: string; config: string; duty: string; drive: string;
    summary: unknown; result: Record<string, unknown>; settings?: unknown; assignment_sig?: string },
  transient?: Record<string, unknown> | null,
): Record<string, unknown> {
  if (!transient) return { ...run };
  const heavy = new Set(['frames', 'field', 'demag_field', 'demag_coef_per_tri']);
  const payload = Object.fromEntries(Object.entries(transient).filter(([key]) => !heavy.has(key)));
  return { ...run, payload };
}

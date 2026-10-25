// The read-only view of what is STORED for a motor, reached the way the Motors tab reaches a motor (die / configuration,
// no project): GET /api/motor-view/{die}/{cfg}[/{duty}/field/{kind}].  The project twin is developmentApi.motorView.
// One answer shape (modules/development/types MotorView / StoredField), so a motor shows the same wherever it was opened.
import type { FieldKind, MotorView, StoredField } from '../modules/development/types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const enc = encodeURIComponent;

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: 'no-store' });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${r.status}`);
  }
  return data as T;
}

export const fetchMotorView = (die: string, config: string) =>
  get<MotorView>(`/api/motor-view/${enc(die)}/${enc(config)}`);

export const fetchMotorField = (die: string, config: string, duty: string, kind: FieldKind) =>
  get<StoredField>(`/api/motor-view/${enc(die)}/${enc(config)}/${enc(duty)}/field/${kind}`);

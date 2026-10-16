// Translating an API error body (docs/I18N.md).  The backend adds a stable
// `code` + `params` beside the unchanged English `detail`
// (src/motor_ai_sim/api_errors.py); a code this build knows is translated, an
// unknown one shows the English detail, exactly as before i18n.
import type { TFunction } from 'i18next';
import { formatApiErrorDetail } from '../lib/apiErrorDetail';

export function translateApiError(t: TFunction, body: unknown): string {
  const b = (body && typeof body === 'object') ? body as Record<string, unknown> : {};
  const english = formatApiErrorDetail(b.detail ?? body);
  const code = typeof b.code === 'string' ? b.code : null;
  if (!code || code.startsWith('http.')) return english;
  const params = (b.params && typeof b.params === 'object') ? b.params as Record<string, unknown> : {};
  return t(code, { ns: 'errors', ...params, defaultValue: english }) as string;
}

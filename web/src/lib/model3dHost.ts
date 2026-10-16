import { glbRoute, type ModelInfo } from './model3d';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');
export const modelBase = (m: Pick<ModelInfo, 'die' | 'config'>): string => `${API}/api/model3d/${encodeURIComponent(m.die)}/${encodeURIComponent(m.config)}`;
export const modelGlbUrl = (m: ModelInfo | null, canDownload: boolean): string | null => {
  const route = glbRoute(m, canDownload);
  return m && route ? `${modelBase(m)}/${route}` : null;
};
export async function responseDetail(response: Response): Promise<string> {
  try { const body = await response.json(); return typeof body?.detail === 'string' ? body.detail : JSON.stringify(body?.detail ?? body); }
  catch { return String(response.status); }
}

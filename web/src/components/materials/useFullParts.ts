/**
 * Hook: the whole motor, part by part (Materials tab "Full").
 *
 * The list comes from GET /api/family/die/{die}/part_materials (the active parts + the CAD part list).  A choice for a
 * non-active part is saved with the DIE (PUT, die.yaml `part_materials`) under the same permission rule as the active
 * material assignment: the owner (admin / local dev) writes the shared catalog, an ordinary signed-in account on an
 * enforced backend keeps a client-side overlay (localStorage) merged over the server rows - one user's choice never
 * mutates a shared die.  Nothing here touches the electromagnetic assignment.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth, useApiReady } from '../../contexts/AuthContext';
import { mergeOverlay, overlayKey, type FullPartRow, type FullPartsPayload } from './fullPartsLogic';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') + '/api/family/die';

function readOverlay(die: string): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(overlayKey(die)) || '{}') as Record<string, string>; }
  catch { return {}; }
}

export function useFullParts(die: string | null, config: string | null, enabled: boolean) {
  const { isAdmin, enforced } = useAuth();
  const ready = useApiReady();
  const localMode = enforced && !isAdmin;

  const [payload, setPayload] = useState<FullPartsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overlay, setOverlay] = useState<Record<string, string>>({});

  const refresh = useCallback(() => {
    if (!ready || !enabled || !die) return;
    setLoading(true);
    const q = config ? `?config=${encodeURIComponent(config)}` : '';
    fetch(`${API}/${encodeURIComponent(die)}/part_materials${q}`, { cache: 'no-store' })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<FullPartsPayload>;
      })
      .then((p) => { setPayload(p); setError(null); setLoading(false); })
      .catch((e) => { setPayload(null); setError(String(e)); setLoading(false); });
  }, [ready, enabled, die, config]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { setOverlay(die ? readOverlay(die) : {}); }, [die]);

  /** Choose a material for a non-active part; `null` returns it to the documented default. */
  const choose = useCallback((part: string, material: string | null) => {
    if (!die) return;
    setSaving(true);
    setError(null);
    (async () => {
      try {
        if (localMode) {
          const cur = readOverlay(die);
          if (material) cur[part] = material; else delete cur[part];
          try { localStorage.setItem(overlayKey(die), JSON.stringify(cur)); } catch { /* quota */ }
          setOverlay(cur);
        } else {
          const r = await fetch(`${API}/${encodeURIComponent(die)}/part_materials`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ part, material }),
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          refresh();
        }
        setSaving(false);
      } catch (e) { setError(String(e)); setSaving(false); }
    })();
  }, [die, localMode, refresh]);

  const rows: FullPartRow[] = useMemo(() => {
    if (!payload?.available) return [];
    const dens = new Map((payload.choices ?? []).map((c) => [c.id, c.density_kg_m3]));
    return mergeOverlay(payload.parts, localMode ? overlay : {}, (id) => dens.get(id) ?? null);
  }, [payload, overlay, localMode]);

  return { payload, rows, loading, saving, error, choose, refresh, localMode };
}

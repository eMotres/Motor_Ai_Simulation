/**
 * Hook: the flat-wire stock table (`GET /api/wires/stock`).
 *
 * A plain, single fetch — this is a small reference table (a few dozen rows),
 * not the materials library's merge-of-three-layers, so it does not need
 * `useMaterialsLibrary`'s bounded-retry machinery. It still waits for
 * `useApiReady` for the same reason that hook does: mounted at the Materials
 * tab, a fetch fired before `/api/me` answers on a fresh sign-in races the
 * auth header and comes back 401.
 */
import { useCallback, useEffect, useState } from 'react';
import { useApiReady } from '../../contexts/AuthContext';
import type { WireStockResponse } from '../../lib/wireStock';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export function useWireStock() {
  const ready = useApiReady();
  const [data, setData] = useState<WireStockResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    fetch(`${API}/api/wires/stock`, { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((j: WireStockResponse) => { setData(j); setError(null); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, []);

  useEffect(() => { if (ready) reload(); }, [ready, reload]);

  return { data, loading, error, reload };
}

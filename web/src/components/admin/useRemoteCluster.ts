// Admin · two regions: the other server's cluster / load payload, fetched live through this server (never stored).
// `null` while loading or on a failed request; `configured: false` in single-region mode.
import { useEffect, useState } from 'react';
import type { RemoteCluster } from './regions';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

export const useRemoteCluster = (pollMs = 15_000): RemoteCluster | null => {
  const [data, setData] = useState<RemoteCluster | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => {
      if (document.visibilityState === 'hidden') return;
      fetch(`${API}/api/admin/regions/remote/cluster`)
        .then((r) => (r.ok ? r.json() : null))
        .then((j: RemoteCluster | null) => { if (live) setData(j); })
        .catch(() => { if (live) setData(null); });
    };
    load();
    const t = window.setInterval(load, pollMs);
    return () => { live = false; window.clearInterval(t); };
  }, [pollMs]);
  return data;
};

// Admin · two regions: {email: region} for the Projects and Verification tables (local accounts + the global directory).
// A failed load is an empty map: every row then reads EU, the single-region truth.
import { useEffect, useState } from 'react';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');

export const useRegionMap = (): Record<string, string> => {
  const [map, setMap] = useState<Record<string, string>>({});
  useEffect(() => {
    let live = true;
    fetch(`${API}/api/admin/regions/accounts`)
      .then((r) => (r.ok ? r.json() : { accounts: {} }))
      .then((j: { accounts?: Record<string, string> }) => { if (live) setMap(j.accounts ?? {}); })
      .catch(() => { /* empty map = EU everywhere */ });
    return () => { live = false; };
  }, []);
  return map;
};

import { useEffect } from 'react';
import { useUIStore } from '../../stores/motorStore';
import { getStoredToken } from '../../lib/localAuth';
import { startActivityBeat } from '../../lib/activityBeat';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

/**
 * Working-time heartbeat (lib/activityBeat.ts).  Mount ONLY for a signed-in user: it renders
 * nothing and sends nothing but the tab id and a random per-page-load session id.
 */
export default function ActivityBeat(): null {
  useEffect(() => startActivityBeat({
    apiBase: API,
    getTab: () => String(useUIStore.getState().activeTab || 'other'),
    getToken: getStoredToken,
  }), []);
  return null;
}

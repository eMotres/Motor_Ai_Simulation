import { useEffect, useState } from 'react';
import { developmentApi } from './api';
import { createActivityPoller, type ActivityRow } from './projectActivity';

/** Live runs on a project's motors: ONE request per project, every 30 s while the tab is visible (stops when hidden, refreshes
 *  when it comes back).  `null` projectId = not on a project / not on the Motors tab: no requests. */
export function useProjectActivity(projectId: string | null | undefined): ActivityRow[] {
  const [rows, setRows] = useState<ActivityRow[]>([]);
  useEffect(() => {
    setRows([]);
    if (!projectId) return;
    const poller = createActivityPoller({
      fetchRows: () => developmentApi.activity(projectId),
      onRows: setRows,
      isVisible: () => document.visibilityState !== 'hidden',
      onVisibility: (listener) => { document.addEventListener('visibilitychange', listener); return () => document.removeEventListener('visibilitychange', listener); },
      setTimer: (fn, ms) => window.setTimeout(fn, ms),
      clearTimer: (handle) => window.clearTimeout(handle as number),
    });
    poller.start();
    return () => poller.stop();
  }, [projectId]);
  return rows;
}

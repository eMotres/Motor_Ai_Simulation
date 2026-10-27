import { useEffect, useState } from 'react';
import { developmentApi } from './api';
import { createActivityPoller } from './projectActivity';
import { EMPTY_ACTIVITY, readSeen, writeSeen, type ActivityData } from './stageBar';

const storage = (): Storage | null => { try { return window.localStorage; } catch { return null; } };

/** Live runs, queue position / ETA and the five-stage progress of a project's variants: ONE request per project, every 30 s while
 *  the tab is visible (stops when hidden, refreshes when it comes back).  `null` projectId = not on a project page: no requests.
 *  The viewer's last visit (this browser) tells the server which results are new ("Results ready"); it is written when the
 *  project page is left. */
export function useProjectActivity(projectId: string | null | undefined): ActivityData {
  const [data, setData] = useState<ActivityData>(EMPTY_ACTIVITY);
  useEffect(() => {
    setData(EMPTY_ACTIVITY);
    if (!projectId) return;
    const poller = createActivityPoller<ActivityData>({
      fetchRows: () => developmentApi.activity(projectId, readSeen(storage(), projectId)),
      onRows: setData,
      isVisible: () => document.visibilityState !== 'hidden',
      onVisibility: (listener) => { document.addEventListener('visibilitychange', listener); return () => document.removeEventListener('visibilitychange', listener); },
      setTimer: (fn, ms) => window.setTimeout(fn, ms),
      clearTimer: (handle) => window.clearTimeout(handle as number),
    });
    poller.start();
    return () => { poller.stop(); writeSeen(storage(), projectId, Date.now()); };
  }, [projectId]);
  return data;
}

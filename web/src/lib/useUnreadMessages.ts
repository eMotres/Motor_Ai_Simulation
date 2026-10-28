/** React side of `unreadMessages.ts`: the shared counts, and the poller App runs while somebody is signed in. */
import { useEffect, useSyncExternalStore } from 'react';
import { pageVisible } from './pageVisible';
import { getUnread, refreshUnread, resetUnread, subscribeUnread, type UnreadCounts } from './unreadMessages';

/** The latest counts (`null` until the first answer). */
export function useUnreadCounts(): UnreadCounts | null {
  return useSyncExternalStore(subscribeUnread, getUnread, getUnread);
}

const POLL_MS = 60_000;

/** Poll the counts every minute while the page is visible; read at once when it is shown again.  Nothing runs while hidden. */
export function useUnreadPolling(enabled: boolean, identity?: string): void {
  useEffect(() => {
    if (!enabled) { resetUnread(); return; }
    resetUnread();
    const read = () => { if (pageVisible()) void refreshUnread(); };
    read();
    const id = setInterval(read, POLL_MS);
    document.addEventListener('visibilitychange', read);
    window.addEventListener('focus', read);
    return () => { clearInterval(id); document.removeEventListener('visibilitychange', read); window.removeEventListener('focus', read); };
  }, [enabled, identity]);
}

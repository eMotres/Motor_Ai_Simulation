/** React side of the upload queue (lib/uploadQueue.ts holds every rule): runs the queued files through `send`, up to the limit at a time,
 *  keeps the rows in state, aborts a cancelled request and asks before the page is left while a file is on its way. */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as Q from './uploadQueue';
import { UploadAborted } from './uploadXhr';
import type { XhrUploadOptions } from './uploadXhr';

export interface UseUploadQueueOptions<R> {
  /** send one file; report bytes through `tx.onProgress`, stop on `tx.signal` */
  send: (file: File, tx: XhrUploadOptions) => Promise<R>;
  /** one file finished (the list refreshes here) */
  onFileDone?: (file: File, result: R) => void;
  /** the text shown beside a failed row */
  errorText?: (e: unknown) => string;
  limit?: number;
}

export function useUploadQueue<R>(o: UseUploadQueueOptions<R>) {
  const [state, setState] = useState(() => Q.emptyQueue(o.limit));
  const ref = useRef(state);
  const opts = useRef(o);
  opts.current = o;
  const files = useRef(new Map<number, File>());
  const aborts = useRef(new Map<number, AbortController>());

  const apply = useCallback((f: (s: Q.UploadQueueState) => Q.UploadQueueState) => {
    const next = f(ref.current);
    if (next !== ref.current) { ref.current = next; setState(next); }
  }, []);

  const pump = useCallback(() => {
    for (const id of Q.startable(ref.current)) {
      const file = files.current.get(id);
      if (!file) { apply((s) => Q.markFailed(s, id, '')); continue; }
      const ctrl = new AbortController();
      aborts.current.set(id, ctrl);
      apply((s) => Q.markStarted(s, id));
      void (async () => {
        try {
          const result = await opts.current.send(file, { signal: ctrl.signal, onProgress: (loaded, total) => {
            // the body is a little larger than the file (form fields): scale to the file's own size
            apply((s) => { const row = s.items.find((i) => i.id === id); return row ? Q.setProgress(s, id, total > 0 ? Math.min(row.size, Math.round((loaded / total) * row.size)) : loaded) : s; });
          } });
          const row = ref.current.items.find((i) => i.id === id);
          if (row?.status === 'uploading') { apply((s) => Q.markDone(s, id)); opts.current.onFileDone?.(file, result); }
          else if (row?.status === 'cancelled') opts.current.onFileDone?.(file, result);   // the server stored it before the abort arrived
        } catch (e) {
          if (!(e instanceof UploadAborted)) {
            const text = opts.current.errorText ? opts.current.errorText(e) : (e instanceof Error ? e.message : String(e));
            apply((s) => Q.markFailed(s, id, text));
          }
        } finally {
          aborts.current.delete(id);
          pump();
        }
      })();
    }
  }, [apply]);

  /** Add files; returns the names refused as duplicates of a file that is queued or uploading. */
  const add = useCallback((list: File[]): string[] => {
    const r = Q.enqueue(ref.current, list.map((f) => ({ name: f.name, size: f.size })));
    for (const { id, index } of r.accepted) files.current.set(id, list[index]);
    apply(() => r.state);
    pump();
    return r.duplicates;
  }, [apply, pump]);

  const cancel = useCallback((id: number) => { aborts.current.get(id)?.abort(); apply((s) => Q.cancel(s, id)); pump(); }, [apply, pump]);
  const retry = useCallback((id: number) => { apply((s) => Q.retry(s, id)); pump(); }, [apply, pump]);
  const dismiss = useCallback((id: number) => { apply((s) => Q.dismiss(s, id)); files.current.delete(id); }, [apply]);

  const busy = Q.isBusy(state);
  useEffect(() => {
    if (!busy) return undefined;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [busy]);

  return { items: state.items, busy, add, cancel, retry, dismiss };
}

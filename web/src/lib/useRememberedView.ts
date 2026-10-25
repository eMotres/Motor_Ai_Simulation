import { useCallback, useState } from 'react';
import { readView, writeView } from './viewMemory';

/** `useState` whose value is remembered across reloads (see viewMemory.ts).  The first render already shows the
 *  remembered view; an unknown or unreadable stored value gives `initial`. */
export function useRememberedView<T>(scope: string, initial: T, validate: (raw: unknown) => T | null): [T, (next: T) => void] {
  const [value, setValue] = useState<T>(() => readView(scope, validate) ?? initial);
  const set = useCallback((next: T) => { setValue(next); writeView(scope, next); }, [scope]);
  return [value, set];
}

/** The composer of the ONE project message stream (owner 2026-11-24): staff may mark a message "Internal (staff only)". */
import type { ProjectThreadMessage } from './types';

/** The default of the "Internal (staff only)" switch: OFF when the last message in view is a customer-side one (or the thread is
 *  empty), ON when the last message in view is internal, so a staff conversation stays internal until staff say otherwise.
 *  Never ON for a caller without the server capability. */
export function defaultInternal(canPostInternal: boolean, messages: readonly ProjectThreadMessage[]): boolean {
  if (!canPostInternal || messages.length === 0) return false;
  return messages[messages.length - 1].staff_only === true;
}

/** What the composer sends: the switch the user touched, else the default; always false without the capability. */
export function composeInternal(canPostInternal: boolean, touched: boolean | null, messages: readonly ProjectThreadMessage[]): boolean {
  if (!canPostInternal) return false;
  return touched ?? defaultInternal(canPostInternal, messages);
}

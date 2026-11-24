/** The audience of a project message (owner 2026-11-24, "one message stream"): an explicit, always visible choice for STAFF between
 *  "Everyone in the project" and "Internal (staff only)".  No silent default: staff cannot send until they chose.  Exception: a reply
 *  inside an internal message is fixed to Internal (the server inherits the mark too).  Once chosen it never changes by itself; it
 *  resets with the project, the signed-in identity and after a send.  Non-staff (no server capability) have no choice: everyone. */
import type { ProjectThreadMessage } from './types';

export type Audience = 'all' | 'internal';

/** The audience a reply to ``parent`` is fixed to, or null when it is a free choice. */
export function fixedAudience(canPostInternal: boolean, parent?: Pick<ProjectThreadMessage, 'staff_only'> | null): Audience | null {
  return canPostInternal && parent?.staff_only === true ? 'internal' : null;
}

/** The audience that would be sent: everyone without the capability; else the fixed one, else what staff chose (null = not chosen yet). */
export function effectiveAudience(canPostInternal: boolean, chosen: Audience | null, parent?: Pick<ProjectThreadMessage, 'staff_only'> | null): Audience | null {
  if (!canPostInternal) return 'all';
  return fixedAudience(canPostInternal, parent) ?? chosen;
}

/** True when the message is sent as internal (never without the capability). */
export function isInternal(canPostInternal: boolean, chosen: Audience | null, parent?: Pick<ProjectThreadMessage, 'staff_only'> | null): boolean {
  return effectiveAudience(canPostInternal, chosen, parent) === 'internal';
}

/** The locale key of the one-line reason why the send button is disabled, or null when the audience is settled. */
export function audienceBlockedReason(canPostInternal: boolean, chosen: Audience | null, parent?: Pick<ProjectThreadMessage, 'staff_only'> | null): string | null {
  return effectiveAudience(canPostInternal, chosen, parent) === null ? 'audienceRequired' : null;
}

/** The composer's chosen audience for the identity it was chosen under: a stale choice (other project or other account) is dropped. */
export interface AudienceChoice { project: string; identity: string; value: Audience | null }
export function choiceFor(state: AudienceChoice | null, project: string, identity: string): Audience | null {
  return state && state.project === project && state.identity === identity ? state.value : null;
}

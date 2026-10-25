/** The Development page remembers where the user was (lib/viewMemory.ts): the open project, its tab and its card.
 *  Pure helpers, so the restore rules are tested without a browser. */
import { oneOf, shape, smallInt, text } from '../../lib/viewMemory.ts';

export const DEVELOPMENT_VIEWS = ['specification', 'motors', 'messages', 'notes'] as const;
export type DevelopmentViewTab = (typeof DEVELOPMENT_VIEWS)[number];
export interface DevelopmentView { project: string; view: DevelopmentViewTab; step: number }

/** One memory per account on a shared browser; the address is only hashed into the key. */
export function developmentViewScope(email: string): string {
  let h = 5381;
  for (const ch of email.trim().toLowerCase()) h = ((h << 5) + h + ch.charCodeAt(0)) >>> 0;
  return `development.u${h.toString(16)}`;
}

export const parseDevelopmentView = shape<DevelopmentView>({
  project: text,
  view: (raw) => oneOf(DEVELOPMENT_VIEWS)(raw) ?? undefined,
  step: smallInt,
});

/** A remembered view that still fits the page: the card index inside the cards that exist, and no Notes tab for a
 *  viewer who does not have it. */
export function restorableView(saved: DevelopmentView, cardCount: number, canEditProject: boolean): { view: DevelopmentViewTab; step: number } {
  const view = saved.view === 'notes' && !canEditProject ? 'specification' : saved.view;
  const step = cardCount > 0 ? Math.min(Math.max(0, saved.step), cardCount - 1) : 0;
  return { view, step };
}

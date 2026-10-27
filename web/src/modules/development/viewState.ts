/** The Development page remembers where the user was (lib/viewMemory.ts): the open project, its tab and its card.
 *  Pure helpers, so the restore rules are tested without a browser. */
import { oneOf, shape, smallInt, text } from '../../lib/viewMemory.ts';

export const DEVELOPMENT_VIEWS = ['specification', 'motors', 'messages', 'notes'] as const;
export type DevelopmentViewTab = (typeof DEVELOPMENT_VIEWS)[number];
export interface DevelopmentView { project: string; view: DevelopmentViewTab; step: number; stepId?: string }

/** One memory per account on a shared browser; the address is only hashed into the key. */
export function developmentViewScope(email: string): string {
  let h = 5381;
  for (const ch of email.trim().toLowerCase()) h = ((h << 5) + h + ch.charCodeAt(0)) >>> 0;
  return `development.u${h.toString(16)}`;
}

const parseBase = shape<DevelopmentView>({
  project: text,
  view: (raw) => oneOf(DEVELOPMENT_VIEWS)(raw) ?? undefined,
  step: smallInt,
});
/** `stepId` is the stable name of the wizard step (the number alone changed meaning when five cards became eight steps);
 *  a record without it was written by the card layout. */
export function parseDevelopmentView(raw: unknown): DevelopmentView | null {
  const base = parseBase(raw);
  if (!base) return null;
  const id = (raw as Record<string, unknown>).stepId;
  return typeof id === 'string' && /^[a-z]{2,24}$/.test(id) ? { ...base, stepId: id } : base;
}

/** The five cards of the layout before the wizard, by index, and the step each one lands on now (the closest destination). */
export const LEGACY_CARD_STEPS = ['application', 'project', 'mechanical', 'operating', 'review'] as const;

/** A remembered view that still fits the page: the step by its id (or, for an old record, the closest step to its old card),
 *  falling back to the first step; and no Notes tab for a viewer who does not have it.  `steps` is the number of steps (the
 *  old numeric rule) or the ids of the steps that exist. */
export function restorableView(saved: DevelopmentView, steps: number | readonly string[], canEditProject: boolean): { view: DevelopmentViewTab; step: number } {
  const view = saved.view === 'notes' && !canEditProject ? 'specification' : saved.view;
  if (typeof steps === 'number') return { view, step: steps > 0 ? Math.min(Math.max(0, saved.step), steps - 1) : 0 };
  const wanted = saved.stepId ?? LEGACY_CARD_STEPS[saved.step];
  const index = wanted ? steps.indexOf(wanted) : -1;
  return { view, step: index >= 0 ? index : 0 };
}

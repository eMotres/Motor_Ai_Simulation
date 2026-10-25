// Why the save before "submit for review" failed, in ONE line (owner 2026-10-26: «Save the draft successfully before
// submitting» was shown for every failure and misled).  409: the specification was changed elsewhere - reload;
// 403: only the project owner can submit; anything else keeps the generic text.
export type SubmitFailureKind = 'conflict' | 'forbidden' | 'other';
export const SUBMIT_FAILURE_KEYS: Record<SubmitFailureKind, string> = {
  conflict: 'wizard.saveConflict',
  forbidden: 'wizard.submitForbidden',
  other: 'saveBeforeSubmit',
};
export function submitFailureKind(error: unknown): SubmitFailureKind {
  const status = error && typeof error === 'object' ? (error as { status?: unknown }).status : undefined;
  return status === 409 ? 'conflict' : status === 403 ? 'forbidden' : 'other';
}
/** The translation key of the one-line reason. */
export function submitFailureKey(error: unknown): string {
  return SUBMIT_FAILURE_KEYS[submitFailureKind(error)];
}
/** Only the people who can edit the specification (project owner, global admins) see the submit button. */
export function canSubmitSpec(canEditSpec: boolean): boolean {
  return canEditSpec === true;
}

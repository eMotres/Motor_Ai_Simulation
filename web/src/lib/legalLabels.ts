// The wording of the acceptance boxes as the person saw it, from the locale pieces (common.json `legal.accept`).  The server keeps it with
// the record (agreement section 15.3: "the acceptance and election labels"), so the sign-up and the existing-user dialog send it along.
import type { TFunction } from 'i18next';
import { acceptLabelText, type AcceptLabels, type AcceptStrings, type AcceptanceForm } from './legalLogic';

/** `t` must be bound to the `common` namespace.  Empty when no capacity is chosen yet (nothing is accepted then). */
export function legalLabels(t: TFunction, form: AcceptanceForm, existing = false): AcceptLabels {
  const out: AcceptLabels = {};
  if (form.capacity) out.accept = acceptLabelText(t('legal.accept', { returnObjects: true }) as unknown as AcceptStrings, form.capacity);
  if (existing) out.election = String(t('legal.accept.earlier'));
  return out;
}

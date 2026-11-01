// The wording of the acceptance boxes as the person saw it, from the locale pieces (common.json `legal`).  The server keeps it with the
// record (agreement section 15.3: "the acceptance and election labels"), so the sign-up box, the "updated terms" line and the
// simulation-start dialog send it along.
import type { TFunction } from 'i18next';
import {
  acceptLabelText, signupLabelText, type AcceptLabels, type AcceptStrings, type AgreementForm, type SignupStrings,
} from './legalLogic';

/** `t` must be bound to the `common` namespace.  The sentence beside the sign-up box ("I use AeroStator for business and accept ..."). */
export function signupLabel(t: TFunction): string {
  const s = t('legal.signup', { returnObjects: true }) as unknown as { lead: string; sep: string; tail: string };
  const parts: SignupStrings = { lead: s.lead, terms: String(t('legal.accept.terms')), sep: s.sep, privacy: String(t('legal.accept.privacy')), tail: s.tail };
  return signupLabelText(parts);
}

/** The sentence of the "We updated the Terms of Use and the Privacy Policy." line (the names are links). */
export function termsLineLabel(t: TFunction): string {
  const s = t('legal.banner', { returnObjects: true }) as unknown as { lead: string; sep: string; tail: string };
  return signupLabelText({ lead: s.lead, terms: String(t('legal.accept.terms')), sep: s.sep, privacy: String(t('legal.accept.privacy')), tail: s.tail });
}

/** The two labels of the simulation-start dialog.  Empty when no capacity or mode is chosen yet (nothing is accepted then). */
export function agreementLabels(t: TFunction, form: AgreementForm): AcceptLabels {
  const out: AcceptLabels = {};
  if (form.capacity && form.mode) out.accept = acceptLabelText(t('legal.accept', { returnObjects: true }) as unknown as AcceptStrings, form.capacity, form.mode);
  out.election = String(t('legal.accept.earlier'));
  return out;
}

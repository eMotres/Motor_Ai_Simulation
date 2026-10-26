import type { SpecState } from '../../types';
import type { SpecDraft } from '../../wizardState';
import type { WizardProfile } from './profile';
import type { WizardStep } from './steps';
import type { useSpecFields } from './useSpecFields';

/** What a step needs from the page: the draft and how to change it, the field renderers and the permissions. */
export interface StepEnv {
  tr: (key: string, args?: Record<string, unknown>) => string;
  lang: string;
  draft: SpecDraft;
  profile: WizardProfile;
  spec: SpecState;
  steps: WizardStep[];
  canEditSpec: boolean; busy: boolean; migrationBusy: boolean;
  updateDraft: (fn: (d: SpecDraft) => SpecDraft) => void;
  fields: ReturnType<typeof useSpecFields>;
  fieldErrors: Record<string, string>;
  goToStep: (index: number) => void;
  /** a field the page asked to reveal: its optional group opens */
  revealField: string | null;
}

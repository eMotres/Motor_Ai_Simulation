import { applyDutyEverywhere } from './dutyApply';
import { beginFamilyLoad, failFamilyLoad, finishFamilyLoad } from './familyLoad';
import type { LocalApplyResult } from './dutyLocalApply';

/** The shared catalog/project entrypoint for loading a selected family duty. */
export async function applyCatalogDuty(die: string, config: string, duty: string, canWrite: boolean, onStart?: () => void): Promise<LocalApplyResult> {
  const token = beginFamilyLoad(die, config, duty);
  try {
    onStart?.();
    const result = await applyDutyEverywhere(die, config, duty, canWrite);
    finishFamilyLoad(token);
    return result;
  } catch (error) {
    failFamilyLoad(token, error instanceof Error ? error.message : String(error));
    throw error;
  }
}

import { applyDutyEverywhere } from './dutyApply';
import { guardLoad } from './loadGuard';
import { beginFamilyLoad, failFamilyLoad, finishFamilyLoad } from './familyLoad';
import type { LocalApplyResult } from './dutyLocalApply';

/** The shared catalog/project entrypoint for loading a selected family duty. */
export async function applyCatalogDuty(die: string, config: string, duty: string, canWrite: boolean, onStart?: () => void): Promise<LocalApplyResult> {
  // Ask BEFORE the view switches and the progress line starts: a Cancel leaves the screen as it was.
  const permit = await guardLoad({ die, config, duty });
  if (!permit) return { message: '', machineChanged: false, declined: true };
  const token = beginFamilyLoad(die, config, duty);
  try {
    onStart?.();
    const result = await applyDutyEverywhere(die, config, duty, canWrite, permit);
    finishFamilyLoad(token);
    return result;
  } catch (error) {
    failFamilyLoad(token, error instanceof Error ? error.message : String(error));
    throw error;
  }
}

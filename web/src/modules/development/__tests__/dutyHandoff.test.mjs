import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('catalog and Development enter the same catalog duty handoff', async () => {
  const catalog = source('../../../components/catalog/FamilyCatalog.tsx');
  const development = source('../components/DevelopmentPage.tsx');
  const actions = source('../MotorOpenActions.tsx');
  const handoff = source('../../../lib/catalogDutyApply.ts');
  assert.match(catalog, /applyCatalogDuty\(die, cfg, duty, canWrite,/);
  assert.match(development, /<MotorOpenActions motor=\{r\.motor\} duty=\{r\.result\.duty\}/);
  assert.match(actions, /applyCatalogDuty\(motor\.die, motor\.config, targetDuty, canWriteServer\(\),/);
  assert.match(handoff, /import \{ applyDutyEverywhere \} from '\.\/dutyApply'/);
  const begin = handoff.indexOf('beginFamilyLoad(');
  const switchTab = handoff.indexOf('onStart?.()');
  const apply = handoff.indexOf('await applyDutyEverywhere(');
  const finish = handoff.indexOf('finishFamilyLoad(');
  const fail = handoff.indexOf('failFamilyLoad(');
  assert.ok(begin >= 0 && begin < switchTab && switchTab < apply && apply < finish && apply < fail);
  assert.match(actions, /motorLinkAvailability\(motor, duty, allowMotorDefault\)/);
  const { motorLinkAvailability } = await import('../motorAvailability.ts');
  assert.equal(motorLinkAvailability({ die: 'D', config: 'C', can_open_configure: true, configure_duty: 'server-selected' }, 'different-row').configureDuty, null);
  assert.match(actions, /setActiveTab\('simulation' as never\)/);
  assert.doesNotMatch(actions, /setActiveTab\('compare' as never\)/);   // no Configure button: the row click opens it (owner 2026-10-28)
  // a project motor opened for Configure takes the very same handoff (no second path)
  assert.match(source('../components/DevelopmentPage.tsx'), /openProjectMotor\(\{ id: selected\.id/);
  assert.match(source('../../../lib/openProjectMotor.ts'), /applyCatalogDuty\(motor\.die, motor\.config, duty, canWriteServer\(\),/);
  assert.doesNotMatch(source('../../../App.tsx'), /motres:open-project-motor|applyDutyLocal\(/);
});

test('real shared apply captures outgoing A before applying B and persists the loaded selection', () => {
  const apply = source('../../../lib/dutyApply.ts');
  const local = source('../../../lib/dutyLocalApply.ts');
  const lastMotor = source('../../../lib/lastMotor.ts');
  const outgoing = apply.indexOf('const prev = leaveForDuty(die, cfg, duty)');
  const payload = apply.indexOf('fetchDutyPayload(die, cfg, duty)', outgoing);
  const localApply = apply.indexOf('applyDutyLocal(die, cfg, duty, p, prev, canWrite)', payload);
  const remember = apply.indexOf('rememberLoadedMotor(rememberedEmail, die, cfg, duty, selectionEpoch)', localApply);
  assert.ok(outgoing >= 0 && outgoing < payload && payload < localApply && localApply < remember);
  assert.match(local, /if \(lastDie && lastDie !== die\) rememberDieSettings\(lastDie\)/);
  assert.match(local, /if \(prevKey && prevKey !== nextKey\) rememberDutyOp\(prevKey\)/);
  assert.match(local, /activeDuty\(\).*rememberDutyOp|rememberDutyOp\(prevKey\)/);
  assert.match(local, /restoreDieSettings\(die\)/);
  assert.match(local, /restoreDutyOp\(opKey\)/);
  assert.match(local, /restoreDieMeshSettings\(die\)/);
  assert.match(local, /const changed = machineChanged\(prev, die, cfg\)/);
  assert.match(local, /if \(changed\) \{[\s\S]*?coupledTempsForNewMachine\(p\.duty\)[\s\S]*?set\('coilTemp', t\.coilTemp\)[\s\S]*?set\('magnetTempC', t\.magnetTempC\)/);
  assert.match(local, /dispatchEvent\(new CustomEvent\(MACHINE_CHANGED_EVENT/);
  assert.match(lastMotor, /method: 'PUT'/); // server preference persistence remains in the normal helper
  assert.match(lastMotor, /candidate = server\.selection \?\? local/);
});

test('A to B to A source flow saves A outgoing state and restores A die and duty snapshots', () => {
  const apply = source('../../../lib/dutyApply.ts');
  const local = source('../../../lib/dutyLocalApply.ts');
  const leave = local.slice(local.indexOf('export function leaveForDuty'), local.indexOf('export async function fetchDutyPayload'));
  const load = local.slice(local.indexOf('export async function applyDutyLocal'));
  assert.match(leave, /if \(lastDie && lastDie !== die\) rememberDieSettings\(lastDie\)/);
  assert.match(leave, /if \(prevKey && prevKey !== nextKey\) rememberDutyOp\(prevKey\)/);
  assert.match(apply, /const prev = leaveForDuty\(die, cfg, duty\)[\s\S]*?applyDutyLocal\(die, cfg, duty, p, prev, canWrite\)/);
  assert.match(load, /restoreDieSettings\(die\)/);
  assert.match(load, /restoreDutyOp\(opKey\)/);
  assert.match(load, /const changed = machineChanged\(prev, die, cfg\)/);
  assert.match(load, /if \(changed\) \{[\s\S]*?dispatchEvent\(new CustomEvent\(MACHINE_CHANGED_EVENT/);
});

test('loading project B updates the normal server preference so reload wins over server A', () => {
  const apply = source('../../../lib/dutyApply.ts');
  const lastMotor = source('../../../lib/lastMotor.ts');
  assert.match(apply, /rememberLoadedMotor\(rememberedEmail, die, cfg, duty, selectionEpoch\)/);
  const localSave = lastMotor.indexOf('writeLocalLastMotor(identity, selection)', lastMotor.indexOf('export async function rememberLoadedMotor'));
  const serverSave = lastMotor.indexOf('await rememberLastMotor(identity, selection)', localSave);
  assert.ok(localSave >= 0 && localSave < serverSave);
  assert.match(lastMotor, /method: 'PUT'/);
  assert.match(lastMotor, /candidate = server\.selection \?\? local/);
});

test('project duty selection uses the same first-saved-then-default helper as its buttons', async () => {
  const { firstProjectMotorDuty } = await import('../motorDuty.ts');
  assert.equal(firstProjectMotorDuty(['saved first', 'saved second'], 'server default'), 'saved first');
  assert.equal(firstProjectMotorDuty([], 'server default'), 'server default');
  assert.equal(firstProjectMotorDuty([], null), null);
});

test('every signed-in account gets Projects (owner 2026-10-30), and grant candidates are advisory only', () => {
  const app = source('../../../App.tsx');
  const page = source('../components/DevelopmentPage.tsx');
  const en = JSON.parse(source('../../../locales/en/development.json'));
  const zh = JSON.parse(source('../../../locales/zh-CN/development.json'));
  assert.match(app, /developmentVisible\(enforced, signedIn\)/);
  assert.doesNotMatch(app, /useDevelopmentMember/);
  assert.match(page, /selected\.grant_removal_candidates/);
  assert.match(page, /grantReason\.\$\{candidate\.reason\}/);
  const candidateStart = page.indexOf('{canEditProject && !!selected.grant_removal_candidates');
  const candidateEnd = page.indexOf('</Tooltip>', candidateStart);
  assert.ok(candidateStart >= 0 && candidateEnd > candidateStart);
  assert.doesNotMatch(page.slice(candidateStart, candidateEnd), /onClick|revoke|remove/i);
  for (const reason of ['motor_unlinked', 'customer_membership_changed', 'customer_membership_removed', 'link_failed', 'membership_update_failed']) {
    assert.equal(typeof en.grantReason[reason], 'string');
    assert.equal(typeof zh.grantReason[reason], 'string');
  }
});

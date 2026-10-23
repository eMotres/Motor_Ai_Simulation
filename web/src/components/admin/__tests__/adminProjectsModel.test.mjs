import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parse } from '@formatjs/icu-messageformat-parser';
import { IntlMessageFormat } from 'intl-messageformat';
import { adminProjectPaths, projectAdminMembers, projectCompletenessLabel, projectMotorKey, projectOpenIntent, projectRoleBody, validProjectId } from '../adminProjectsModel.ts';
import { claimProjectOpenIntent, projectOpenStorageKey, restoreProjectOpenIntent } from '../../../modules/development/projectOpenIntent.ts';

test('project completeness uses the Development required-field counters and preserves unknown values', () => {
  assert.equal(projectCompletenessLabel({ filled_required: 3, total_required: 5 }), '3/5');
  assert.equal(projectCompletenessLabel({ filled: 2, required: 4 }), '2/4');
  assert.equal(projectCompletenessLabel(undefined), '—');
  assert.equal(projectCompletenessLabel({ filled_required: 0 }), '—');
});

test('admin project mutation paths encode project IDs, member addresses, and motor names', () => {
  assert.equal(adminProjectPaths.member('sample-project', 'engineer+test@example.invalid'), '/api/admin/projects/sample-project/members?email=engineer%2Btest%40example.invalid');
  assert.equal(adminProjectPaths.motor('sample-project', 'synthetic die/a', 'cfg 1'), '/api/admin/projects/sample-project/motors?die=synthetic%20die%2Fa&config=cfg%201');
  assert.equal(adminProjectPaths.motors('sample-project'), '/api/admin/projects/sample-project/motors');
  assert.equal(projectMotorKey('Synthetic', 'Config A'), 'Synthetic/Config A');
});

test('pending invitations remain visible and active members take precedence on duplicate email', () => {
  assert.deepEqual(projectAdminMembers(
    [{ email: 'person@example.invalid', role: 'engineer' }],
    [{ email: 'person@example.invalid', role: 'customer' }, { email: 'pending@example.invalid', role: 'customer' }],
  ), [
    { email: 'person@example.invalid', role: 'engineer', pending: false },
    { email: 'pending@example.invalid', role: 'customer', pending: true },
  ]);
});

test('role updates send the selected member and project navigation carries a durable project id', () => {
  assert.deepEqual(projectRoleBody('person@example.invalid', 'owner'), { email: 'person@example.invalid', role: 'owner' });
  assert.deepEqual(projectOpenIntent('sample-project'), { storageKey: 'development.openProject', projectId: 'sample-project' });
});

test('project handoff claims only the intended id and a failed open never overwrites a newer intent', () => {
  const values = new Map([[projectOpenStorageKey, 'project-a']]);
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  assert.equal(claimProjectOpenIntent(storage, 'project-b'), null);
  assert.equal(storage.getItem(projectOpenStorageKey), 'project-a');
  assert.equal(claimProjectOpenIntent(storage, 'project-a'), 'project-a');
  assert.equal(storage.getItem(projectOpenStorageKey), null);
  restoreProjectOpenIntent(storage, 'project-a');
  storage.setItem(projectOpenStorageKey, 'project-b');
  restoreProjectOpenIntent(storage, 'project-a');
  assert.equal(storage.getItem(projectOpenStorageKey), 'project-b');
});

test('project IDs match the backend lowercase slug rule after normalization', () => {
  assert.equal(validProjectId('Synthetic-Project-2'), true);
  assert.equal(validProjectId('bad--slug'), false);
  assert.equal(validProjectId('has space'), false);
});

test('new Projects UI messages have complete EN and zh-CN keys and matching ICU placeholders', () => {
  const en = JSON.parse(readFileSync(new URL('../../../locales/en/admin.json', import.meta.url), 'utf8'));
  const zh = JSON.parse(readFileSync(new URL('../../../locales/zh-CN/admin.json', import.meta.url), 'utf8'));
  const vars = (message) => [...message.matchAll(/\{([\w.]+)(?=\s*[},])/g)].map((m) => m[1]).sort();
  const walk = (a, b, path) => {
    if (typeof a === 'string') {
      assert.equal(typeof b, 'string', `${path} missing in zh-CN`);
      parse(a); parse(b);
      assert.deepEqual(vars(b), vars(a), `${path} ICU placeholders differ`);
      return;
    }
    assert.ok(a && b && typeof a === 'object' && typeof b === 'object', `${path} key shape differs`);
    assert.deepEqual(Object.keys(b).sort(), Object.keys(a).sort(), `${path} keys differ`);
    for (const key of Object.keys(a)) walk(a[key], b[key], `${path}.${key}`);
  };
  walk(en.projects, zh.projects, 'admin.projects');
  walk(en.nav.projects, zh.nav.projects, 'admin.nav.projects');
  const enDevelopment = JSON.parse(readFileSync(new URL('../../../locales/en/development.json', import.meta.url), 'utf8'));
  const zhDevelopment = JSON.parse(readFileSync(new URL('../../../locales/zh-CN/development.json', import.meta.url), 'utf8'));
  walk(enDevelopment.comparisonCount, zhDevelopment.comparisonCount, 'development.comparisonCount');
  walk(enDevelopment.variant, zhDevelopment.variant, 'development.variant');
  walk(enDevelopment.comparison, zhDevelopment.comparison, 'development.comparison');
  walk(enDevelopment.grantReviewCount, zhDevelopment.grantReviewCount, 'development.grantReviewCount');
});

test('grant review count formats a plural count in English and Chinese', () => {
  const en = JSON.parse(readFileSync(new URL('../../../locales/en/development.json', import.meta.url), 'utf8'));
  const zh = JSON.parse(readFileSync(new URL('../../../locales/zh-CN/development.json', import.meta.url), 'utf8'));
  assert.match(String(new IntlMessageFormat(en.grantReviewCount, 'en').format({ count: 2 })), /2/);
  assert.match(String(new IntlMessageFormat(zh.grantReviewCount, 'zh-CN').format({ count: 2 })), /2/);
  assert.match(String(new IntlMessageFormat(en.comparisonCount, 'en').format({ variants: 3, results: 7 })), /3.*7/);
  assert.match(String(new IntlMessageFormat(zh.comparisonCount, 'zh-CN').format({ variants: 3, results: 7 })), /3.*7/);
});

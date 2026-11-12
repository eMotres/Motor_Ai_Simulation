// The derived "Specification submitted" stage (owner 2026-11-12): stored status `spec` + a submitted version; nothing else changes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { shownStatus, submittedDate } from '../projectStage.ts';

const sub = { version: 2, at: '2026-11-12T14:03:00+00:00' };

test('spec + a submitted version shows as spec_submitted; every other stage shows as stored', () => {
  assert.equal(shownStatus('spec', sub), 'spec_submitted');
  assert.equal(shownStatus('spec', null), 'spec');
  assert.equal(shownStatus('spec', undefined), 'spec');
  for (const s of ['design', 'simulation', 'quote', 'prototype', 'production', 'closed']) assert.equal(shownStatus(s, sub), s);
});

test('the confirmation date is UTC and localised', () => {
  assert.equal(submittedDate('2026-11-12T23:30:00+00:00', 'en'), '12 Nov 2026');
  assert.equal(submittedDate(null, 'en'), '');
  assert.equal(submittedDate('not a date', 'zh-CN'), '');
});

test('locale keys exist in EN and zh-CN, the staff menu keeps its seven stages, no engineers are named', () => {
  const en = JSON.parse(readFileSync(new URL('../../../locales/en/development.json', import.meta.url), 'utf8'));
  const zh = JSON.parse(readFileSync(new URL('../../../locales/zh-CN/development.json', import.meta.url), 'utf8'));
  assert.equal(en.status.spec_submitted, 'Specification submitted');
  assert.equal(zh.status.spec_submitted, '规格书已提交');
  assert.match(en.wizard.submittedBanner, /\{version\}.*\{date\}.*being prepared for the calculation.*Motors & results/);
  assert.doesNotMatch(en.wizard.submittedBanner, /engineer/i);
  assert.doesNotMatch(zh.wizard.submittedBanner, /工程师/);
  const page = readFileSync(new URL('../components/DevelopmentPage.tsx', import.meta.url), 'utf8');
  assert.match(page, /allowedProjectStatuses\(canManageGlobally, selected\.status\)\.map\(\(s\) => <MenuItem key=\{s\} value=\{s\}>\{tr\(`status\.\$\{s\}`\)\}/);
});

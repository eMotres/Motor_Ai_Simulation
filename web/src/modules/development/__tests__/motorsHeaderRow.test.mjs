// The "Motors & results" header is ONE compact row (owner 2026-11-26): the project-stage block (status line, bar and the lone (i)) is
// gone from this page; "+ Link motor" and "Latest / All versions" share one row; the per-motor progress bar in each card stays.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const page = readFileSync(new URL('../components/DevelopmentPage.tsx', import.meta.url), 'utf8');

test('no project-stage status block on the motors view (it stays on the specification view)', () => {
  assert.match(page, /\{view === 'specification' && selected && !isController && <ProjectStatusBar data=\{activity\} t=\{tr\} \/>\}/);
  assert.doesNotMatch(page, /\(view === 'specification' \|\| view === 'motors'\) && selected && !isController && <ProjectStatusBar/);
  const row = page.slice(page.indexOf('data-testid="motors-header-row"'), page.indexOf('data-testid="motors-header-row"') + 1500);
  assert.doesNotMatch(row, /ProjectStatusBar|motorsTip/);
});

test('the link button and the version switch share ONE row; the per-motor stage bar stays in each variant header', () => {
  const row = page.slice(page.indexOf('data-testid="motors-header-row"'), page.indexOf('data-testid="motors-header-row"') + 1500);
  assert.match(row, /tr\('linkMotor'\)[\s\S]*versionView\.tip/);
  assert.match(page, /<VariantStageBar data=\{activity\} motor=\{live\} t=\{tr\} \/>/);
  assert.doesNotMatch(page, /<Stack direction="row" alignItems="center" spacing=\{1\} sx=\{\{ mb: 0\.7 \}\}><ActivityChip/);
});

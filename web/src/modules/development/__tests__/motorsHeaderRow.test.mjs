// The "Motors & results" header is ONE compact row (owner 2026-11-26): the project-stage block (status line, bar and the lone (i)) is
// gone from this page; "+ Link motor" and "Latest / All versions" share one row; the per-motor progress bar in each card stays.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const page = readFileSync(new URL('../components/DevelopmentPage.tsx', import.meta.url), 'utf8');

test('no project-stage status block on the motors view (it stays on the specification view)', () => {
  assert.match(page, /\{view === 'specification' && selected && !isController && <ProjectStatusBar data=\{activity\} t=\{tr\} \/>\}/);
  assert.doesNotMatch(page, /\(view === 'specification' \|\| view === 'motors'\) && selected && !isController && <ProjectStatusBar/);
});

test('"+ Link motor", "Latest / All versions" and the activity chip sit in the SAME row as "Updates by e-mail"; a row of their own only without it', () => {
  assert.match(page, /const motorsActions = view === 'motors' && !isController \? <>[\s\S]*tr\('linkMotor'\)[\s\S]*versionView\.tip[\s\S]*<\/> : null;/);
  assert.match(page, /<DigestSettings target=\{\{ kind: 'project', id: selected\.id \}\} actions=\{motorsActions\} \/>/);
  assert.match(page, /\{!canEditSpec && <Box [^\n]*data-testid="motors-header-row">\{motorsActions\}<\/Box>\}/);
  const digest = readFileSync(new URL('../../../components/digest/DigestSettings.tsx', import.meta.url), 'utf8');
  assert.match(digest, /\{actions\}\n\s+<Button size="small"[^\n]*aria-expanded/);           // before the Settings button, in the title row
  assert.match(digest, /data-testid="digest-actions"/);                                         // never lost while the settings load or fail
  assert.match(page, /<VariantStageBar data=\{activity\} motor=\{live\} t=\{tr\} \/>/);
  assert.doesNotMatch(page, /<Stack direction="row" alignItems="center" spacing=\{1\} sx=\{\{ mb: 0\.7 \}\}><ActivityChip/);
});

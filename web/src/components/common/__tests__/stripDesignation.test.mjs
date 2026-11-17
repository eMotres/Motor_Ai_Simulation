// node --test: the line above the pages names the loaded motor by its standard designation (coordinator's browser finding on
// #731, owner 2026-11-17: no old names once a motor has one), the stored die / configuration only in its tooltip; while the
// live build differs from the saved one (drift) or there is no complete designation, the old die / configuration line stays.
// A wiring test of the source: the strip renders only inside the app with a live backend context.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const strip = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'ActiveFamilyStrip.tsx'), 'utf8');

test('the designation is fetched for the loaded motor and only a COMPLETE one is used', () => {
  assert.match(strip, /import \{ fetchDesignation \} from '\.\/MotorDesignation';/);
  assert.match(strip, /fetchDesignation\(String\(ctx\.die\), String\(ctx\.config\)\)[\s\S]{0,120}a\?\.complete && a\.designation \? a\.designation : null/);
  assert.match(strip, /\}, \[ctx\?\.die, ctx\?\.config\]\);/);
});

test('with a designation and no drift the strip shows it, the stored names in the tooltip, the link to the catalog kept', () => {
  const i = strip.indexOf('{desig && !drifted2 ? (');
  assert.ok(i > 0);
  const branch = strip.slice(i, strip.indexOf(') : (<>', i));
  assert.match(branch, /<Tooltip title=\{`\$\{ctx\.die\} \/ \$\{ctx\.config\} · Motors catalog`\}>/);
  assert.match(branch, /data-testid="strip-die-to-catalog"[\s\S]*setActiveTab\('motors' as never\)[\s\S]*>\{desig\}<\/b>/);
  assert.doesNotMatch(branch.replace(/title=\{[^}]*\}/, ''), /\{ctx\.die\}|\{cfgLabel\}/);   // no stored name outside the tooltip
});

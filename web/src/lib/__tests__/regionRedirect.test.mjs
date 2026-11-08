/** Two regions (docs/regions/DESIGN.md): the web's answer to the server's 421 `wrong_region`. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { wrongRegionTarget, regionHostAllowed } from '../regionRedirect.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const body = (base) => ({ detail: { code: 'wrong_region', region: 'us', base_url: base } });

test('a wrong_region 421 moves the tab to the home server, same path, query and hash', () => {
  assert.equal(wrongRegionTarget(421, body('https://us.aerostator.com'), 'https://aerostator.com/projects/p1?tab=spec#x'),
    'https://us.aerostator.com/projects/p1?tab=spec#x');
  assert.equal(wrongRegionTarget(421, body('https://aerostator.com/'), 'https://us.aerostator.com/'), 'https://aerostator.com/');
});

test('anything else is ignored: other status, other code, a foreign or plain-http host, a loop onto the same host', () => {
  const here = 'https://aerostator.com/x';
  assert.equal(wrongRegionTarget(409, body('https://us.aerostator.com'), here), null);
  assert.equal(wrongRegionTarget(421, { detail: { code: 'other', base_url: 'https://us.aerostator.com' } }, here), null);
  assert.equal(wrongRegionTarget(421, body('https://evil.example.com'), here), null);
  assert.equal(wrongRegionTarget(421, body('https://aerostator.com.evil.example'), here), null);
  assert.equal(wrongRegionTarget(421, body('http://us.aerostator.com'), here), null);
  assert.equal(wrongRegionTarget(421, body('https://aerostator.com'), here), null);
  assert.equal(wrongRegionTarget(421, body('not a url'), here), null);
  assert.equal(wrongRegionTarget(421, null, here), null);
  assert.ok(regionHostAllowed('cn.aerostator.com') && !regionHostAllowed('aerostator.co'));
});

test('the fetch wrapper uses it on a 421 only', () => {
  const src = readFileSync(join(HERE, '..', 'apiAuth.ts'), 'utf8');
  assert.match(src, /import \{ wrongRegionTarget \} from '\.\/regionRedirect';/);
  assert.match(src, /res\.status === 421 && isApiUrl/);
  assert.match(src, /window\.location\.assign\(target\)/);
});

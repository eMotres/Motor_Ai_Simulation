// node --test - WHICH model a caller sees (owner 2026-10-15): the FULL model with all tools only for view 'full' (NDA + verification, owner / admin);
// everybody else the SIMPLIFIED model; 'none' = simplified not built (never a fallback to full).  The server is the gate; this keeps the UI honest.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

let M = null;
try { M = await import('../model3d.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

t('the server view decides; an older server without it falls back to the NDA flag (never to full without it)', () => {
  assert.equal(M.viewOf({ view: 'full' }, false), 'full');
  assert.equal(M.viewOf({ view: 'simplified' }, true), 'simplified');
  assert.equal(M.viewOf({ view: 'none' }, true), 'none');
  assert.equal(M.viewOf({}, true), 'full');
  assert.equal(M.viewOf({}, false), 'simplified');
  assert.equal(M.viewOf(null, false), 'simplified');
});

t('the viewer loads the full GLB only for the full view, the simplified view route for the simplified one, nothing for none', () => {
  assert.equal(M.glbRoute({ view: 'full' }), 'model.glb');
  assert.equal(M.glbRoute({ view: 'simplified' }), 'simplified_view.glb');
  assert.equal(M.glbRoute({ view: 'none' }), null);
  assert.equal(M.glbRoute({}, false), 'simplified_view.glb');
  assert.equal(M.glbRoute({}, true), 'model.glb');
});

t('the Motor Assembly tree, section, explode, measure and the other full tools exist only for the full view', () => {
  assert.equal(M.fullToolsOpen('full'), true);
  assert.equal(M.fullToolsOpen('simplified'), false);
  assert.equal(M.fullToolsOpen('none'), false);
});

t('the simplified line and its tooltip exist in EN and zh-CN, in both namespaces the viewers use', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../locales');
  for (const loc of ['en', 'zh-CN']) {
    for (const ns of ['model3d', 'geometry']) {
      const d = JSON.parse(readFileSync(resolve(root, loc, `${ns}.json`), 'utf8'));
      for (const k of ['simplifiedLine', 'simplifiedLineTip', 'noView']) assert.equal(typeof d[k], 'string', `${loc}/${ns}/${k}`);
      if (loc === 'zh-CN') assert.match(d.simplifiedLine, /[一-鿿]/);
    }
  }
  const en = JSON.parse(readFileSync(resolve(root, 'en', 'model3d.json'), 'utf8'));
  assert.match(en.downloadLocked, /Simplified model - sign the NDA for the full construction/);
});

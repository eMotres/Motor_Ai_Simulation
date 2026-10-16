import test from 'node:test';
import assert from 'node:assert/strict';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { importsIn, inspectBoundary, scanTree } from '../../../../scripts/check-viewer-boundaries.mjs';

test('scanner ignores comments and ordinary strings that contain import-like text', () => {
  const source = `// import X from '../modules/viewer3d/internal/x';\nconst a = "require('@/stores/motorStore')"; /* export {X} from '@/components/X' */`;
  assert.deepEqual(importsIn(source), []);
  assert.deepEqual(inspectBoundary('src/components/Host.tsx',source), []);
});

test('static imports including import type and export-from are discovered', () => {
  const source = `import type { X } from '../modules/viewer3d/internal/X';\nexport { Y } from '@/modules/viewer3d/internal/Y';`;
  assert.deepEqual(importsIn(source), ['../modules/viewer3d/internal/X','@/modules/viewer3d/internal/Y']);
  assert.equal(inspectBoundary('src/components/Host.tsx',source).length,2);
});

test('multiline import and export clauses are discovered', () => {
  const source = `import type {\n X,\n Y\n} from '@/modules/viewer3d/internal/X';\nexport {\n Z\n} from '../modules/viewer3d/private/Z';`;
  assert.deepEqual(importsIn(source), ['@/modules/viewer3d/internal/X','../modules/viewer3d/private/Z']);
  assert.equal(inspectBoundary('src/components/Host.tsx',source).length,2);
});

test('newlines between import, from, and source tokens cannot bypass the boundary', () => {
  const cases = [
    `import { X } from\n '@/modules/viewer3d/internal/ModelCanvas';`,
    `const X = import\n(\n '@/modules/viewer3d/internal/ModelCanvas');`,
    `require(\n '@/modules/viewer3d/internal/ModelCanvas');`,
  ];
  for (const source of cases) assert.equal(inspectBoundary('src/components/Host.tsx', source).length, 1, source);
});

test('dynamic import and require aliases normalize traversal and slashes', () => {
  const source = String.raw`const a = import ( '@/modules/viewer3d/x/../internal/ModelCanvas' );
const b = require ( '..\\modules\\viewer3d\\internal\\PartTree' );`;
  assert.equal(inspectBoundary('src/components/Host.tsx',source).length,2);
});

test('actual source tree respects module boundaries', () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const src = resolve(here, '../../..');
  assert.deepEqual(scanTree(src), []);
});

test('module rejects relative, alias and normalized imports of components or host stores', () => {
  const source = `import X from '../../../components/geometry/Host';\nimport Y from '@/stores/uiStore';\nconst z = require('../../../../src/components/common/useThing');`;
  const got = inspectBoundary('src/modules/viewer3d/internal/Renderer.ts',source);
  assert.equal(got.length,3);
  assert.ok(got.every((v) => v.rule === 'module-no-components-or-stores'));
});

test('public index imports are allowed while internal and directory-subpath imports are blocked', () => {
  assert.deepEqual(inspectBoundary('src/components/Host.tsx',`import { UnifiedViewer } from '@/modules/viewer3d';`),[]);
  for (const path of ['@/modules/viewer3d/internal','@/modules/viewer3d/internal/index','../modules/viewer3d/private/../internal']) {
    assert.equal(inspectBoundary('src/components/Host.tsx',`import X from '${path}';`).length,1,path);
  }
});

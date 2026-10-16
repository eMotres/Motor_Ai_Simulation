import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveSchemasRoute } from '../schemasRoute.ts';

test('legacy /system route redirects to /schemas and retains query and hash', () => {
  assert.deepEqual(resolveSchemasRoute('https://example.test/system?tab=system&die=abc#design-search'), {
    href: '/schemas?tab=schemas&die=abc#design-search', activeTab: 'schemas', redirected: true,
  });
});

test('legacy tab query redirects while preserving unrelated URL context', () => {
  assert.deepEqual(resolveSchemasRoute('https://example.test/?tab=system&design=d-123&locale=zh-CN#panel'), {
    href: '/?tab=schemas&design=d-123&locale=zh-CN#panel', activeTab: 'schemas', redirected: true,
  });
});

test('canonical path and tab query both open the Schemas tab', () => {
  assert.equal(resolveSchemasRoute('https://example.test/schemas').activeTab, 'schemas');
  assert.equal(resolveSchemasRoute('https://example.test/?tab=schemas#loops').activeTab, 'schemas');
  assert.equal(resolveSchemasRoute('https://example.test/').activeTab, null);
});

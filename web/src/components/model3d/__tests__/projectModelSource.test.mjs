import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openingSource, sourceScope } from '../projectModelSource.ts';

test('a project with an uploaded model opens on Uploaded; without one on Generated; a choice made in the project wins', () => {
  assert.equal(openingSource({ controller: false, remembered: null, liveUploads: 1 }), 'uploaded');
  assert.equal(openingSource({ controller: false, remembered: null, liveUploads: 0 }), 'generated');
  assert.equal(openingSource({ controller: false, remembered: 'generated', liveUploads: 3 }), 'generated');
  assert.equal(openingSource({ controller: false, remembered: 'uploaded', liveUploads: 0 }), 'uploaded');
});

test('a controller project is always Uploaded (nothing to generate from)', () => {
  assert.equal(openingSource({ controller: true, remembered: 'generated', liveUploads: 0 }), 'uploaded');
  assert.equal(sourceScope('p-1'), 'project.model3d.source.p-1');
});

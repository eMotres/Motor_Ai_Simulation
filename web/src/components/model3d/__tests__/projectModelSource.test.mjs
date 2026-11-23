import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openingSource, sourceScope } from '../projectModelSource.ts';

test('a project with an uploaded model opens on Uploaded; without one on Generated; a remembered choice applies only when it can show something', () => {
  assert.equal(openingSource({ controller: false, remembered: null, liveUploads: 1 }), 'uploaded');
  assert.equal(openingSource({ controller: false, remembered: null, liveUploads: 0 }), 'generated');
  assert.equal(openingSource({ controller: false, remembered: 'generated', liveUploads: 3 }), 'generated');
  assert.equal(openingSource({ controller: false, remembered: 'uploaded', liveUploads: 0 }), 'generated');
});

test('archive the last upload, reopen: Generated; restore it, reopen: the remembered Uploaded again', () => {
  const remembered = 'uploaded';
  assert.equal(openingSource({ controller: false, remembered, liveUploads: 0 }), 'generated');
  assert.equal(openingSource({ controller: false, remembered, liveUploads: 1 }), 'uploaded');
});

test('a choice made now wins, so staff can open Uploaded to add the first model', () => {
  assert.equal(openingSource({ controller: false, pickedNow: 'uploaded', remembered: null, liveUploads: 0 }), 'uploaded');
  assert.equal(openingSource({ controller: false, pickedNow: 'generated', remembered: 'uploaded', liveUploads: 2 }), 'generated');
});

test('a controller project is always Uploaded; switched to a motor project it follows the motor rules', () => {
  assert.equal(openingSource({ controller: true, pickedNow: 'generated', remembered: 'generated', liveUploads: 0 }), 'uploaded');
  assert.equal(openingSource({ controller: false, remembered: null, liveUploads: 0 }), 'generated');
  assert.equal(openingSource({ controller: false, remembered: null, liveUploads: 1 }), 'uploaded');
  assert.equal(sourceScope('p-1'), 'project.model3d.source.p-1');
});

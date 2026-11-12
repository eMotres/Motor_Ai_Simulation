// node --test: the die drawings left the catalog tree (owner 2026-11-12); they come from GET /api/family/thumbs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readThumbs, thumbOf } from '../dieThumbs.ts';
import { indexDies } from '../cardMotor.ts';

test('readThumbs keeps only name -> non-empty string entries', () => {
  assert.deepEqual(readThumbs({ thumbs: { A: '<svg/>', B: '', C: 5, D: null } }), { A: '<svg/>' });
  assert.deepEqual(readThumbs(null), {});
  assert.deepEqual(readThumbs({ thumbs: 'x' }), {});
  assert.deepEqual(readThumbs({}), {});
});

test('the drawing comes from the thumbs map, then from an older server\'s thumb_svg, else none', () => {
  assert.equal(thumbOf({ name: 'A' }, { A: '<a/>' }), '<a/>');
  assert.equal(thumbOf({ name: 'A', thumb_svg: '<old/>' }, { A: '<a/>' }), '<a/>');
  assert.equal(thumbOf({ name: 'A', thumb_svg: '<old/>' }, null), '<old/>');
  assert.equal(thumbOf({ name: 'A', thumb_svg: null }, {}), undefined);
  assert.equal(thumbOf(null, { A: '<a/>' }), undefined);
});

test('the overview tiles get the drawing once the thumbs have arrived', () => {
  const dies = [{ name: 'CILN28', slots: 24, poles: 20, has_thumb: true }];
  assert.equal(indexDies(dies).get('ciln28').thumbSvg, undefined);
  assert.equal(indexDies(dies, { CILN28: '<svg/>' }).get('ciln28').thumbSvg, '<svg/>');
});

/**
 * Unit tests for the mesh load/save contract.
 *
 * The web project has no test runner (no vitest/jest in package.json), and
 * adding one is not a one-file change, so these run on Node's built-in runner
 * against the TypeScript module directly (Node >= 22 strips the types):
 *
 *     cd web && node --test src/components/mesh/__tests__/meshSaveContract.test.mjs
 *
 * Plain .mjs on purpose: tsconfig.app.json only compiles .ts/.tsx under src, so
 * this file adds no type-check errors and vite never bundles it.
 *
 * What is under test is the rule the 2026-09-07 incident produced (user:
 * "захожу в Mesh и опять не сохранено то, что было до этого — там точно стояло
 * 1/2"): between 09:0x (config n_sectors: 2), the 09:06:45 API restart and 09:2x
 * (the API answering the panel's constant defaults 1 / 1.3), a save fired for
 * settings the user had not touched, from a panel that had not read the config.
 * Both of those must now be refused.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  adoptMeshConfig, configRetryDelayMs, decideMeshSave, MESH_CONFIG_KEYS,
} from '../meshSaveContract.ts';

/** The panel's constant defaults — the values that must never reach the server. */
const DEFAULTS = {
  mesh_size_mm: 4.0, min_size_mm: 0.3, outer_air_factor: 1.3,
  gap_layers: 2, n_sectors: 1,
};

test('no save before the server config has been read', () => {
  const d = decideMeshSave(DEFAULTS, ['n_sectors'], false);
  assert.equal(d.save, false);
  assert.equal(d.reason, 'config-unknown');
  assert.deepEqual(d.patch, {});
});

test('no save when the user changed nothing (the 09:2x clobber)', () => {
  // Config loaded, panel painted from an EMPTY localStorage → constants.
  const d = decideMeshSave(DEFAULTS, [], true);
  assert.equal(d.save, false);
  assert.equal(d.reason, 'nothing-user-changed');
  assert.deepEqual(d.patch, {});
});

test('a user edit is saved, and ONLY that setting is sent', () => {
  const state = { ...DEFAULTS, n_sectors: 2 };   // the user picked 1/2
  const d = decideMeshSave(state, ['n_sectors'], true);
  assert.equal(d.save, true);
  assert.deepEqual(d.patch, { n_sectors: 2 });
  // outer_air_factor is NOT in the body, so the server keeps its own value —
  // the second half of what was overwritten on 2026-09-07.
  assert.equal('outer_air_factor' in d.patch, false);
});

test('several user edits ride one batched patch', () => {
  const state = { ...DEFAULTS, n_sectors: 2, gap_layers: 4 };
  const d = decideMeshSave(state, ['n_sectors', 'gap_layers'], true);
  assert.deepEqual(d.patch, { n_sectors: 2, gap_layers: 4 });
});

test('non-finite values are never sent', () => {
  const d = decideMeshSave({ ...DEFAULTS, mesh_size_mm: NaN }, ['mesh_size_mm'], true);
  assert.equal(d.save, false);
});

test('unknown dirty keys cannot smuggle fields into the body', () => {
  const d = decideMeshSave(DEFAULTS, ['normal_deviation', 'pole_copy'], true);
  assert.equal(d.save, false);
  assert.deepEqual(MESH_CONFIG_KEYS.slice(), [
    'mesh_size_mm', 'min_size_mm', 'outer_air_factor', 'gap_layers', 'n_sectors',
  ]);
});

test('adoption takes the server values and ignores junk', () => {
  const got = adoptMeshConfig({
    mesh_size_mm: 4.0, min_size_mm: 0.3, outer_air_factor: 1.3,
    gap_layers: 1.0, n_sectors: 2, normal_deviation: 8.0, note: 'x', n_radial: null,
  });
  assert.deepEqual(got, {
    mesh_size_mm: 4.0, min_size_mm: 0.3, outer_air_factor: 1.3,
    gap_layers: 1, n_sectors: 2,
  });
});

test('gap_layers is clamped to the slider range 1…6, not the old 1…3', () => {
  assert.equal(adoptMeshConfig({ gap_layers: 4 }).gap_layers, 4);   // used to come back as 3
  assert.equal(adoptMeshConfig({ gap_layers: 9 }).gap_layers, 6);
  assert.equal(adoptMeshConfig({ gap_layers: 0 }).gap_layers, 1);
});

test('a malformed payload adopts nothing (server stays authoritative)', () => {
  assert.deepEqual(adoptMeshConfig(null), {});
  assert.deepEqual(adoptMeshConfig({ n_sectors: '2' }), {});
});

test('retry backs off 1→2→4…60 s and never gives up', () => {
  assert.deepEqual(
    [0, 1, 2, 3, 4, 5, 6, 7, 20].map(configRetryDelayMs),
    [1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000],
  );
});

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { contrastRatio, MODULE_NAMES, MODULE_NODE_TOKENS, NODE_BORDER_WIDTH_PX } from '../systemGraph.ts';

const fixtureRoot = new URL('./fixtures/', import.meta.url);
const fixtureFiles = [
  'system_graph_modules.json', 'system_graph_modules_motor.json',
  'system_graph_propeller_s1.json', 'system_graph_propeller_s1_motor.json',
];
const repoRoot = (() => {
  let dir = path.dirname(fileURLToPath(import.meta.url));
  while (!fs.existsSync(path.join(dir, 'docs', 'MODULE_MAP.md'))) {
    const parent = path.dirname(dir);
    if (parent === dir) throw new Error('Could not find repository docs/MODULE_MAP.md');
    dir = parent;
  }
  return dir;
})();
const moduleMapHeadings = new Map();
for (const line of fs.readFileSync(path.join(repoRoot, 'docs', 'MODULE_MAP.md'), 'utf8').split(/\r?\n/)) {
  const match = line.match(/^### (M\d{2}) — (.+)$/u);
  if (match && !moduleMapHeadings.has(match[1])) moduleMapHeadings.set(match[1], match[2]);
}

test('frontend canonical module names match MODULE_MAP headings', () => {
  for (const [id, name] of Object.entries(MODULE_NAMES)) assert.equal(moduleMapHeadings.get(id), name, `${id} heading`);
});

test('every graph fixture node maps to a canonical, real software module and source', () => {
  for (const file of fixtureFiles) {
    const graph = JSON.parse(fs.readFileSync(new URL(file, fixtureRoot), 'utf8'));
    assert.ok(graph.nodes.length > 0, `${file} has nodes`);
    for (const node of graph.nodes) {
      assert.ok(Object.hasOwn(MODULE_NAMES, node.module_id), `${file}:${node.id} has a valid module id`);
      assert.equal(node.module_name, MODULE_NAMES[node.module_id], `${file}:${node.id} has the canonical module name`);
      assert.ok(Array.isArray(node.public_api), `${file}:${node.id} includes its public API list`);
      assert.equal(node.documentation, 'docs/MODULE_MAP.md', `${file}:${node.id} links to its module docs`);
      assert.ok(fs.existsSync(path.join(repoRoot, node.documentation)), `${file}:${node.id} documentation exists`);
      assert.ok(node.source?.file, `${file}:${node.id} points to a source file`);
      assert.match(node.source.file, /^src\/motor_ai_sim\//, `${file}:${node.id} source stays inside the real codebase`);
      assert.doesNotMatch(node.source.file, /astra_progress|scratch/i, `${file}:${node.id} has no scratch source path`);
      assert.ok(fs.existsSync(path.join(repoRoot, node.source.file)), `${file}:${node.id} source exists: ${node.source.file}`);
    }
  }
});

test('all module header tokens meet 4.5:1 text contrast and 1.5px border minimum', () => {
  assert.deepEqual(Object.keys(MODULE_NAMES).sort(), Object.keys(MODULE_NODE_TOKENS).sort());
  assert.ok(NODE_BORDER_WIDTH_PX >= 1.5);
  for (const [id, token] of Object.entries(MODULE_NODE_TOKENS)) {
    assert.ok(contrastRatio(token.foreground, token.background) >= 4.5, `${id} header contrast`);
    assert.ok(contrastRatio('#111827', '#ffffff') >= 4.5, `${id} node title on white`);
    assert.ok(contrastRatio('#334155', '#ffffff') >= 4.5, `${id} secondary text on white`);
    assert.ok(contrastRatio(token.border, '#ffffff') >= 3, `${id} border visibility`);
  }
});

test('roadmap stub nodes retain readable module tokens and distinguishable stub styling', () => {
  const graph = JSON.parse(fs.readFileSync(new URL('system_graph_modules.json', fixtureRoot), 'utf8'));
  const stubs = graph.nodes.filter((node) => node.roadmap);
  assert.ok(stubs.length > 0, 'fixture exercises a StubModule');
  for (const node of stubs) {
    const token = MODULE_NODE_TOKENS[node.module_id];
    assert.ok(token);
    assert.ok(contrastRatio(token.foreground, token.background) >= 4.5);
  }
  const panel = fs.readFileSync(new URL('../../components/system/SystemPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /n\.roadmap \? 'dashed' : 'solid'/);
  assert.doesNotMatch(panel, /opacity:\s*data\.dim|filter:\s*data\.dim/);
});

test('module names have EN and zh-CN display labels while preserving canonical English data names', () => {
  const en = JSON.parse(fs.readFileSync(new URL('../../locales/en/common.json', import.meta.url), 'utf8'));
  const zh = JSON.parse(fs.readFileSync(new URL('../../locales/zh-CN/common.json', import.meta.url), 'utf8'));
  for (const id of Object.keys(MODULE_NAMES)) {
    assert.ok(en.system.modules[id], `EN label for ${id}`);
    assert.ok(zh.system.modules[id], `zh-CN label for ${id}`);
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const directory = dirname(fileURLToPath(import.meta.url));
const componentPath = join(directory, 'GeometryLayout.tsx');
const source = readFileSync(componentPath, 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { jsx:ts.JsxEmit.ReactJSX, module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2020 } }).outputText;
const module = { exports:{} };
new Function('require','module','exports',compiled)(createRequire(componentPath), module, module.exports);
const GeometryLayout = module.exports.default;

test('Geometry layout renders the shared EM column, optional CAD editor, divider and viewer in order', () => {
  const html = renderToStaticMarkup(React.createElement(GeometryLayout, {
    emContent:React.createElement('span',null,'Save to Motor · EM parameters'),
    cadContent:React.createElement('span',null,'CAD-only editor'),
    viewer:React.createElement('span',null,'Shared 3D viewer'),
    panelWidth:300,
    onDividerMouseDown(){},
  }));
  const em = html.indexOf('Save to Motor');
  const cad = html.indexOf('CAD-only editor');
  const divider = html.indexOf('geometry-resizer');
  const viewer = html.indexOf('Shared 3D viewer');
  assert.ok(em >= 0 && cad > em && divider > cad && viewer > divider, html);
  assert.match(html, /data-testid="geometry-left-column"/);
  assert.match(html, /data-testid="geometry-viewer-column"/);
});

test('Geometry Active uses the same EM and viewer columns without a CAD editor region', () => {
  const html = renderToStaticMarkup(React.createElement(GeometryLayout, {
    emContent:React.createElement('span',null,'Shared EM parameters'),
    viewer:React.createElement('span',null,'Active viewer'),
    panelWidth:300,
    onDividerMouseDown(){},
  }));
  assert.ok(html.includes('Shared EM parameters'));
  assert.ok(html.includes('Active viewer'));
  assert.doesNotMatch(html, /geometry-cad-content|CAD-only editor/);
});

test('Geometry Active and Full receive one shared EM content element and Full adds only the CAD editor below it', () => {
  const app = readFileSync(join(directory, '..', '..', 'App.tsx'), 'utf8');
  const tab = readFileSync(join(directory, 'GeometryTab.tsx'), 'utf8');
  const full = readFileSync(join(directory, 'GeometryFullPanel.tsx'), 'utf8');
  assert.match(app, /const geometryEmContent\s*=\s*<>[\s\S]*SaveToMotorButton[\s\S]*ParameterVariationTable[\s\S]*<\/>/);
  assert.match(app, /<GeometryTab emContent=\{geometryEmContent\} activeViewer=\{geometryActiveViewer\}/);
  assert.match(tab, /<GeometryLayout emContent=\{emContent\} viewer=\{activeViewer\}/);
  assert.match(tab, /<GeometryFullPanel[\s\S]*emContent=\{emContent\}/);
  assert.match(full, /<GeometryLayout emContent=\{emContent\} cadContent=\{leftBody\}/);
  assert.doesNotMatch(full, /toggle-cad-drawer|cad-drawer|kit-chip/);
});

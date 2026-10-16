import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const THREE = require('three');
const previousTsLoader = require.extensions['.ts'];
require.extensions['.ts'] = (module, filename) => {
  const source = readFileSync(filename, 'utf8');
  const output = ts.transpileModule(source, { compilerOptions:{ module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2020 } }).outputText;
  module._compile(output, filename);
};
const { ModelViewer } = require('../internal/modelViewer.ts');
if (previousTsLoader) require.extensions['.ts'] = previousTsLoader;
else delete require.extensions['.ts'];

function engineForTest() {
  const viewer = Object.create(ModelViewer.prototype);
  Object.assign(viewer, {
    scene: new THREE.Scene(), root: null, groups: [], plane: new THREE.Plane(new THREE.Vector3(0,0,-1),0),
    marks: new THREE.Group(), picks: [], threadMeshes: [], threadMap: null, motorLength: 12,
    box: { min:[-1,-1,-1], max:[1,1,1] }, host: { clientWidth:640, clientHeight:480 },
    camera: new THREE.OrthographicCamera(-100,100,100,-100,0.1,5000),
    controls: { target:new THREE.Vector3(), update(){} }, onMeasure(){}, onCameraDirection(){}, onCameraUp(){}, onSelect(){},
  });
  viewer.camera.up.set(0,0,1);
  viewer.camera.position.set(30,-40,50);
  return viewer;
}

function assertTop(viewer, label) {
  const direction = viewer.camera.position.clone().sub(viewer.controls.target).normalize();
  assert.ok(Math.abs(direction.x) < 1e-6, `${label}: x=${direction.x}`);
  assert.ok(Math.abs(direction.y) < 1e-6, `${label}: y=${direction.y}`);
  assert.ok(direction.z > 0.999999, `${label}: z=${direction.z}`);
}

test('shared engine installs every new object in a top view and Home returns to top', () => {
  const viewer = engineForTest();
  const root = new THREE.Group();
  root.add(new THREE.Mesh(new THREE.BoxGeometry(12,18,24), new THREE.MeshStandardMaterial()));
  viewer.setObject(root, []);
  assertTop(viewer, 'initial active/GLB setObject');
  const initial = viewer.camera.position.clone().sub(viewer.controls.target).normalize();
  viewer.view('front');
  assert.ok(viewer.camera.position.y < viewer.controls.target.y - 0.999);
  viewer.controls.target.set(4,-7,2); // pan
  viewer.camera.position.set(14,13,32); // orbit and zoom
  viewer.camera.zoom = 3;
  viewer.home();
  assertTop(viewer, 'Home');
  const home = viewer.camera.position.clone().sub(viewer.controls.target).normalize();
  assert.ok(initial.distanceTo(home) < 1e-6, 'Home restores the same top orientation as the initial load');
  assert.equal(viewer.camera.zoom, 1, 'Home also refits after zoom');
  viewer.clear();
  root.traverse((o) => { if (o.isMesh) { o.geometry.dispose(); o.material.dispose(); } });
});

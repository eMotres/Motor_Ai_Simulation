// The viewer loads the meshopt-compressed GLBs the server now sends (cad/viewer_pack.py) and still loads the canonical ones.
//
// PACKED is a SYNTHETIC test model, not a motor: tests/test_cad_viewer_pack.py::synthetic_glb(n=8) (three tubes: Core, Coils/Coil_01,
// Coils/Coil_02 with a thread extra) packed by gltfpack 1.3 with viewer_pack.TOOL_ARGS (-cc -vpf -vn 10 -kn -km -ke).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { Box3 } from 'three';
const { makeGltfLoader } = await import('../gltfLoader.ts');

const PACKED =
  'Z2xURgIAAABcEgAAvAsAAEpTT057ImFzc2V0Ijp7InZlcnNpb24iOiIyLjAiLCJnZW5lcmF0b3IiOiJnbHRmcGFjayAxLjMiLCJleHRyYXMiOnsibW90b3Jf' +
  'aWQiOiJTWU5USF9MMSIsInVuaXRzIjoibW0iLCJ1cF9heGlzIjoiWiAobW90b3IgYXhpcykifX0sImV4dGVuc2lvbnNVc2VkIjpbIktIUl9tZXNoX3F1YW50' +
  'aXphdGlvbiIsIkVYVF9tZXNob3B0X2NvbXByZXNzaW9uIl0sImV4dGVuc2lvbnNSZXF1aXJlZCI6WyJLSFJfbWVzaF9xdWFudGl6YXRpb24iLCJFWFRfbWVz' +
  'aG9wdF9jb21wcmVzc2lvbiJdLCJidWZmZXJzIjpbeyJieXRlTGVuZ3RoIjoxNjY4fSx7ImJ5dGVMZW5ndGgiOjQ5OTIsImV4dGVuc2lvbnMiOnsiRVhUX21l' +
  'c2hvcHRfY29tcHJlc3Npb24iOnsiZmFsbGJhY2siOnRydWV9fX1dLCJidWZmZXJWaWV3cyI6W3siYnVmZmVyIjoxLCJieXRlT2Zmc2V0IjowLCJieXRlTGVu' +
  'Z3RoIjoyMzA0LCJieXRlU3RyaWRlIjoxMiwidGFyZ2V0IjozNDk2MiwiZXh0ZW5zaW9ucyI6eyJFWFRfbWVzaG9wdF9jb21wcmVzc2lvbiI6eyJidWZmZXIi' +
  'OjAsImJ5dGVPZmZzZXQiOjAsImJ5dGVMZW5ndGgiOjEwMjQsImJ5dGVTdHJpZGUiOjEyLCJtb2RlIjoiQVRUUklCVVRFUyIsImZpbHRlciI6IkVYUE9ORU5U' +
  'SUFMIiwiY291bnQiOjE5Mn19fSx7ImJ1ZmZlciI6MSwiYnl0ZU9mZnNldCI6MjMwNCwiYnl0ZUxlbmd0aCI6MTUzNiwiYnl0ZVN0cmlkZSI6OCwidGFyZ2V0' +
  'IjozNDk2MiwiZXh0ZW5zaW9ucyI6eyJFWFRfbWVzaG9wdF9jb21wcmVzc2lvbiI6eyJidWZmZXIiOjAsImJ5dGVPZmZzZXQiOjEwMjQsImJ5dGVMZW5ndGgi' +
  'OjQxMSwiYnl0ZVN0cmlkZSI6OCwibW9kZSI6IkFUVFJJQlVURVMiLCJmaWx0ZXIiOiJPQ1RBSEVEUkFMIiwiY291bnQiOjE5Mn19fSx7ImJ1ZmZlciI6MSwi' +
  'Ynl0ZU9mZnNldCI6Mzg0MCwiYnl0ZUxlbmd0aCI6MTE1MiwidGFyZ2V0IjozNDk2MywiZXh0ZW5zaW9ucyI6eyJFWFRfbWVzaG9wdF9jb21wcmVzc2lvbiI6' +
  'eyJidWZmZXIiOjAsImJ5dGVPZmZzZXQiOjE0MzYsImJ5dGVMZW5ndGgiOjIyOSwiYnl0ZVN0cmlkZSI6MiwibW9kZSI6IlRSSUFOR0xFUyIsImNvdW50Ijo1' +
  'NzZ9fX1dLCJhY2Nlc3NvcnMiOlt7ImJ1ZmZlclZpZXciOjAsImJ5dGVPZmZzZXQiOjAsImNvbXBvbmVudFR5cGUiOjUxMjYsImNvdW50Ijo2NCwidHlwZSI6' +
  'IlZFQzMiLCJtaW4iOlstMjAsLTIwLC01XSwibWF4IjpbMjAsMjAsNV19LHsiYnVmZmVyVmlldyI6MSwiYnl0ZU9mZnNldCI6MCwiY29tcG9uZW50VHlwZSI6' +
  'NTEyMiwiY291bnQiOjY0LCJ0eXBlIjoiVkVDMyIsIm5vcm1hbGl6ZWQiOnRydWV9LHsiYnVmZmVyVmlldyI6MiwiYnl0ZU9mZnNldCI6MCwiY29tcG9uZW50' +
  'VHlwZSI6NTEyMywiY291bnQiOjE5MiwidHlwZSI6IlNDQUxBUiJ9LHsiYnVmZmVyVmlldyI6MCwiYnl0ZU9mZnNldCI6NzY4LCJjb21wb25lbnRUeXBlIjo1' +
  'MTI2LCJjb3VudCI6NjQsInR5cGUiOiJWRUMzIiwibWluIjpbLTE0LC0xNCwtNl0sIm1heCI6WzE0LDE0LDZdfSx7ImJ1ZmZlclZpZXciOjEsImJ5dGVPZmZz' +
  'ZXQiOjUxMiwiY29tcG9uZW50VHlwZSI6NTEyMiwiY291bnQiOjY0LCJ0eXBlIjoiVkVDMyIsIm5vcm1hbGl6ZWQiOnRydWV9LHsiYnVmZmVyVmlldyI6Miwi' +
  'Ynl0ZU9mZnNldCI6Mzg0LCJjb21wb25lbnRUeXBlIjo1MTIzLCJjb3VudCI6MTkyLCJ0eXBlIjoiU0NBTEFSIn0seyJidWZmZXJWaWV3IjowLCJieXRlT2Zm' +
  'c2V0IjoxNTM2LCJjb21wb25lbnRUeXBlIjo1MTI2LCJjb3VudCI6NjQsInR5cGUiOiJWRUMzIiwibWluIjpbLTE3LC0xNywtNl0sIm1heCI6WzE3LDE3LDZd' +
  'fSx7ImJ1ZmZlclZpZXciOjEsImJ5dGVPZmZzZXQiOjEwMjQsImNvbXBvbmVudFR5cGUiOjUxMjIsImNvdW50Ijo2NCwidHlwZSI6IlZFQzMiLCJub3JtYWxp' +
  'emVkIjp0cnVlfSx7ImJ1ZmZlclZpZXciOjIsImJ5dGVPZmZzZXQiOjc2OCwiY29tcG9uZW50VHlwZSI6NTEyMywiY291bnQiOjE5MiwidHlwZSI6IlNDQUxB' +
  'UiJ9XSwibWF0ZXJpYWxzIjpbeyJuYW1lIjoiQ29yZSIsInBick1ldGFsbGljUm91Z2huZXNzIjp7ImJhc2VDb2xvckZhY3RvciI6WzAuMzE4NTAwMDEyLDAu' +
  'MzQyNDAwMDE0LDAuMzgwMTAwMDEyLDFdLCJtZXRhbGxpY0ZhY3RvciI6MC4xMDAwMDAwMDEsInJvdWdobmVzc0ZhY3RvciI6MC41NTAwMDAwMTJ9LCJkb3Vi' +
  'bGVTaWRlZCI6dHJ1ZX0seyJuYW1lIjoiQ29pbHMiLCJwYnJNZXRhbGxpY1JvdWdobmVzcyI6eyJiYXNlQ29sb3JGYWN0b3IiOlswLjYwMzc5OTk5OSwwLjE4' +
  'NzI5OTk5NywwLjAzOTcwMDAwMTUsMV0sIm1ldGFsbGljRmFjdG9yIjowLjEwMDAwMDAwMSwicm91Z2huZXNzRmFjdG9yIjowLjU1MDAwMDAxMn0sImRvdWJs' +
  'ZVNpZGVkIjp0cnVlfV0sIm1lc2hlcyI6W3sicHJpbWl0aXZlcyI6W3siYXR0cmlidXRlcyI6eyJQT1NJVElPTiI6MCwiTk9STUFMIjoxfSwiaW5kaWNlcyI6' +
  'MiwibWF0ZXJpYWwiOjB9XX0seyJwcmltaXRpdmVzIjpbeyJhdHRyaWJ1dGVzIjp7IlBPU0lUSU9OIjozLCJOT1JNQUwiOjR9LCJpbmRpY2VzIjo1LCJtYXRl' +
  'cmlhbCI6MX1dfSx7InByaW1pdGl2ZXMiOlt7ImF0dHJpYnV0ZXMiOnsiUE9TSVRJT04iOjYsIk5PUk1BTCI6N30sImluZGljZXMiOjgsIm1hdGVyaWFsIjox' +
  'fV19XSwibm9kZXMiOlt7Im5hbWUiOiJDb3JlIiwibWVzaCI6MH0seyJuYW1lIjoiQ29yZSIsImNoaWxkcmVuIjpbMF0sImV4dHJhcyI6eyJmYW1pbHkiOiJD' +
  'b3JlIiwiZXhwbG9kZSI6MC4wLCJwYXJ0IjoiQ29yZSIsInRyaWFuZ2xlcyI6NjR9fSx7Im5hbWUiOiJDb2lsXzAxIiwibWVzaCI6MX0seyJuYW1lIjoiQ29p' +
  'bF8wMiIsIm1lc2giOjIsImV4dHJhcyI6eyJ0aHJlYWQiOnsicGl0Y2hfbW0iOjAuNX19fSx7Im5hbWUiOiJDb2lscyIsImNoaWxkcmVuIjpbMiwzXSwiZXh0' +
  'cmFzIjp7ImZhbWlseSI6IkNvaWxzIiwiZXhwbG9kZSI6LTAuMzUsInBhcnQiOiJDb2lscyIsInRyaWFuZ2xlcyI6MTI4fX0seyJuYW1lIjoiTW9kZWwiLCJj' +
  'aGlsZHJlbiI6WzEsNF19XSwic2NlbmVzIjpbeyJub2RlcyI6WzVdfV0sInNjZW5lIjowfSAghAYAAEJJTgCgVVVVMzMzM5KRkZKRkpKRM8zMzEhHSEdHSEdI' +
  'Pzz8/JKRSEqRR0mSR0mSD/Pz85JJSpFHSZJHSZJI8zMzM0fNzs7Nzs3NzjPMzMwdHh0eHh0eHT88/PzNzh2vzh6wzR6wzQ/z8/PNsK/OHrDNHrDNHfMzMzMe' +
  'FhUVFhUWFhUzzMzMbm1ubW1ubW4/PPz8FhVuVxVtWBZtWBYP8/PzFlhXFW1YFm1YFm759aUzMzMzFzc5FRY6OBj7DLDw8JCg8CcbHR4oEBgzABw3AB0bEicy' +
  'GzoAKCgXGxw3AB0bEicyGzoAHPMzMzNUIU1PHyBQTiLzzMzMDx0eHUFDGxxEYBEiLQAMTQBDCw8PLAtQAGAQIQsMTQBDCw8PLAtQAEKzMzMzEy8xERIyMBTz' +
  'zMzMBxESESkrDxAs+/8G8PWX9fA8FBkvKxgy+PVvD1l/Xw88Ey8rGDIqVVVVABACAAAAQAgAAEAIAAEAIAAQAgAAAEAIAABACAABACAAEAIAAABACAAAQAgA' +
  'AQAgAAEBQAAAAIAAAAB1dXUzMzMzkpGSkZGSkZIzzMzMSEdHSEdISEcAkpFIj0mSAEdJkgBISpEAD/Pz85LZSZJHSZJISpFI8zMzM0fNzs3Ozs3OzTPMzMwd' +
  'Hh4dHh0dHgDNzh08sM0AHrDNAB2vzgAP8/PzzeywzR6wzR2vzh3zMzMzHhYVFhUVFhUWM8zMzG5tbW5tbm5tABYVbttYFgBtWBYAblcVAA/z8/MWg1gWbVgW' +
  'blcVbvX1tTMzMzM4GBc3ORUWOjPMzMwcGx0JCh4cDCc4Nxw5GxInMhs6ABwcDyhPADhVGxInMhs6ABwcDygz8zMzMxtOIiFNTx8gUDPMzMxCQUMbHERCHl9O' +
  'TUKFCw8PLAtQAEIMEhBvAE6RCw8PLAtQAEIMEhAt8zMzM0EwFBMvMRESMjPMzMwqKSsPECwqEjswLypVBQkHGAUyACoGDAjw/1l/Xw9sj0MwWxgyKhlVVVUA' +
  'ABACAEAIAABACAABACAAAAAQAgBACAAAQAgAAQAgAAAAEAIAQAgAAEAIAAEAIAAAAQFAAAAAgAAAAAAAAF9fXwAAoACfoJ+gn6CfoJ+gn6CfoJ+gn6CfoJ+g' +
  'n6CfoJ+gwAAAAJ/AAAAAoK8AwAC/wL/Av8C/wL/Av8C/wL/Av8C/wL/Av8C/wL/AwAAAAL/AAAAAwL8AwAC/wL/Av8C/wL/Av8C/wL/Av8C/wL/Av8C/wL/A' +
  'wAAAAL/AAAAAwFVVVQhmZmZmZmZmQAAAAIAAAABIZmZmZmZmZkAAAACAAAAASGZmZmZmZmZAAAAAgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACgA9wAA' +
  'APcA2P/1oF9fXwA2jjb3xI3EAMOOw/jDjsP8w442w47D+MOOwwDEjcT3wAAAAHqAAAAAfTaONvfEjcQAw47D+MOOw/zDjjbDjsP4w47DAMSNxPfAAAAAeoAA' +
  'AAB9No4298SNxADDjsP4w47D/MOONsOOw/jDjsMAxI3E98AAAAB6gAAAAFVVVRlECIjJIiARBcAAAAAEQAAAAJlECIjJIiARBcAAAAAEQAAAAJlECIjJIiAR' +
  'BcAAAAAEQAAAAF9fXwA1jTUAxI3E98SNxADDjsMAxI3Cw47DAMSNxPfEjcQAwAAAAH+AAAAAfDWNNQDEjcT3xI3EAMOOwwDEjcLDjsMAxI3E98SNxADAAAAA' +
  'f4AAAAB8NY01AMSNxPfEjcQAw47DAMSNwsOOwwDEjcT3xI3EAMAAAAB/gAAAAFVVVSYERAgHIBEQBMAAAAAGQAAAACYERAgHIBEQBMAAAAAGQAAAACYERAgH' +
  'IBEQBMAAAAAGQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAMEBPgD/AQAAAOHwEBAAEAAQABAAEAAQAB8P8CAQABAAEAAQABAAEAAf' +
  'DfAgIBAAEAAQABAAEAAQDB/wIAAQABAAEAAQABAAEA8f/hAQABAAEAAQABAAEAAfD/AgEAAQABAAEAAQABAAHw3wICAQABAAEAAQABAAEAwf8CAAEAAQABAA' +
  'EAAQABAPH/4QEAAQABAAEAAQABAAHw/wIBAAEAAQABAAEAAQAB8N8CAgEAAQABAAEAAQABAMH/AgABAAEAAQABAAEAAQDx8ABCAgIgMAXwQgICIDAF8EICAi' +
  'AwB2h1ZneKmGZYlomAFpAAAAAAA=' 
  ;

const bytes = (b64) => { const b = Buffer.from(b64, 'base64'); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };

/** A canonical (uncompressed) GLB of one triangle under Model/Part/Tri, as cad/fc_scripts/glb_writer.py writes it. */
function plainGlb() {
  const pos = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const idx = new Uint32Array([0, 1, 2]);
  const bin = Buffer.concat([Buffer.from(pos.buffer), Buffer.from(idx.buffer)]);
  const doc = { asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [2] }],
    nodes: [{ name: 'Tri', mesh: 0 }, { name: 'Part', children: [0], extras: { family: 'Part', explode: 0.1 } }, { name: 'Model', children: [1] }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, material: 0 }] }], materials: [{ name: 'Part' }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] },
      { bufferView: 1, componentType: 5125, count: 3, type: 'SCALAR' }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 }, { buffer: 0, byteOffset: 36, byteLength: 12 }], buffers: [{ byteLength: bin.length }] };
  let js = Buffer.from(JSON.stringify(doc)); js = Buffer.concat([js, Buffer.alloc((4 - js.length % 4) % 4, 0x20)]);
  const head = Buffer.alloc(12); head.writeUInt32LE(0x46546c67, 0); head.writeUInt32LE(2, 4); head.writeUInt32LE(28 + js.length + bin.length, 8);
  const cj = Buffer.alloc(8); cj.writeUInt32LE(js.length, 0); cj.writeUInt32LE(0x4e4f534a, 4);
  const cb = Buffer.alloc(8); cb.writeUInt32LE(bin.length, 0); cb.writeUInt32LE(0x004e4942, 4);
  const all = Buffer.concat([head, cj, js, cb, bin]);
  return all.buffer.slice(all.byteOffset, all.byteOffset + all.byteLength);
}

test('the packed GLB is really compressed: a loader without the meshopt decoder refuses it', async () => {
  await assert.rejects(new GLTFLoader().parseAsync(bytes(PACKED), ''));
});

test('the viewer loader decodes it with the node tree, extras and CAD-frame float positions of the canonical file', async () => {
  const gltf = await makeGltfLoader().parseAsync(bytes(PACKED), '');
  const model = gltf.scene.getObjectByName('Model');
  assert.deepEqual(model.children.map((n) => n.name), ['Core', 'Coils']);
  const coils = model.getObjectByName('Coils');
  assert.deepEqual(coils.children.map((n) => n.name), ['Coil_01', 'Coil_02']);
  assert.equal(coils.userData.explode, -0.35);
  assert.equal(coils.getObjectByName('Coil_02').userData.thread.pitch_mm, 0.5);
  gltf.scene.traverse((o) => {                       // no dequantisation transform anywhere: positions are read in the CAD frame
    assert.deepEqual(o.position.toArray(), [0, 0, 0], o.name);
    assert.deepEqual(o.scale.toArray(), [1, 1, 1], o.name);
    if (o.isMesh) assert.ok(o.geometry.getAttribute('position').array instanceof Float32Array, o.name);
  });
  const core = new Box3().setFromObject(model.getObjectByName('Core'));
  for (const [got, want] of [[core.min.toArray(), [-20, -20, -5]], [core.max.toArray(), [20, 20, 5]]])
    got.forEach((v, i) => assert.ok(Math.abs(v - want[i]) < 0.01, `${got} vs ${want}`));
  let tris = 0; gltf.scene.traverse((o) => { if (o.isMesh) tris += o.geometry.index.count / 3; });
  assert.equal(tris, 3 * 4 * 2 * 8);                 // three tubes x 4 faces x 2 triangles x 8 facets
});

test('a canonical, uncompressed GLB still loads through the same loader', async () => {
  const gltf = await makeGltfLoader().parseAsync(plainGlb(), '');
  const part = gltf.scene.getObjectByName('Part');
  assert.equal(part.userData.explode, 0.1);
  assert.deepEqual(part.children.map((n) => n.name), ['Tri']);
});

test('the model viewer parses every GLB with the meshopt-enabled loader', () => {
  const src = readFileSync(new URL('../modelViewer.ts', import.meta.url), 'utf8');
  assert.match(src, /makeGltfLoader\(\)\.parseAsync\(/);
  assert.doesNotMatch(src, /new GLTFLoader\(/);
});

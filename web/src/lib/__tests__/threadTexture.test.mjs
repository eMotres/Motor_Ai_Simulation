// node --test - the thread relief maths of the 3D model tab (lib/threadTexture.ts): the GLB record, the profile, the baked normal
// map, the cylindrical projection (pitch along the axis, turns round it, the atan2 seam).  Imports the .ts itself (Node >= 22.18 / 24).
import test from 'node:test';
import assert from 'node:assert/strict';

let T = null;
try { T = await import('../threadTexture.ts'); } catch { /* old Node */ }
const t = T ? test : test.skip;

const rec = { thread: { kind: 'external', d_mm: 2.5, pitch_mm: 0.45, length_mm: 17, standard: 'ISO 261', designation: 'M2.5x0.45',
  radius_mm: 1.215, axis: [0, 0, 1], origin: [14.85, 0, -3], hand: 'right' } };

t('the GLB thread record is parsed; old or broken records give null', () => {
  const p = T.parseThread(rec);
  assert.equal(p.kind, 'external'); assert.equal(p.pitch_mm, 0.45); assert.deepEqual(p.axis, [0, 0, 1]);
  assert.equal(T.threadLabel(p), 'M2.5x0.45, ISO 261');
  assert.equal(T.parseThread(undefined), null);
  assert.equal(T.parseThread({}), null);                                           // an older GLB: no extras
  assert.equal(T.parseThread({ thread: { kind: 'sideways', d_mm: 2, pitch_mm: 0.4 } }), null);
  assert.equal(T.parseThread({ thread: { kind: 'internal', d_mm: 2, pitch_mm: 0 } }), null);
  const bare = T.parseThread({ thread: { kind: 'internal', d_mm: 3, pitch_mm: 0.5 } });
  assert.equal(bare.designation, 'M3x0.5'); assert.equal(bare.standard, 'ISO 261'); assert.equal(bare.axis, null);
});

t('the profile is the ISO form: periodic, depth 0.54 P, flanks at 60 degrees, crest P/8 and root P/4 wide', () => {
  let lo = 1, hi = 0;
  for (let k = 0; k < 1000; k++) {
    const h = T.profileHeight(k / 1000);
    lo = Math.min(lo, h); hi = Math.max(hi, h);
    assert.ok(Math.abs(T.profileHeight(k / 1000 + 3) - h) < 1e-9);                  // periodic
  }
  assert.equal(lo, 0); assert.equal(hi, T.THREAD_DEPTH);
  const flank = (1 - 1 / 8 - 1 / 4) / 2;
  assert.ok(Math.abs(T.THREAD_DEPTH / flank - Math.tan(Math.PI / 3)) < 0.01);       // 60 deg thread: flank slope tan 60
  assert.equal(T.profileHeight(0.5), T.THREAD_DEPTH);                               // crest centre
  assert.equal(T.profileHeight(0.0), 0);                                            // root centre
  assert.ok(Math.abs(T.slope(0.25) - Math.sqrt(3)) < 0.05);                         // on the rising flank
  assert.ok(Math.abs(T.slope(0.75) + Math.sqrt(3)) < 0.05);
});

t('the baked normal map is a tileable diagonal pattern of unit normals', () => {
  const n = 64;
  const m = T.buildThreadNormalMap(n);
  assert.equal(m.length, n * n * 4);
  const px = (i, j) => { const o = 4 * (j * n + i); return [m[o], m[o + 1], m[o + 2], m[o + 3]]; };
  for (let k = 0; k < 20; k++) {
    const i = (k * 7) % n, j = (k * 13) % n;
    assert.deepEqual(px(i, j), px((i + 1) % n, (j + 1) % n));                       // constant along the helix diagonal (v - u const)
    const [r, g, b, a] = px(i, j).map((c, q) => (q < 3 ? (c / 255) * 2 - 1 : c));
    assert.ok(Math.abs(Math.hypot(r, g, b) - 1) < 0.02); assert.equal(a, 255); assert.ok(b > 0);
  }
  let steep = 0;
  for (let i = 0; i < n; i++) if (Math.abs((px(i, 0)[1] / 255) * 2 - 1) > 0.7) steep++;
  assert.ok(steep > n / 3 && steep < (n * 2) / 3);                                  // the two flanks are about 2 x 31 % of the pitch
});

t('normalScale corrects the helix factor to the real radius; left hand mirrors it', () => {
  const [sx, sy] = T.normalScaleFor(1.0, 0.4);
  assert.ok(Math.abs(sx - 0.4 / (2 * Math.PI) / T.RHO_REF) < 1e-9); assert.equal(sy, 1);
  assert.ok(T.normalScaleFor(2.5, 0.8)[0] < T.normalScaleFor(1.25, 0.8)[0]);        // bigger radius -> smaller helix angle
  assert.equal(T.normalScaleFor(1, 0.4, 'left')[0], -sx);
});

t('the axis frame is orthonormal and right-handed', () => {
  for (const ax of [[0, 0, 1], [1, 0, 0], [0.3, -0.5, 0.8], [0, 0, -2]]) {
    const { e1, e2, a } = T.axisFrame(ax);
    const d = (p, q) => p[0] * q[0] + p[1] * q[1] + p[2] * q[2];
    assert.ok(Math.abs(d(e1, e2)) < 1e-12 && Math.abs(d(e1, a)) < 1e-12 && Math.abs(d(e2, a)) < 1e-12);
    assert.ok(Math.abs(d(e1, e1) - 1) < 1e-12);
    const c = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]];
    assert.ok(Math.abs(d(c, a) - 1) < 1e-12);                                       // e1 x e2 = axis
  }
});

// a triangle strip round a cylinder of radius r about an axis through `origin`: `seg` segments, one turn, z0..z1
function cylinder(r, z0, z1, seg, origin = [0, 0, 0], start = 0) {
  const p = [];
  const at = (k, z) => { const a = start + (2 * Math.PI * k) / seg; return [origin[0] + r * Math.cos(a), origin[1] + r * Math.sin(a), z]; };
  for (let k = 0; k < seg; k++) {
    p.push(...at(k, z0), ...at(k + 1, z0), ...at(k + 1, z1), ...at(k, z0), ...at(k + 1, z1), ...at(k, z1));
  }
  return p;
}

t('v advances exactly one per pitch along the axis, u one per turn (right hand)', () => {
  const pitch = 0.4;
  const pos = cylinder(1, 0, 6, 24);
  const uv = T.threadUVs(pos, [0, 0, 1], [0, 0, 0], pitch);
  assert.equal(uv.length, (pos.length / 3) * 2);
  let vmin = Infinity, vmax = -Infinity;
  for (let i = 0; i < uv.length; i += 2) { vmin = Math.min(vmin, uv[i + 1]); vmax = Math.max(vmax, uv[i + 1]); }
  assert.ok(Math.abs(vmin) < 1e-6); assert.ok(Math.abs(vmax - 6 / pitch) < 1e-4);   // 15 pitches over 6 mm
  // vertex at angle 90 deg, z = 0.4: one pitch up and a quarter turn round
  const q = T.threadUVs([1, 0, 0, 0, 1, 0.4, 0, 1, 0.4], [0, 0, 1], [0, 0, 0], pitch);
  const du = q[2] - q[0], dv = q[3] - q[1];
  assert.ok(Math.abs(Math.abs(du) - 0.25) < 1e-6 && Math.abs(dv - 1) < 1e-6);
  const left = T.threadUVs([1, 0, 0, 0, 1, 0.4, 0, 1, 0.4], [0, 0, 1], [0, 0, 0], pitch, 'left');
  assert.ok(Math.abs(left[2] - left[0] + (q[2] - q[0])) < 1e-6);                    // mirrored u
});

t('u is continuous across the atan2 seam, whatever the start angle and the axis position', () => {
  for (const [start, origin] of [[0, [0, 0, 0]], [Math.PI - 0.1, [0, 0, 0]], [3.0, [14.85, -2, 0]], [-Math.PI, [5, 5, 5]]]) {
    const pos = cylinder(1.25, -1, 3, 21, origin, start);
    const uv = T.threadUVs(pos, [0, 0, 1], origin, 0.45);
    for (let tri = 0; tri < uv.length / 6; tri++) {
      const us = [uv[6 * tri], uv[6 * tri + 2], uv[6 * tri + 4]];
      assert.ok(Math.max(...us) - Math.min(...us) < 0.1, `triangle ${tri} spans ${us}`);   // one segment = 1/21 turn
    }
  }
});

t('the helix: points on one right-hand helix of the thread pitch share the texture phase s = v - u', () => {
  const pitch = 0.45, r = 1.25;
  const pts = [];
  for (let k = 0; k < 3; k++) {
    const a = 0.7 + k * 1.1;                                                       // along a right-hand helix z = pitch * a / 2 pi
    pts.push(r * Math.cos(a), r * Math.sin(a), (pitch * a) / (2 * Math.PI));
  }
  const uv = T.threadUVs(pts, [0, 0, 1], [0, 0, 0], pitch);
  const frac = (x) => x - Math.floor(x);
  const s = [0, 1, 2].map((k) => frac(uv[2 * k + 1] - uv[2 * k]));
  assert.ok(Math.abs(s[0] - s[1]) < 1e-5 && Math.abs(s[1] - s[2]) < 1e-5);
});

t('axis and origin fall back to Z through the middle of the mesh when the record has none', () => {
  const pos = cylinder(1, 0, 4, 12, [3, -2, 0]);
  const r = T.resolveAxis({ axis: null, origin: null }, pos);
  assert.deepEqual(r.axis, [0, 0, 1]);
  assert.ok(Math.abs(r.origin[0] - 3) < 1e-6 && Math.abs(r.origin[1] + 2) < 1e-6);
  assert.deepEqual(T.resolveAxis({ axis: [1, 0, 0], origin: [1, 2, 3] }, pos), { axis: [1, 0, 0], origin: [1, 2, 3] });
});

/**
 * The canvas for the 3D tab.
 *
 * Camera, lighting and the overlay Viewcube are the Geometry/Mesh tab's, not a
 * second 3D stack: `CameraSync` + `ViewcubeNavigation` are imported from
 * `viewer3d/MotorScene` so the cube in the corner drives this scene exactly the
 * way it drives that one, and "FRONT" means the same thing on both.
 *
 * What is drawn is HALF A MACHINE: one anti-periodic sector of the `z >= 0`
 * half.  The mirrored and repeated copies are `<group>` transforms of the SAME
 * geometry — they cost no payload, they add no information, and the panel that
 * mounts this scene is required to say which of them are switched on.  Under
 * anti-periodicity |B| is identical in every copy, which is why mirroring a
 * MAGNITUDE is honest; a signed component would not be, and none is drawn.
 *
 * Units are millimetres, the cross-section is in XY, the stack runs along +Z —
 * the same convention as the rest of the viewer.
 */
import React, { useEffect, useMemo, useRef } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, OrthographicCamera, Grid } from '@react-three/drei';
import * as THREE from 'three';
import { ViewcubeNavigation, CameraSync } from '../viewer3d/MotorScene';
import { PART_COLORS } from '../../lib/partColors';
import { jet01, bandColor, N_BANDS, BAND_VERT, BAND_FRAG } from '../simulation/fieldView';
import type { GeometryPayload, SurfacePayload, SurfaceRegion } from './api';

/** Region name → the project's single source of truth for part colour.
 *
 * `polarity` is the magnet's OWN sign, read off its magnetisation vector by the
 * backend — never guessed from the index.  The sector holds an odd number of
 * poles, so index parity is right in sector 0 and wrong in sector 1, and the
 * repeated view would put two same-poled magnets side by side at the seam.
 */
export function partColor(name: string, kind: string, polarity = 1): string {
  if (kind === 'magnet') return polarity >= 0 ? PART_COLORS.magnetN : PART_COLORS.magnetS;
  if (name === 'stator') return PART_COLORS.statorIron;
  if (name === 'rotor') return PART_COLORS.rotorIron;
  if (name === 'shaft') return PART_COLORS.shaft;
  if (name === 'air') return '#5b6b80';
  return PART_COLORS.statorIron;
}

// --------------------------------------------------------------------------
// surface geometry (mesh + field panels)
// --------------------------------------------------------------------------

/** Indexed positions + values → geometry, in one of TWO honest readings.
 *
 * ELEMENT (smooth = false): non-indexed, one flat colour per triangle.  A value
 * belongs to one tet; painting it on shared vertices would average two
 * elements' fields into a gradient neither solve computed.  This is the field
 * as solved, and the facets ARE the discretisation.
 *
 * NODAL (smooth = true): indexed, one value per shared vertex (area-averaged
 * server-side), interpolated across the face by the GPU and banded per PIXEL by
 * the same shader the 2D field view uses — so the bands are true iso-levels of
 * the interpolated field rather than a per-triangle staircase.  This is what
 * ANSYS shows by default, and like ANSYS the element view stays one click away:
 * the jump between neighbours is the discretisation error, and smoothing hides
 * it.
 */
function buildSurfaceGeometry(
  r: SurfaceRegion,
  colorFor: ((v: number, tri: number) => [number, number, number]) | null,
  smooth = false,
  norm: ((v: number) => number) | null = null,
): THREE.BufferGeometry {
  if (smooth && r.values_node && norm) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(r.positions, 3));
    geo.setIndex(r.indices);
    const nv = r.values_node.length;
    const a = new Float32Array(nv);
    for (let i = 0; i < nv; i++) {
      const v = r.values_node[i];
      a[i] = Number.isFinite(v) ? norm(v) : 0;
    }
    geo.setAttribute('aVal', new THREE.BufferAttribute(a, 1));
    geo.computeVertexNormals();
    return geo;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(r.positions, 3));
  geo.setIndex(r.indices);
  const flat = geo.toNonIndexed();
  geo.dispose();
  if (colorFor && r.values) {
    const n = r.values.length;
    const col = new Float32Array(n * 9);
    for (let t = 0; t < n; t++) {
      const [cr, cg, cb] = colorFor(r.values[t], t);
      for (let k = 0; k < 3; k++) {
        col[t * 9 + k * 3] = cr;
        col[t * 9 + k * 3 + 1] = cg;
        col[t * 9 + k * 3 + 2] = cb;
      }
    }
    flat.setAttribute('color', new THREE.BufferAttribute(col, 3));
  }
  flat.computeVertexNormals();
  return flat;
}

interface SurfaceProps {
  payload: SurfacePayload;
  /** null → colour by part; otherwise the field's normaliser + ramp. */
  scale: { vmin: number; vmax: number } | null;
  wireframe: boolean;
  opacity: number;
  /** -1 in an anti-periodic copy: every magnet in it is reversed. */
  polaritySign: number;
  /** true → nodal (interpolated) field; false → the per-element field solved. */
  smooth?: boolean;
}

const Surfaces: React.FC<SurfaceProps> = ({
  payload, scale, wireframe, opacity, polaritySign, smooth = true,
}) => {
  const built = useMemo(() => {
    // BANDED, not a continuous ramp — N_BANDS of them, exactly as the 2D field
    // view does it, so a colour means the same thing on both screens.  Values
    // outside the percentile range land in the end band and are not given a
    // colour of their own: a clipped value must read as "at least this", never
    // as a distinct feature.
    const colorFor = scale
      ? (v: number) => {
          const t = Math.max(0, Math.min(1, (v - scale.vmin) / Math.max(scale.vmax - scale.vmin, 1e-12)));
          const k = Math.min(N_BANDS - 1, Math.floor(t * N_BANDS));
          const [r, g, b] = bandColor(k, N_BANDS);
          return [r / 255, g / 255, b / 255] as [number, number, number];
        }
      : null;
    // The shader wants 0..1; the CPU path wants the raw value.  ONE normaliser
    // for both so a colour cannot mean two things on the same screen.
    const norm = scale
      ? (v: number) => Math.max(0, Math.min(1,
          (v - scale.vmin) / Math.max(scale.vmax - scale.vmin, 1e-12)))
      : null;
    const useSmooth = !!scale && smooth;
    return payload.regions.map((r) => ({
      name: r.name,
      kind: r.kind,
      geo: buildSurfaceGeometry(r, colorFor, useSmooth, norm),
      smooth: useSmooth && !!r.values_node,
      color: partColor(r.name, r.kind, (r.polarity ?? 1) * polaritySign),
      isAir: r.name === 'air',
    }));
  }, [payload, scale, polaritySign, smooth]);

  useEffect(() => () => { built.forEach((b) => b.geo.dispose()); }, [built]);

  return (
    <>
      {built.map((b) => (
        <group key={b.name}>
          <mesh geometry={b.geo}>
            {/* NODAL: band the INTERPOLATED value per pixel with the same
                shader the 2D field view uses, so a band edge is a true
                iso-level of the field instead of a triangle boundary, and the
                colours match the other screen exactly.  Unlit on purpose — a
                contour plot that is also shaded reads its own shadows as field
                gradient, which is the one thing this picture must not do. */}
            {b.smooth ? (
              <shaderMaterial
                key={`band_${b.name}`}
                vertexShader={BAND_VERT}
                fragmentShader={BAND_FRAG}
                uniforms={{ uBands: { value: N_BANDS }, uIso: { value: 0.55 } }}
                side={THREE.DoubleSide}
                transparent={opacity < 1 || b.isAir}
                opacity={b.isAir ? Math.min(opacity, 0.25) : opacity}
                polygonOffset
                polygonOffsetFactor={1}
                polygonOffsetUnits={1}
              />
            ) : (
            /* metalness 0: there is no environment map in this scene, and a
                metallic surface without one renders black — which is how a
                perfectly good mesh ends up looking like an empty screen.
                polygonOffset pushes the fill a hair back so the wireframe sits
                on it instead of z-fighting it. */
            <meshStandardMaterial
              vertexColors={!!scale}
              color={scale ? '#ffffff' : b.color}
              side={THREE.DoubleSide}
              transparent={opacity < 1 || b.isAir}
              opacity={b.isAir ? Math.min(opacity, 0.25) : opacity}
              roughness={0.85}
              metalness={0.0}
              emissive={scale ? '#000000' : b.color}
              emissiveIntensity={scale ? 0 : 0.35}
              polygonOffset
              polygonOffsetFactor={1}
              polygonOffsetUnits={1}
              flatShading
            />
            )}
          </mesh>
          {/* Edges are LIGHT on purpose: the part palette is dark slate, and a
              dark wireframe over dark iron is a mesh view that shows no mesh. */}
          {wireframe && (
            <lineSegments>
              <wireframeGeometry args={[b.geo]} />
              <lineBasicMaterial color="#e2e8f0" transparent opacity={0.22} />
            </lineSegments>
          )}
        </group>
      ))}
    </>
  );
};

// --------------------------------------------------------------------------
// solid bodies (geometry panel) — extruded straight from the CAD rings
// --------------------------------------------------------------------------

function shapeOf(part: { outer: number[][]; holes: number[][][] }): THREE.Shape {
  const s = new THREE.Shape();
  part.outer.forEach(([x, y], i) => (i === 0 ? s.moveTo(x, y) : s.lineTo(x, y)));
  s.closePath();
  part.holes.forEach((h) => {
    const p = new THREE.Path();
    h.forEach(([x, y], i) => (i === 0 ? p.moveTo(x, y) : p.lineTo(x, y)));
    p.closePath();
    s.holes.push(p);
  });
  return s;
}

/** An annulus SECTOR as a shape — the end-turn band covers the slots, over the
 *  same angular span as the iron beneath it and no more. */
function annulusSector(rIn: number, rOut: number, spanDeg: number): THREE.Shape {
  const n = Math.max(Math.round(spanDeg / 3), 8);
  const a = (spanDeg * Math.PI) / 180;
  const s = new THREE.Shape();
  for (let i = 0; i <= n; i++) {
    const t = (a * i) / n;
    const x = rOut * Math.cos(t), y = rOut * Math.sin(t);
    i === 0 ? s.moveTo(x, y) : s.lineTo(x, y);
  }
  for (let i = n; i >= 0; i--) {
    const t = (a * i) / n;
    s.lineTo(rIn * Math.cos(t), rIn * Math.sin(t));
  }
  s.closePath();
  return s;
}

interface SolidsProps {
  geom: GeometryPayload;
  showCoils: boolean;
  showEndTurns: boolean;
  showMagnetArrows: boolean;
  /** Draw only the axial half the model actually solves. */
  modelledHalfOnly: boolean;
  sectorDeg: number;
  polaritySign: number;
  /** Mesh / field views: draw the WINDING only.  The iron and the magnets are
   *  already on screen as meshed surfaces, and drawing the CAD solid on top of
   *  its own mesh is two pictures of one part fighting for the same pixels. */
  coilsOnly?: boolean;
}

const Solids: React.FC<SolidsProps> = ({
  geom, showCoils, showEndTurns, showMagnetArrows, modelledHalfOnly, sectorDeg,
  polaritySign, coilsOnly = false,
}) => {
  // WHAT IS ON SCREEN DECIDES THE EXTENT.  Beside a mesh (coilsOnly) the drawn
  // machine is the MODELLED HALF, 0..stack/2 — the mesh has nothing below z = 0,
  // and the mirror switch is what puts the other half there.  The copper used to
  // ignore that and extrude the full -6..+6 stack, so it ran straight through
  // the mirror plane and out the far end of a half-length mesh: conductors that
  // "the mirror does not cut", because they were never the half in the first
  // place.  In the geometry view (no mesh) the switch still rules.
  const halfOnly = coilsOnly || modelledHalfOnly;
  const zLo = halfOnly ? geom.extrusion.modelled_z_lo_mm : geom.extrusion.z_lo_mm;
  const zHi = halfOnly ? geom.extrusion.modelled_z_hi_mm : geom.extrusion.z_hi_mm;
  const depth = Math.max(zHi - zLo, 1e-6);

  const bodies = useMemo(() => {
    const out: { key: string; geo: THREE.BufferGeometry; color: string; op: number }[] = [];
    if (!coilsOnly) geom.regions.forEach((r) => {
      r.parts.forEach((p, i) => {
        const g = new THREE.ExtrudeGeometry(shapeOf(p), { depth, bevelEnabled: false });
        g.translate(0, 0, zLo);
        out.push({
          key: `${r.name}_${i}`, geo: g,
          color: partColor(r.name, r.kind, (r.polarity ?? 1) * polaritySign), op: 1,
        });
      });
    });
    if (showCoils) {
      geom.coils.sides.forEach((c) => {
        c.parts.forEach((p, i) => {
          const g = new THREE.ExtrudeGeometry(shapeOf(p), { depth, bevelEnabled: false });
          g.translate(0, 0, zLo);
          // Translucent beside a field map: the copper is NOT in the mesh (the
          // scalar-potential model carries the current as a source field, not
          // as a meshed conductor), so a solid opaque bar would read as a
          // region whose field came out zero.
          out.push({ key: `coil_${c.index}_${i}`, geo: g, color: PART_COLORS.copper,
                     op: coilsOnly ? 0.45 : 0.95 });
        });
      });
    }
    return out;
  }, [geom, depth, zLo, showCoils, polaritySign, coilsOnly]);

  useEffect(() => () => { bodies.forEach((b) => b.geo.dispose()); }, [bodies]);

  // The end-turn BAND, drawn as what it is: a translucent slab of the height
  // the model gives the end winding, not a bundle of bent wires the model does
  // not have.
  const hEw = geom.coils.end_turn_band_mm;
  const rOut = geom.machine.stator_od_mm / 2;
  const rIn = geom.machine.rotor_od_mm / 2;
  const bandGeo = useMemo(() => {
    if (!(hEw > 0)) return null;
    const g = new THREE.ExtrudeGeometry(
      annulusSector(geom.machine.stator_bore_mm / 2, rOut, sectorDeg),
      { depth: hEw, bevelEnabled: false },
    );
    return g;
  }, [hEw, rOut, sectorDeg, geom.machine.stator_bore_mm]);
  useEffect(() => () => { bandGeo?.dispose(); }, [bandGeo]);

  return (
    <>
      {bodies.map((b) => (
        <mesh key={b.key} geometry={b.geo}>
          <meshStandardMaterial
            color={b.color} side={THREE.DoubleSide} roughness={0.6}
            metalness={0.3} transparent={b.op < 1} opacity={b.op} flatShading
          />
        </mesh>
      ))}

      {/* The end-turn BAND: the axial space h_ew the model gives the end turn,
          over the slots, over the sector — a volume the winding occupies, not a
          wire it is made of. */}
      {showEndTurns && bandGeo && [1, -1].map((s) => (
        (modelledHalfOnly && s < 0) ? null : (
          <mesh key={`ew${s}`} geometry={bandGeo}
            position={[0, 0, s > 0 ? zHi : zLo - hEw]}>
            <meshStandardMaterial
              color={PART_COLORS.copper} side={THREE.DoubleSide}
              transparent opacity={0.22} roughness={0.9} depthWrite={false}
            />
          </mesh>
        )
      ))}

      {showMagnetArrows && geom.regions.filter((r) => r.kind === 'magnet').map((r) => {
        if (!r.centroid_mm || r.M_dir_deg === null) return null;
        const [cx, cy] = r.centroid_mm;
        const L = Math.max((rOut - rIn) * 0.45, 1.5);
        const a = (r.M_dir_deg * Math.PI) / 180 + (polaritySign < 0 ? Math.PI : 0);
        return (
          <arrowHelper
            key={`M_${r.name}`}
            args={[
              new THREE.Vector3(Math.cos(a), Math.sin(a), 0),
              new THREE.Vector3(cx - Math.cos(a) * L * 0.5, cy - Math.sin(a) * L * 0.5, zHi + 0.05),
              L, 0xffe066, L * 0.35, L * 0.22,
            ]}
          />
        );
      })}
    </>
  );
};

// --------------------------------------------------------------------------
// B vectors
// --------------------------------------------------------------------------

const Vectors: React.FC<{
  data: { points: number[]; vectors: number[] };
  vmax: number;
  len: number;
}> = ({ data, vmax, len }) => {
  const geo = useMemo(() => {
    const n = Math.floor(data.points.length / 3);
    const pos = new Float32Array(n * 6);
    const col = new Float32Array(n * 6);
    for (let i = 0; i < n; i++) {
      const px = data.points[i * 3], py = data.points[i * 3 + 1], pz = data.points[i * 3 + 2];
      const bx = data.vectors[i * 3], by = data.vectors[i * 3 + 1], bz = data.vectors[i * 3 + 2];
      const m = Math.hypot(bx, by, bz) || 1e-12;
      const s = len / m;
      pos.set([px, py, pz, px + bx * s, py + by * s, pz + bz * s], i * 6);
      const [r, g, b] = jet01(Math.max(0, Math.min(1, m / Math.max(vmax, 1e-12))));
      col.set([r / 255, g / 255, b / 255, r / 255, g / 255, b / 255], i * 6);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    return g;
  }, [data, vmax, len]);
  useEffect(() => () => geo.dispose(), [geo]);
  return (
    <lineSegments geometry={geo}>
      <lineBasicMaterial vertexColors />
    </lineSegments>
  );
};

// --------------------------------------------------------------------------
// camera
// --------------------------------------------------------------------------

const FitView: React.FC<{ radiusMm: number; controlsRef: React.MutableRefObject<any> }> = ({
  radiusMm, controlsRef,
}) => {
  const { camera, size } = useThree();
  useEffect(() => {
    if (size.width === 0 || !(camera as any).isOrthographicCamera) return;
    const cam = camera as THREE.OrthographicCamera;
    const r = Math.max(radiusMm, 1) * 1.35;
    const aspect = size.width / Math.max(size.height, 1);
    cam.left = -r * aspect; cam.right = r * aspect;
    cam.top = r; cam.bottom = -r;
    cam.zoom = 1;
    cam.updateProjectionMatrix();
    if (controlsRef.current) {
      controlsRef.current.target.set(0, 0, 0);
      controlsRef.current.update();
    }
  }, [radiusMm, size.width, size.height, camera, controlsRef]);
  return null;
};

const ResizeFix: React.FC = () => {
  const { gl, setSize } = useThree();
  useEffect(() => {
    const parent = gl.domElement.parentElement;
    if (!parent) return;
    let lw = 0, lh = 0;
    const apply = () => {
      const r = parent.getBoundingClientRect();
      if (r.width > 1 && r.height > 1 && (Math.abs(r.width - lw) > 1 || Math.abs(r.height - lh) > 1)) {
        lw = r.width; lh = r.height;
        setSize(r.width, r.height);
      }
    };
    const ro = new ResizeObserver(apply);
    ro.observe(parent);
    apply();
    const t = setTimeout(apply, 80);
    return () => { ro.disconnect(); clearTimeout(t); };
  }, [gl, setSize]);
  return null;
};

// --------------------------------------------------------------------------
// the scene
// --------------------------------------------------------------------------

export interface SceneProps {
  /** Geometry panel input (null when a mesh/field surface is shown instead). */
  geom: GeometryPayload | null;
  surface: SurfacePayload | null;
  scale: { vmin: number; vmax: number } | null;
  vectors: { points: number[]; vectors: number[] } | null;
  wireframe: boolean;
  showCoils: boolean;
  showEndTurns: boolean;
  showMagnetArrows: boolean;
  /** Repeat the sector round the ring (n_sectors copies). */
  fullRing: boolean;
  /** Mirror the solved z >= 0 half through the z = 0 plane. */
  mirrorZ: boolean;
  /** Geometry panel only: draw just the half the solver models. */
  modelledHalfOnly: boolean;
  sectorDeg: number;
  nSectors: number;
  antiperiodic: boolean;
  radiusMm: number;
  showGrid: boolean;
}

const Static3DScene: React.FC<SceneProps> = ({
  geom, surface, scale, vectors, wireframe, showCoils, showEndTurns,
  showMagnetArrows, fullRing, mirrorZ, modelledHalfOnly, sectorDeg, nSectors,
  antiperiodic, radiusMm, showGrid,
}) => {
  const controlsRef = useRef<any>(null);
  const vmax = scale ? scale.vmax : 1;

  // `sign` is the anti-periodic sign of THIS copy: every magnet in an odd copy
  // is reversed, and drawing it otherwise would show a machine with two
  // same-poled magnets meeting at the seam.
  const contentFor = (sign: number) => (
    <>
      {geom && (
        <Solids geom={geom} showCoils={showCoils} showEndTurns={showEndTurns}
          sectorDeg={sectorDeg} showMagnetArrows={showMagnetArrows}
          modelledHalfOnly={modelledHalfOnly} polaritySign={sign}
          coilsOnly={!!surface} />
      )}
      {surface && (
        <Surfaces payload={surface} scale={scale} wireframe={wireframe}
          opacity={1} polaritySign={sign} />
      )}
      {vectors && vectors.points.length > 0 && (
        <Vectors data={vectors} vmax={vmax} len={Math.max(radiusMm * 0.05, 0.4)} />
      )}
    </>
  );

  // The sector copies. Copy 0 is the solved one; every other copy is a rigid
  // transform of it and carries no new information — which is exactly why the
  // panel labels the toggle rather than making it the default.
  const copies = fullRing ? Math.max(nSectors, 1) : 1;
  const stack: React.ReactNode[] = [];
  for (let k = 0; k < copies; k++) {
    const rot: [number, number, number] = [0, 0, (k * sectorDeg * Math.PI) / 180];
    const sign = antiperiodic && k % 2 === 1 ? -1 : 1;
    stack.push(<group key={`s${k}`} rotation={rot}>{contentFor(sign)}</group>);
    if (mirrorZ) {
      // Mirroring in z does NOT reverse a magnet: M is in the plane.
      stack.push(
        <group key={`s${k}m`} rotation={rot} scale={[1, 1, -1]}>{contentFor(sign)}</group>,
      );
    }
  }

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <Canvas
        style={{ width: '100%', height: '100%', display: 'block', background: 'var(--panel-2)' }}
        resize={{ debounce: 0 }}>
        <ResizeFix />
        <OrthographicCamera makeDefault position={[0, 0, 250]} near={0.1} far={5000} />
        <FitView radiusMm={radiusMm} controlsRef={controlsRef} />
        {/* Lit brighter than the Geometry tab's scene: this one is read for
            structure (which element, which region, where the cut is), not
            admired for material. */}
        <ambientLight intensity={1.05} />
        <directionalLight position={[100, 100, 100]} intensity={0.7} />
        <directionalLight position={[-100, 50, -100]} intensity={0.4} />
        <directionalLight position={[0, 0, 200]} intensity={0.35} />
        {showGrid && (
          <Grid args={[400, 400]} cellSize={5} cellThickness={0.3} cellColor="#33415a"
            sectionSize={25} sectionThickness={0.6} sectionColor="#475569"
            fadeDistance={400} fadeStrength={1.5}
            rotation={[Math.PI / 2, 0, 0]} position={[0, 0, -radiusMm]} />
        )}
        {stack}
        <OrbitControls ref={controlsRef} enableDamping={false} enableRotate enablePan enableZoom zoomSpeed={1.2} />
        <CameraSync controlsRef={controlsRef} />
        <ViewcubeNavigation controlsRef={controlsRef} />
      </Canvas>
    </div>
  );
};

export default Static3DScene;

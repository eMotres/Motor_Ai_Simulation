/**
 * FemMeshViewer3D — interactive 3D viewer for the 2-D FEM triangle mesh.
 *
 * Uses @react-three/fiber + drei OrbitControls so the user can orbit, pan
 * and zoom the mesh the same way they navigate the Geometry tab.
 *
 * Triangles are placed in the XY plane (z = 0), coloured per domain via
 * vertex colours, and optionally outlined with a wireframe overlay.
 */
import React, { useEffect, useMemo, useRef } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, OrthographicCamera, Grid } from '@react-three/drei';
import { ViewcubeNavigation, CameraSync } from '../viewer3d/MotorScene';
import * as THREE from 'three';

export interface FemMeshPayload {
  n_vertices: number;
  n_triangles: number;
  vertices: [number, number][];          // metres
  triangles: [number, number, number][];
  domain_per_tri: number[];
  extent: [number, number, number, number]; // xmin xmax ymin ymax in metres
  outlines?: { domain: number; loops: [number, number][][] }[];
}

// Domain id → RGB (matches the field map / canvas convention)
const DOMAIN_RGB: Record<number, [number, number, number]> = {
  0:  [0.55, 0.65, 0.78],   // air (light blue-gray, distinguishable from stator/rotor steel)
  1:  [0.34, 0.40, 0.48],   // stator (medium steel gray)
  2:  [0.98, 0.75, 0.14],   // winding
  3:  [0.22, 0.74, 0.97],   // air gap
  4:  [0.23, 0.51, 0.96],   // magnet N
  5:  [0.22, 0.27, 0.32],   // rotor (darker steel gray)
  6:  [0.71, 0.71, 0.75],   // shaft
  7:  [0.45, 0.85, 0.80],   // motion band (slip surface) — teal, distinct from air gap
  8:  [0.22, 0.40, 0.55],   // outer air ring — deep ocean blue
  44: [0.94, 0.27, 0.27],   // magnet S
};

// Boundary stroke color per domain (slightly darker than the fill so it
// reads as a clean outline, not a colour shift).
const DOMAIN_STROKE: Record<number, number> = {
  0:  0x1e293b,
  1:  0x0f172a,
  2:  0xb45309,
  3:  0x0284c7,
  4:  0x1d4ed8,
  5:  0x0b1220,
  6:  0x475569,
  7:  0x0d9488,
  8:  0x1e3a8a,
  44: 0x991b1b,
};

interface MeshShapeProps {
  payload: FemMeshPayload;
  showFill: boolean;
  showWire: boolean;
  showOutlines: boolean;
}

/** Group all triangles by domain → produce one indexed BufferGeometry per
 *  domain id.  Each geometry uses ONLY the vertices referenced by its
 *  triangles, so we get crisp solid colours without any interpolation across
 *  domain boundaries. */
function usePerDomainGeometries(payload: FemMeshPayload):
  Map<number, THREE.BufferGeometry> {
  return useMemo(() => {
    const out = new Map<number, THREE.BufferGeometry>();
    const v = payload.vertices;
    const t = payload.triangles;
    const d = payload.domain_per_tri;
    const S = 1000; // m → mm

    // Bucket triangles by domain
    const byDom: Record<number, number[]> = {};
    for (let i = 0; i < t.length; i++) {
      const dom = d[i];
      (byDom[dom] ??= []).push(i);
    }

    for (const [domStr, triIdx] of Object.entries(byDom)) {
      const dom = Number(domStr);
      // Compact vertex remap: each unique vertex used here gets a new local id
      const remap = new Map<number, number>();
      const positions: number[] = [];
      const indices: number[] = [];
      for (const ti of triIdx) {
        const [a, b, c] = t[ti];
        for (const vid of [a, b, c]) {
          let local = remap.get(vid);
          if (local === undefined) {
            local = positions.length / 3;
            remap.set(vid, local);
            positions.push(v[vid][0] * S, v[vid][1] * S, 0);
          }
          indices.push(local);
        }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions), 3));
      g.setIndex(indices);
      out.set(dom, g);
    }
    return out;
  }, [payload]);
}

/** Smooth CadQuery boundaries as one line geometry per domain.
 *  Drawn ABOVE the mesh + wireframe so the user always sees crisp outlines. */
function useDomainOutlines(payload: FemMeshPayload):
  { dom: number; geo: THREE.BufferGeometry }[] {
  return useMemo(() => {
    if (!payload.outlines) return [];
    const S = 1000;
    const Z = 0.8;   // above fill (z=0) and wire (z=0.5)
    const grouped = new Map<number, number[]>();
    for (const entry of payload.outlines) {
      const arr = grouped.get(entry.domain) ?? [];
      for (const loop of entry.loops) {
        if (loop.length < 2) continue;
        for (let i = 0; i < loop.length; i++) {
          const a = loop[i];
          const b = loop[(i + 1) % loop.length];
          arr.push(a[0] * S, a[1] * S, Z, b[0] * S, b[1] * S, Z);
        }
      }
      grouped.set(entry.domain, arr);
    }
    return [...grouped.entries()].map(([dom, arr]) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(arr), 3));
      return { dom, geo: g };
    });
  }, [payload]);
}

/** Wireframe edges as crisp white lines on top of the fill. */
function useFemWireframe(payload: FemMeshPayload): THREE.BufferGeometry {
  return useMemo(() => {
    const n = payload.n_triangles;
    const positions = new Float32Array(n * 6 * 3);
    const v = payload.vertices;
    const t = payload.triangles;
    const S = 1000;
    let o = 0;
    for (let i = 0; i < n; i++) {
      const [a, b, c] = t[i];
      const ax = v[a][0] * S, ay = v[a][1] * S;
      const bx = v[b][0] * S, by = v[b][1] * S;
      const cx = v[c][0] * S, cy = v[c][1] * S;
      positions[o++] = ax; positions[o++] = ay; positions[o++] = 0.5;
      positions[o++] = bx; positions[o++] = by; positions[o++] = 0.5;
      positions[o++] = bx; positions[o++] = by; positions[o++] = 0.5;
      positions[o++] = cx; positions[o++] = cy; positions[o++] = 0.5;
      positions[o++] = cx; positions[o++] = cy; positions[o++] = 0.5;
      positions[o++] = ax; positions[o++] = ay; positions[o++] = 0.5;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    return geo;
  }, [payload]);
}

const MeshShape: React.FC<MeshShapeProps> = ({
  payload, showFill, showWire, showOutlines,
}) => {
  const domGeos  = usePerDomainGeometries(payload);
  const wireGeo  = useFemWireframe(payload);
  const outGeos  = useDomainOutlines(payload);

  return (
    <group>
      {showFill && Array.from(domGeos.entries()).map(([dom, geo]) => {
        const [r, g, b] = DOMAIN_RGB[dom] ?? [0.5, 0.5, 0.5];
        return (
          <mesh key={dom} geometry={geo}>
            <meshBasicMaterial
              color={new THREE.Color(r, g, b)}
              side={THREE.DoubleSide}
              transparent={false}
              depthWrite
            />
          </mesh>
        );
      })}
      {showWire && (
        <lineSegments geometry={wireGeo}>
          <lineBasicMaterial color={0x000000} transparent opacity={0.25}/>
        </lineSegments>
      )}
      {showOutlines && outGeos.map(({ dom, geo }) => (
        <lineSegments key={`out-${dom}`} geometry={geo}>
          <lineBasicMaterial color={DOMAIN_STROKE[dom] ?? 0x000000}
            linewidth={2}/>
        </lineSegments>
      ))}
    </group>
  );
};

/** Auto-fit the orthographic camera so the mesh fills the view. Resets every
 *  time the payload changes (e.g. user clicks Rebuild). */
const FitView: React.FC<{
  payload: FemMeshPayload | null;
  controlsRef: React.MutableRefObject<any>;
}> = ({ payload, controlsRef }) => {
  const { camera, size, gl } = useThree();
  const lastPayload = useRef<FemMeshPayload | null>(null);

  useEffect(() => {
    if (!payload || size.width === 0) return;
    if (!(camera as any).isOrthographicCamera) return;
    const cam = camera as THREE.OrthographicCamera;
    const [xmin, xmax, ymin, ymax] = payload.extent;
    // Centre the camera on the extent midpoint — so a non-symmetric region
    // (1/4 sector occupying [0,R]×[0,R] instead of [-R,R]²) gets the same
    // generous padding around it as the full motor would.
    const cx = (xmin + xmax) * 0.5 * 1000;       // mm
    const cy = (ymin + ymax) * 0.5 * 1000;
    const r = Math.max(xmax - xmin, ymax - ymin) * 1000 * 0.55;  // mm
    const aspect = size.width / size.height;
    cam.left   = -r * aspect;
    cam.right  =  r * aspect;
    cam.top    =  r;
    cam.bottom = -r;
    cam.zoom   = 1.0;
    cam.position.set(cx, cy, 300);
    cam.lookAt(cx, cy, 0);
    cam.updateProjectionMatrix();
    // OrbitControls keeps the camera locked on its `target` every frame, so
    // we must also move the target — otherwise it pulls the camera back to
    // (0,0,0) immediately after we set it.
    if (controlsRef.current) {
      controlsRef.current.target.set(cx, cy, 0);
      controlsRef.current.update();
    }
    lastPayload.current = payload;
    gl.render(camera as any, camera as any);   // trigger a redraw
  }, [payload, size.width, size.height, camera, gl, controlsRef]);

  return null;
};

interface ViewerProps {
  payload: FemMeshPayload | null;
  showFill?: boolean;
  showWire?: boolean;
  showOutlines?: boolean;
  /** Render a faint reference grid in the XY plane */
  showGrid?: boolean;
}

const FemMeshViewer3D: React.FC<ViewerProps> = ({
  payload, showFill = true, showWire = false, showOutlines = true,
  showGrid = true,
}) => {
  const controlsRef = useRef<any>(null);
  if (!payload || payload.n_triangles === 0) return null;

  return (
    <Canvas style={{ background: '#060d17' }}>
      <OrthographicCamera makeDefault position={[0, 0, 300]} near={0.1} far={5000}/>
      <FitView payload={payload} controlsRef={controlsRef}/>
      <ambientLight intensity={0.9}/>
      <directionalLight position={[100, 100, 100]} intensity={0.5}/>

      {showGrid && (
        <Grid
          args={[400, 400]}
          cellSize={5}
          cellThickness={0.3}
          cellColor="#1e293b"
          sectionSize={25}
          sectionThickness={0.6}
          sectionColor="#334155"
          fadeDistance={250}
          fadeStrength={1.5}
          rotation={[Math.PI / 2, 0, 0]}
          position={[0, 0, -0.5]}
        />
      )}

      <MeshShape payload={payload} showFill={showFill}
        showWire={showWire} showOutlines={showOutlines}/>

      <OrbitControls
        ref={controlsRef}
        enableDamping={false}
        enableRotate
        enablePan
        enableZoom
        zoomSpeed={1.2}
      />
      {/* Drive + follow the shared overlay Viewcube (same as Geometry):
          CameraSync streams this camera's orientation to the cube;
          ViewcubeNavigation reorients this camera when a face is clicked. */}
      <CameraSync controlsRef={controlsRef}/>
      <ViewcubeNavigation controlsRef={controlsRef}/>
    </Canvas>
  );
};

export default FemMeshViewer3D;

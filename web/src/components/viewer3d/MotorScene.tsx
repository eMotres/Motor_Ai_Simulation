import React, { Suspense, useRef, useEffect } from 'react';
import { Canvas, useThree, useFrame } from '@react-three/fiber';
import { OrbitControls, PerspectiveCamera, OrthographicCamera, Grid } from '@react-three/drei';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import { useUIStore, useMotorStore, useBuildTimingStore } from '../../stores/motorStore';
import * as THREE from 'three';
import Viewcube from './Viewcube';
import { ApiMotor2dFlat, ApiMotorExtruded } from './ApiMotorMesh';
import PointCloudMesh from './PointCloudMesh';
import { STLCollection } from './STLMesh';
import ComponentTree from './ComponentTree';
import MaterialBar from './MaterialBar';
import { guardCanvas } from './webglGuard';

// Camera that auto-adjusts to viewport aspect ratio
const FRUSTUM = 300;

const AdaptiveCamera: React.FC = () => {
  const { cameraMode } = useUIStore();
  const { camera, size } = useThree();

  // Guard: avoid NaN on first render before canvas is measured
  const aspect = size.width > 0 && size.height > 0 ? size.width / size.height : 1;

  // Imperatively update frustum whenever canvas size changes — this fixes
  // the case where the initial JSX render fires before R3F has measured the
  // canvas, leaving the camera with a stale (wrong) aspect ratio.
  useEffect(() => {
    if (cameraMode !== 'orthographic') return;
    const cam = camera as THREE.OrthographicCamera;
    if (!cam.isOrthographicCamera) return;
    cam.left   = -FRUSTUM * aspect;
    cam.right  =  FRUSTUM * aspect;
    cam.top    =  FRUSTUM;
    cam.bottom = -FRUSTUM;
    cam.updateProjectionMatrix();
  }, [camera, aspect, cameraMode]);

  if (cameraMode === 'perspective') {
    return <PerspectiveCamera makeDefault position={[0, 0, 250]} fov={50} />;
  }

  return (
    <OrthographicCamera
      makeDefault
      position={[0, 0, 250]}
      near={0.1}
      far={5000}
      left={-FRUSTUM * aspect}
      right={ FRUSTUM * aspect}
      top={   FRUSTUM}
      bottom={-FRUSTUM}
    />
  );
};

// Component to sync camera with viewcube
export const CameraSync: React.FC<{ controlsRef?: React.RefObject<any> }> = ({ controlsRef }) => {
  const { camera } = useThree();
  
  // Initial sync when camera is ready
  useEffect(() => {
    // Apply 180° Y rotation offset to align with ViewCube coordinate system
    const offset = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);
    const adjustedQuat = camera.quaternion.clone().multiply(offset);
    
    window.dispatchEvent(new CustomEvent('mainCameraChange', {
      detail: { quaternion: adjustedQuat }
    }));
  }, [camera]);
  
  useFrame(() => {
    // Apply 180° Y rotation offset to align with ViewCube coordinate system
    const offset = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);
    const adjustedQuat = camera.quaternion.clone().multiply(offset);
    
    window.dispatchEvent(new CustomEvent('mainCameraChange', {
      detail: { quaternion: adjustedQuat }
    }));
  });
  
  return null;
};

// Component to handle viewcube navigation events
export const ViewcubeNavigation: React.FC<{ controlsRef: React.RefObject<any> }> = ({ controlsRef }) => {
  const { camera, invalidate } = useThree();
  const targetPosition = useRef<THREE.Vector3 | null>(null);
  const isAnimating = useRef(false);
  const animationFrame = useRef<number | undefined>(undefined);
  
  useEffect(() => {
    const handleNavigate = (e: CustomEvent) => {
      const { position } = e.detail;

      // Orbit around the CURRENT controls target — i.e. the model centre.
      // For the full motor that's the origin; for a 1/4 sector the fit set it
      // to the sector midpoint (cx,cy). Hard-coding (0,0,0) here was what made
      // a face-click fling the off-centre sector into a corner.
      const center = controlsRef.current?.target
        ? controlsRef.current.target.clone()
        : new THREE.Vector3(0, 0, 0);

      // Use fixed distance for standard views; offset from the model centre.
      const distance = 250;
      const direction = position.clone().normalize();
      const newPosition = center.clone().add(direction.multiplyScalar(distance));

      // Animate camera to new position
      isAnimating.current = true;
      targetPosition.current = newPosition;

      const startPosition = camera.position.clone();
      const startTime = performance.now();
      const duration = 500; // 500ms animation

      const animate = (time: number) => {
        const elapsed = time - startTime;
        const progress = Math.min(elapsed / duration, 1);

        // Ease out cubic
        const eased = 1 - Math.pow(1 - progress, 3);

        camera.position.lerpVectors(startPosition, newPosition, eased);
        camera.lookAt(center);

        if (controlsRef.current) {
          controlsRef.current.target.copy(center);
          controlsRef.current.update();
        }
        // The camera moved outside r3f's knowledge — in demand-mode
        // rendering (see the <Canvas>) every step has to ask for a frame.
        invalidate();

        if (progress < 1) {
          animationFrame.current = requestAnimationFrame(animate);
        } else {
          isAnimating.current = false;
        }
      };
      
      if (animationFrame.current) {
        cancelAnimationFrame(animationFrame.current);
      }
      animationFrame.current = requestAnimationFrame(animate);
    };
    
    window.addEventListener('viewcubeNavigate', handleNavigate as EventListener);
    return () => {
      window.removeEventListener('viewcubeNavigate', handleNavigate as EventListener);
      if (animationFrame.current) {
        cancelAnimationFrame(animationFrame.current);
      }
    };
  }, [camera, controlsRef, invalidate]);
  
  return null;
};

// Demand-mode safety net.  r3f asks for a frame on its own reconciler
// commits, but not for a geometry swapped on a ref, a texture that finished
// loading, or a store change a mesh reads imperatively.  So: any change in
// either store asks for a frame AFTER React has committed and run its
// effects (the timeout), and a few frames are asked for after mount to catch
// the async loads (HDR environment, the first mesh) that touch no store.
// A frame of a still scene is cheap; a still scene that never draws is what
// the first demand-mode build of this viewer showed (2026-09-13).
const SceneInvalidator: React.FC = () => {
  const invalidate = useThree(s => s.invalidate);
  useEffect(() => {
    const ask = () => { window.setTimeout(() => invalidate(), 0); };
    const un1 = useMotorStore.subscribe(ask);
    const un2 = useUIStore.subscribe(ask);
    const timers = [100, 500, 1500, 3000, 6000].map(
      ms => window.setTimeout(() => invalidate(), ms));
    return () => { un1(); un2(); timers.forEach(t => window.clearTimeout(t)); };
  }, [invalidate]);
  return null;
};

// Fits camera to motor once on first load.
// Tracks which camera instance was fitted — if AdaptiveCamera replaces the camera,
// the zoom is re-applied to the new instance.
const FitCameraOnLoad: React.FC<{ controlsRef: React.RefObject<any> }> = ({ controlsRef }) => {
  const { camera, size, invalidate } = useThree();
  const { geometry, connectedToApi } = useMotorStore();
  const { cameraMode } = useUIStore();
  const fittedCamera = useRef<THREE.Camera | null>(null);

  // Demand-mode rendering: the fit below runs inside a frame, so ask for one
  // whenever the inputs it waits on arrive (the geometry, the connection, a
  // camera swap) — otherwise the first frames can pass before they do and no
  // later frame comes on its own.
  useEffect(() => { invalidate(); }, [geometry, connectedToApi, cameraMode, camera, invalidate]);

  useFrame(() => {
    if (!connectedToApi) return;
    if (fittedCamera.current === camera) return; // already fitted this exact camera instance

    const outerR: number =
      (geometry as any).stator_outer_radius ||
      ((geometry as any).stator_diameter ? (geometry as any).stator_diameter / 2 : 0);
    if (!outerR || outerR <= 0) return;

    if (cameraMode === 'orthographic') {
      if (!(camera as any).isOrthographicCamera) return; // wait for ortho camera to register
      fittedCamera.current = camera;
      const frustumSize = 300;
      const aspect = size.width / size.height;
      const padding = 1.15;
      const zoom = Math.min(
        frustumSize / (outerR * padding),
        (frustumSize * aspect) / (outerR * padding),
      );
      (camera as THREE.OrthographicCamera).zoom = zoom;
      camera.updateProjectionMatrix();
    } else {
      if (!(camera as any).isPerspectiveCamera) return;
      fittedCamera.current = camera;
      const fov = ((camera as THREE.PerspectiveCamera).fov * Math.PI) / 180;
      camera.position.setZ((outerR * 1.15) / Math.tan(fov / 2));
      camera.lookAt(0, 0, 0);
    }

    if (controlsRef.current) {
      controlsRef.current.target.set(0, 0, 0);
      controlsRef.current.update();
    }
    invalidate();                        // draw the fitted view (demand mode)
  });

  return null;
};

// ─── EXT ↔ 2D toggle button ──────────────────────────────────────────────────
const fmt = (s: number | null) => s == null ? '…' : s < 1 ? `${(s * 1000).toFixed(0)}ms` : `${s.toFixed(1)}s`;

const View2dToggle: React.FC = () => {
  const { renderMode, toggleRenderMode } = useUIStore();
  const { mesh_ext_s, mesh2d_s } = useBuildTimingStore();

  const is2d = renderMode === '2d';
  const timingText = is2d ? `2D: ${fmt(mesh2d_s)}` : `3D: ${fmt(mesh_ext_s)}`;
  const btnBg      = is2d ? '#3b82f6' : '#7c3aed';
  const btnLabel   = is2d ? '2D' : '3D';
  const btnTitle   = is2d
    ? '2D flat cross-section (for simulation) — click for Extruded 3D'
    : 'Extruded 3D (fast, no CadQuery) — click for 2D flat';

  return (
    <div style={{ position: 'absolute', bottom: 12, right: 12, zIndex: 20, display: 'flex', alignItems: 'center', gap: 6 }}>
      <span style={{
        fontSize: 11, color: '#9ca3af', background: '#111827',
        border: '1px solid #374151', borderRadius: 4,
        padding: '2px 7px', fontFamily: 'monospace',
      }}>
        {timingText}
      </span>
      <button
        onClick={toggleRenderMode}
        title={btnTitle}
        style={{
          padding: '4px 12px', borderRadius: 6, border: '1px solid #4b5563',
          background: btnBg, color: '#fff', fontWeight: 700,
          fontSize: 13, cursor: 'pointer', letterSpacing: 1,
        }}
      >
        {btnLabel}
      </button>
    </div>
  );
};


/** The lighting rig — three lights, no image-based environment.
 *
 *  This used to be drei's `<Environment preset="studio">`, which fetches
 *  `studio_small_03_1k.hdr` from raw.githack.com at render time.  On
 *  emotres.com that origin is CORS-blocked, so the fetch failed on EVERY
 *  viewer mount and threw out of the Canvas; an error boundary caught it and
 *  rendered exactly these three lights instead.  So the HDR never lit a single
 *  frame on production — all it did was flood the console with red ("Access to
 *  fetch … blocked by CORS policy" + "Uncaught: Could not load
 *  studio_small_03_1k.hdr") on every mount, and earlier (2026-09-07) it took
 *  the whole panel down.
 *
 *  Bundling the HDR locally was the alternative and it is not worth it: the 1k
 *  studio map is ~1.5 MB, five times the budget for an asset whose only job is
 *  nicer reflections on the metals.  The rig below is the production look now,
 *  unconditionally — no network, no boundary, no console noise. */
const EnvironmentOrLights: React.FC<{ intensity: number }> = ({ intensity }) => (
  <>
    <hemisphereLight intensity={0.9 * intensity} groundColor="#444" />
    <directionalLight position={[3, 5, 4]} intensity={1.2 * intensity} />
    <directionalLight position={[-4, -2, -3]} intensity={0.4 * intensity} />
  </>
);

const MotorScene: React.FC<{ force3d?: boolean }> = ({ force3d }) => {
  const { showGrid, showAxes, envIntensity, setSelectedPart } = useUIStore();
  // Materials tab opens in FULL 3D regardless of the leftover render mode
  // (user 2026-08-25: "нужно сделать то же самое, как в геометрии") — the
  // 2D/3D toggle still works afterwards.
  const { renderMode: _rm, toggleRenderMode: _trm } = useUIStore();
  useEffect(() => {
    if (force3d && _rm === '2d') _trm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [force3d]);
  const controlsRef = useRef<any>(null);

  return (
    <>
      {/* frameloop="demand" (2026-09-13): a frame is drawn when something
          changes — a control move (drei invalidates), a React commit in the
          scene, an explicit invalidate() — instead of 60 per second for as
          long as the tab is open.  The GPU sat at full tilt on a still image
          all day; the day's two "Context Lost" freezes made that a cost worth
          removing.  onCreated puts a lost/restored context on the record. */}
      <Canvas shadows frameloop="demand" className="motor-canvas"
        onCreated={guardCanvas('geometry viewer')}
        onPointerMissed={() => setSelectedPart(null)}>
        {/* Adaptive camera that switches between Perspective and Orthographic */}
        <AdaptiveCamera />

        <OrbitControls
          ref={controlsRef}
          enableDamping={false}
        />
      
      {/* Lighting */}
      <ambientLight intensity={0.4} />
      <directionalLight
        position={[100, 100, 100]}
        intensity={1}
        castShadow
        shadow-mapSize={[2048, 2048]}
      />
      <directionalLight position={[-100, 50, -100]} intensity={0.5} />
      
      {/* Environment for reflections */}
      <EnvironmentOrLights intensity={envIntensity} />
      
      {/* Post-processing effects for Fusion 360 look */}
      <EffectComposer enableNormalPass>
        <Bloom
          intensity={0.15}
          luminanceThreshold={0.9}
          luminanceSmoothing={0.9}
        />
      </EffectComposer>
      
      {/* Grid */}
      {showGrid && (
        <Grid
          args={[500, 500]}
          cellSize={10}
          cellThickness={0.5}
          cellColor="#6b7280"
          sectionSize={50}
          sectionThickness={1}
          sectionColor="#374151"
          fadeDistance={500}
          fadeStrength={1}
          followCamera={false}
          position={[0, -50, 0]}
        />
      )}
      
      {/* Axes helper */}
      {showAxes && <axesHelper args={[100]} />}
      
      {/* Motor components */}
        <Suspense fallback={null}>
          <MotorComponents controlsRef={controlsRef} />
        </Suspense>
        
        {/* Camera synchronization */}
        <CameraSync controlsRef={controlsRef} />
        <ViewcubeNavigation controlsRef={controlsRef} />
        <SceneInvalidator />
      </Canvas>
      
      {/* Viewcube overlay */}
      <Viewcube />

      {/* Component tree overlay */}
      <ComponentTree />

      {/* 2D / 3D toggle */}
      <View2dToggle />

      {/* Material assignment bar — shows when a part is selected */}
      <MaterialBar />
    </>
  );
};

const MotorComponents: React.FC<{ controlsRef: React.RefObject<any> }> = ({ controlsRef }) => {
  const { viewMode, stlMeshes, connectedToApi } = useMotorStore();
  const { renderMode } = useUIStore();

  const showPointCloud = viewMode === 'pointcloud' || viewMode === 'hybrid';
  const showSTL = viewMode === 'stl' && Object.keys(stlMeshes).length > 0;

  return (
    <group>
      <FitCameraOnLoad controlsRef={controlsRef} />
      {showSTL && <STLCollection meshes={stlMeshes} />}

      {connectedToApi && (
        <>
          {/* 2D flat cross-section — for simulation/FEM */}
          {renderMode === '2d' && <ApiMotor2dFlat />}

          {/* Extruded 3D — fast Shapely+NumPy, no CadQuery */}
          {renderMode === 'extruded' && <ApiMotorExtruded />}
        </>
      )}

      {showPointCloud && <PointCloudMesh />}
    </group>
  );
};

export default MotorScene;

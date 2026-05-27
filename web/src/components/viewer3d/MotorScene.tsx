import React, { Suspense, useRef, useEffect } from 'react';
import { Canvas, useThree, useFrame } from '@react-three/fiber';
import { OrbitControls, PerspectiveCamera, OrthographicCamera, Environment, Grid } from '@react-three/drei';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import { useUIStore, useMotorStore } from '../../stores/motorStore';
import * as THREE from 'three';
import Viewcube from './Viewcube';
import StatorMesh from './StatorMesh';
import RotorMesh from './RotorMesh';
import ShaftMesh from './ShaftMesh';
import MagnetMesh from './MagnetMesh';
import WindingsMesh from './WindingsMesh';
import { ApiStatorMesh, ApiRotorMesh, ApiShaftMesh, ApiMagnetsMesh, ApiCoilsMesh, ApiMotor2dFlat, ApiMotorExtruded } from './ApiMotorMesh';
import PointCloudMesh from './PointCloudMesh';
import { STLCollection } from './STLMesh';
import ComponentTree from './ComponentTree';

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
const CameraSync: React.FC<{ controlsRef: React.RefObject<any> }> = ({ controlsRef }) => {
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
const ViewcubeNavigation: React.FC<{ controlsRef: React.RefObject<any> }> = ({ controlsRef }) => {
  const { camera } = useThree();
  const targetPosition = useRef<THREE.Vector3 | null>(null);
  const isAnimating = useRef(false);
  const animationFrame = useRef<number | undefined>(undefined);
  
  useEffect(() => {
    const handleNavigate = (e: CustomEvent) => {
      const { position, name } = e.detail;
      
      // Use fixed distance for standard views
      const distance = 250;
      const direction = position.clone().normalize();
      const newPosition = direction.multiplyScalar(distance);
      
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
        camera.lookAt(0, 0, 0);
        
        if (controlsRef.current) {
          controlsRef.current.target.set(0, 0, 0);
          controlsRef.current.update();
        }
        
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
  }, [camera, controlsRef]);
  
  return null;
};

// Fits camera to motor once on first load.
// Tracks which camera instance was fitted — if AdaptiveCamera replaces the camera,
// the zoom is re-applied to the new instance.
const FitCameraOnLoad: React.FC<{ controlsRef: React.RefObject<any> }> = ({ controlsRef }) => {
  const { camera, size } = useThree();
  const { geometry, connectedToApi } = useMotorStore();
  const { cameraMode } = useUIStore();
  const fittedCamera = useRef<THREE.Camera | null>(null);

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
  });

  return null;
};

// ─── Render-mode cycle button: 3D → EXT → 2D → 3D ───────────────────────────
const RENDER_MODE_LABELS: Record<string, string> = {
  '3d':       '3D',
  'extruded': 'EXT',
  '2d':       '2D',
};
const RENDER_MODE_TITLES: Record<string, string> = {
  '3d':       'CadQuery 3D — click for Extruded 2D',
  'extruded': 'Extruded 2D (fast) — click for 2D flat',
  '2d':       '2D flat cross-section — click for CadQuery 3D',
};
const RENDER_MODE_BG: Record<string, string> = {
  '3d':       '#1f2937',
  'extruded': '#7c3aed',
  '2d':       '#3b82f6',
};

const View2dToggle: React.FC = () => {
  const { renderMode, cycleRenderMode } = useUIStore();
  return (
    <button
      onClick={cycleRenderMode}
      title={RENDER_MODE_TITLES[renderMode] ?? ''}
      style={{
        position: 'absolute', bottom: 12, right: 12, zIndex: 20,
        padding: '4px 12px', borderRadius: 6, border: '1px solid #4b5563',
        background: RENDER_MODE_BG[renderMode] ?? '#1f2937',
        color: '#fff', fontWeight: 700, fontSize: 13, cursor: 'pointer',
        letterSpacing: 1,
      }}
    >
      {RENDER_MODE_LABELS[renderMode] ?? '3D'}
    </button>
  );
};

const MotorScene: React.FC = () => {
  const { showGrid, showAxes, envIntensity } = useUIStore();
  const controlsRef = useRef<any>(null);

  return (
    <>
      <Canvas shadows className="motor-canvas">
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
      <Environment preset="studio" background={false} environmentIntensity={envIntensity} />
      
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
      </Canvas>
      
      {/* Viewcube overlay */}
      <Viewcube />

      {/* Component tree overlay */}
      <ComponentTree />

      {/* 2D / 3D toggle */}
      <View2dToggle />
    </>
  );
};

const MotorComponents: React.FC<{ controlsRef: React.RefObject<any> }> = ({ controlsRef }) => {
  const { viewMode, stlMeshes, connectedToApi } = useMotorStore();
  const { metalness, roughness, componentVisibility, view2d, renderMode } = useUIStore();

  const showPointCloud = viewMode === 'pointcloud' || viewMode === 'hybrid';
  const showSTL = viewMode === 'stl' && Object.keys(stlMeshes).length > 0;

  const statorMaterialProps = { color: '#7f8c8d', metalness, roughness };
  const coilMaterialProps   = { color: '#b87333', metalness, roughness };

  const vis = componentVisibility;

  if (connectedToApi) {
    return (
      <group>
        <FitCameraOnLoad controlsRef={controlsRef} />
        {showSTL && <STLCollection meshes={stlMeshes} />}

        {/* 2D flat cross-section mode */}
        {renderMode === '2d' && <ApiMotor2dFlat />}

        {/* Extruded 2D mode — fast Shapely+NumPy, no CadQuery */}
        {renderMode === 'extruded' && <ApiMotorExtruded />}

        {/* 3D solid mode (CadQuery) */}
        {renderMode === '3d' && (viewMode === 'solid' || viewMode === 'hybrid') && (
          <>
            <ApiStatorMesh materialProps={statorMaterialProps} visible={vis.stator} />
            <ApiRotorMesh  materialProps={statorMaterialProps} visible={vis.rotor} />
            <ApiShaftMesh                                      visible={vis.shaft} />
            <ApiMagnetsMesh                                    visible={vis.magnets} />
            <ApiCoilsMesh  materialProps={coilMaterialProps}   visible={vis.coils} />
          </>
        )}

        {showPointCloud && <PointCloudMesh />}
      </group>
    );
  }

  return (
    <group>
      {showSTL && <STLCollection meshes={stlMeshes} />}

      {(viewMode === 'solid' || viewMode === 'hybrid') && (
        <>
          <StatorMesh  materialProps={statorMaterialProps} />
          <RotorMesh   materialProps={statorMaterialProps} />
          <ShaftMesh />
          <MagnetMesh />
          <WindingsMesh materialProps={coilMaterialProps} />
        </>
      )}

      {showPointCloud && <PointCloudMesh />}
    </group>
  );
};

export default MotorScene;

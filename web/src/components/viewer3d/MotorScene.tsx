import React, { Suspense, useRef, useEffect, useCallback } from 'react';
import { Canvas, useThree, useFrame } from '@react-three/fiber';
import { OrbitControls, PerspectiveCamera, Environment, Grid } from '@react-three/drei';
import { useUIStore, useMotorStore } from '../../stores/motorStore';
import * as THREE from 'three';
import Viewcube from './Viewcube';
import StatorMesh from './StatorMesh';
import RotorMesh from './RotorMesh';
import ShaftMesh from './ShaftMesh';
import MagnetMesh from './MagnetMesh';
import WindingsMesh from './WindingsMesh';
import { ApiStatorMesh, ApiRotorMesh, ApiShaftMesh, ApiMagnetsMesh, ApiCoilsMesh } from './ApiMotorMesh';
import PointCloudMesh from './PointCloudMesh';
import { STLCollection } from './STLMesh';

// Component to sync camera with viewcube
const CameraSync: React.FC<{ controlsRef: React.RefObject<any> }> = ({ controlsRef }) => {
  const { camera } = useThree();
  
  useFrame(() => {
    // Dispatch camera orientation to viewcube
    window.dispatchEvent(new CustomEvent('mainCameraChange', {
      detail: { quaternion: camera.quaternion.clone() }
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
      
      // Calculate new camera position - look at origin
      const direction = position.clone().normalize();
      const distance = camera.position.length();
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

const MotorScene: React.FC = () => {
  const { showGrid, showAxes, autoRotate } = useUIStore();
  const controlsRef = useRef<any>(null);
  
  return (
    <>
      <Canvas shadows className="motor-canvas">
        <PerspectiveCamera makeDefault position={[200, 200, 200]} fov={50} />
        <OrbitControls 
          ref={controlsRef}
          autoRotate={autoRotate}
          autoRotateSpeed={1}
          enableDamping
          dampingFactor={0.05}
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
      <Environment preset="studio" />
      
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
          <MotorComponents />
        </Suspense>
        
        {/* Camera synchronization */}
        <CameraSync controlsRef={controlsRef} />
        <ViewcubeNavigation controlsRef={controlsRef} />
      </Canvas>
      
      {/* Viewcube overlay */}
      <Viewcube />
    </>
  );
};

const MotorComponents: React.FC = () => {
  const { connectedToApi, viewMode, stlMeshes } = useMotorStore();
  
  // Show point cloud when in pointcloud or hybrid mode
  const showPointCloud = viewMode === 'pointcloud' || viewMode === 'hybrid';
  
  // Show STL when in stl mode
  const showSTL = viewMode === 'stl' && Object.keys(stlMeshes).length > 0;
  
  // Use API meshes when connected, otherwise use local meshes
  if (connectedToApi) {
    return (
      <group>
        {/* STL meshes from Fusion 360 / Modulus pipeline */}
        {showSTL && <STLCollection meshes={stlMeshes} />}
        
        {/* Solid mesh (show in solid or hybrid mode) */}
        {(viewMode === 'solid' || viewMode === 'hybrid') && (
          <>
            <ApiStatorMesh />
            <ApiRotorMesh />
            <ApiShaftMesh />
            <ApiMagnetsMesh />
            <ApiCoilsMesh />
          </>
        )}
        
        {/* Point cloud (show in pointcloud or hybrid mode) */}
        {showPointCloud && <PointCloudMesh />}
      </group>
    );
  }
  
  return (
    <group>
      {/* STL meshes from Fusion 360 / Modulus pipeline */}
      {showSTL && <STLCollection meshes={stlMeshes} />}
      
      {/* Solid mesh (show in solid or hybrid mode) */}
      {(viewMode === 'solid' || viewMode === 'hybrid') && (
        <>
          <StatorMesh />
          <RotorMesh />
          <ShaftMesh />
          <MagnetMesh />
          <WindingsMesh />
        </>
      )}
      
      {/* Point cloud (show in pointcloud or hybrid mode) */}
      {showPointCloud && <PointCloudMesh />}
    </group>
  );
};

export default MotorScene;

import React, { Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, PerspectiveCamera, Environment, Grid } from '@react-three/drei';
import { useUIStore } from '../../stores/motorStore';
import StatorMesh from './StatorMesh';
import RotorMesh from './RotorMesh';
import ShaftMesh from './ShaftMesh';
import MagnetMesh from './MagnetMesh';
import WindingsMesh from './WindingsMesh';

const MotorScene: React.FC = () => {
  const { showGrid, showAxes, autoRotate } = useUIStore();
  
  return (
    <Canvas shadows className="motor-canvas">
      <PerspectiveCamera makeDefault position={[200, 200, 200]} fov={50} />
      <OrbitControls 
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
    </Canvas>
  );
};

const MotorComponents: React.FC = () => {
  return (
    <group>
      {/* Stator */}
      <StatorMesh />
      
      {/* Rotor */}
      <RotorMesh />
      
      {/* Shaft */}
      <ShaftMesh />
      
      {/* Magnets */}
      <MagnetMesh />
      
      {/* Windings */}
      <WindingsMesh />
    </group>
  );
};

export default MotorScene;

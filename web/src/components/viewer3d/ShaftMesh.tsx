import React, { useMemo } from 'react';
import * as THREE from 'three';
import { useMotorStore, useUIStore } from '../../stores/motorStore';

const ShaftMesh: React.FC = () => {
  const { geometry: motorGeometry } = useMotorStore();
  const { showWireframe, envIntensity } = useUIStore();
  
  const shaftGeometry = useMemo(() => {
    const radius = motorGeometry.rotor_inner_radius!;
    
    const cylGeometry = new THREE.CylinderGeometry(
      radius,  // top radius
      radius,  // bottom radius
      motorGeometry.stator_width,  // height
      32,  // radial segments
      1,   // height segments
      false  // open ended
    );
    
    // Rotate to align with motor (Z-axis)
    cylGeometry.rotateX(Math.PI / 2);
    
    return cylGeometry;
  }, [motorGeometry]);
  
  return (
    <mesh 
      geometry={shaftGeometry} 
      castShadow 
      receiveShadow
    >
      <meshStandardMaterial
        color="#505050"
        metalness={0.95}
        roughness={0.25}
        envMapIntensity={envIntensity * 1.5}
        wireframe={showWireframe}
      />
    </mesh>
  );
};

export default ShaftMesh;

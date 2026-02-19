import React, { useMemo } from 'react';
import * as THREE from 'three';
import { useMotorStore, useUIStore } from '../../stores/motorStore';

const ShaftMesh: React.FC = () => {
  const { geometry: motorGeometry } = useMotorStore();
  const { showWireframe } = useUIStore();
  
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
        color="#6b7280"
        metalness={0.9}
        roughness={0.2}
        wireframe={showWireframe}
      />
    </mesh>
  );
};

export default ShaftMesh;

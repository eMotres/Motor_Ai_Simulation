import React, { useMemo } from 'react';
import * as THREE from 'three';
import { useMotorStore, useUIStore } from '../../stores/motorStore';

interface RotorMeshProps {
  materialProps?: {
    color: string;
    metalness: number;
    roughness: number;
  };
}

const RotorMesh: React.FC<RotorMeshProps> = ({ materialProps }) => {
  const { geometry } = useMotorStore();
  const { showWireframe, envIntensity } = useUIStore();
  
  const rotorGeometry = useMemo(() => {
    const shape = new THREE.Shape();
    const outerR = geometry.rotor_outer_radius! - geometry.magnet_height;
    const innerR = geometry.rotor_inner_radius!;
    
    // Draw outer circle
    shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);
    
    // Cut out inner bore (shaft hole)
    const hole = new THREE.Path();
    hole.absarc(0, 0, innerR, 0, Math.PI * 2, true);
    shape.holes.push(hole);
    
    // Extrude to 3D
    const extrudeSettings = {
      depth: geometry.stator_width,
      bevelEnabled: false,
    };
    
    const extrudedGeometry = new THREE.ExtrudeGeometry(shape, extrudeSettings);
    extrudedGeometry.translate(0, 0, -geometry.stator_width / 2);
    
    return extrudedGeometry;
  }, [geometry]);
  
  return (
    <mesh 
      geometry={rotorGeometry} 
      castShadow 
      receiveShadow
    >
      <meshStandardMaterial
        color={materialProps?.color || '#7f8c8d'}
        metalness={materialProps?.metalness ?? 0.9}
        roughness={materialProps?.roughness ?? 0.3}
        envMapIntensity={envIntensity * 1.5}
        wireframe={showWireframe}
      />
    </mesh>
  );
};

export default RotorMesh;

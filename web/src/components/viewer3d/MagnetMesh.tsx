import React, { useMemo } from 'react';
import * as THREE from 'three';
import { useMotorStore, useUIStore } from '../../stores/motorStore';

const MagnetMesh: React.FC = () => {
  const { geometry } = useMotorStore();
  const { showWireframe, envIntensity } = useUIStore();
  
  const magnetGeometries = useMemo(() => {
    const numPoles = geometry.num_poles!;
    const poleAngle = (2 * Math.PI) / numPoles;
    const innerR = geometry.rotor_outer_radius! - geometry.magnet_height;
    const outerR = geometry.rotor_outer_radius!;
    
    const geometries: { geometry: THREE.ExtrudeGeometry; rotation: number; color: string }[] = [];
    
    for (let i = 0; i < numPoles; i++) {
      const startAngle = i * poleAngle + poleAngle * 0.1;
      const endAngle = (i + 1) * poleAngle - poleAngle * 0.1;
      
      const shape = new THREE.Shape();
      
      // Inner arc
      shape.absarc(0, 0, innerR, startAngle, endAngle, false);
      
      // Outer arc (reverse direction)
      shape.absarc(0, 0, outerR, endAngle, startAngle, true);
      
      shape.closePath();
      
      const extrudeSettings = {
        depth: geometry.stator_width,
        bevelEnabled: false,
      };
      
      const extrudedGeometry = new THREE.ExtrudeGeometry(shape, extrudeSettings);
      extrudedGeometry.translate(0, 0, -geometry.stator_width / 2);
      
      // Alternating colors for N/S poles
      const color = i % 2 === 0 ? '#dc2626' : '#2563eb'; // Red for N, Blue for S
      
      geometries.push({
        geometry: extrudedGeometry,
        rotation: 0,
        color,
      });
    }
    
    return geometries;
  }, [geometry]);
  
  return (
    <group>
      {magnetGeometries.map((magnet, index) => (
        <mesh
          key={index}
          geometry={magnet.geometry}
          castShadow
          receiveShadow
        >
          <meshStandardMaterial
            color={magnet.color}
            metalness={0.8}
            roughness={0.4}
            envMapIntensity={envIntensity * 1.5}
            wireframe={showWireframe}
          />
        </mesh>
      ))}
    </group>
  );
};

export default MagnetMesh;

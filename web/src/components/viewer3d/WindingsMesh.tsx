import React, { useMemo } from 'react';
import * as THREE from 'three';
import { useMotorStore, useUIStore } from '../../stores/motorStore';

interface WindingsMeshProps {
  materialProps?: {
    color: string;
    metalness: number;
    roughness: number;
  };
}

const WindingsMesh: React.FC<WindingsMeshProps> = ({ materialProps }) => {
  const { geometry } = useMotorStore();
  const { showWireframe, envIntensity } = useUIStore();
  
  const windingGeometries = useMemo(() => {
    const numSlots = geometry.num_slots!;
    const slotAngle = (2 * Math.PI) / numSlots;
    const innerR = geometry.stator_inner_radius!;
    const outerR = geometry.stator_outer_radius! - geometry.core_thickness;
    const slotWidth = geometry.tooth_width * 0.6;
    
    const geometries: { geometry: THREE.ExtrudeGeometry; position: [number, number, number] }[] = [];
    
    for (let i = 0; i < numSlots; i++) {
      const centerAngle = i * slotAngle;
      
      const shape = new THREE.Shape();
      
      // Create slot shape (simplified trapezoid)
      const halfWidthInner = slotWidth / 2;
      const halfWidthOuter = slotWidth * 1.2 / 2; // Wider at outer edge
      
      // Calculate corner points
      const cos = Math.cos(centerAngle);
      const sin = Math.sin(centerAngle);
      const perpCos = Math.cos(centerAngle + Math.PI / 2);
      const perpSin = Math.sin(centerAngle + Math.PI / 2);
      
      // Inner corners
      const x1 = cos * innerR + perpCos * halfWidthInner;
      const y1 = sin * innerR + perpSin * halfWidthInner;
      const x2 = cos * innerR - perpCos * halfWidthInner;
      const y2 = sin * innerR - perpSin * halfWidthInner;
      
      // Outer corners
      const x3 = cos * outerR - perpCos * halfWidthOuter;
      const y3 = sin * outerR - perpSin * halfWidthOuter;
      const x4 = cos * outerR + perpCos * halfWidthOuter;
      const y4 = sin * outerR + perpSin * halfWidthOuter;
      
      shape.moveTo(x1, y1);
      shape.lineTo(x4, y4);
      shape.lineTo(x3, y3);
      shape.lineTo(x2, y2);
      shape.closePath();
      
      const extrudeSettings = {
        depth: geometry.stator_width * 0.9,
        bevelEnabled: false,
      };
      
      const extrudedGeometry = new THREE.ExtrudeGeometry(shape, extrudeSettings);
      extrudedGeometry.translate(0, 0, -geometry.stator_width * 0.45);
      
      geometries.push({
        geometry: extrudedGeometry,
        position: [0, 0, 0],
      });
    }
    
    return geometries;
  }, [geometry]);
  
  return (
    <group>
      {windingGeometries.map((winding, index) => (
        <mesh
          key={index}
          geometry={winding.geometry}
          position={winding.position}
          castShadow
          receiveShadow
        >
          <meshStandardMaterial
            color={materialProps?.color || '#b87333'}
            metalness={materialProps?.metalness ?? 0.8}
            roughness={materialProps?.roughness ?? 0.4}
            envMapIntensity={envIntensity * 1.2}
            wireframe={showWireframe}
          />
        </mesh>
      ))}
    </group>
  );
};

export default WindingsMesh;

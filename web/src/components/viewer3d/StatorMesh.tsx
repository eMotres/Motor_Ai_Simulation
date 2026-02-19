import React, { useMemo } from 'react';
import * as THREE from 'three';
import { useMotorStore, useUIStore } from '../../stores/motorStore';

const StatorMesh: React.FC = () => {
  const { geometry } = useMotorStore();
  const { showWireframe } = useUIStore();
  
  const statorGeometry = useMemo(() => {
    const shape = new THREE.Shape();
    const outerR = geometry.stator_outer_radius!;
    const innerR = geometry.stator_inner_radius!;
    const slotR = geometry.stator_outer_radius! - geometry.core_thickness;
    const numSlots = geometry.num_slots!;
    const slotAngle = (2 * Math.PI) / numSlots;
    const slotWidth = geometry.tooth_width * 0.8; // Approximate slot width
    
    // Draw outer circle
    shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);
    
    // Cut out inner bore (with slots)
    const innerHole = new THREE.Path();
    
    // Start at first slot
    const halfSlotWidth = (slotWidth / innerR) / 2;
    
    for (let i = 0; i < numSlots; i++) {
      const angle = i * slotAngle;
      const nextAngle = (i + 1) * slotAngle;
      
      // Slot position
      const slotStart = angle - halfSlotWidth;
      const slotEnd = angle + halfSlotWidth;
      
      if (i === 0) {
        innerHole.moveTo(
          Math.cos(slotEnd) * innerR,
          Math.sin(slotEnd) * innerR
        );
      }
      
      // Arc to next slot
      innerHole.absarc(0, 0, innerR, slotEnd, nextAngle - halfSlotWidth, false);
      
      // Draw slot (radial cutout)
      const slotCenterX = Math.cos(angle) * (innerR + geometry.slot_height / 2);
      const slotCenterY = Math.sin(angle) * (innerR + geometry.slot_height / 2);
      
      // Simple rectangular slot
      innerHole.lineTo(
        Math.cos(angle - halfSlotWidth * 0.5) * (innerR + geometry.slot_height),
        Math.sin(angle - halfSlotWidth * 0.5) * (innerR + geometry.slot_height)
      );
      innerHole.lineTo(
        Math.cos(angle + halfSlotWidth * 0.5) * (innerR + geometry.slot_height),
        Math.sin(angle + halfSlotWidth * 0.5) * (innerR + geometry.slot_height)
      );
      innerHole.lineTo(
        Math.cos(angle + halfSlotWidth * 0.5) * innerR,
        Math.sin(angle + halfSlotWidth * 0.5) * innerR
      );
    }
    
    shape.holes.push(innerHole);
    
    // Extrude to 3D
    const extrudeSettings = {
      depth: geometry.stator_width,
      bevelEnabled: false,
    };
    
    const extrudedGeometry = new THREE.ExtrudeGeometry(shape, extrudeSettings);
    // Center the geometry
    extrudedGeometry.translate(0, 0, -geometry.stator_width / 2);
    
    return extrudedGeometry;
  }, [geometry]);
  
  return (
    <mesh 
      geometry={statorGeometry} 
      castShadow 
      receiveShadow
      position={[0, 0, 0]}
    >
      <meshStandardMaterial
        color="#4a5568"
        metalness={0.8}
        roughness={0.3}
        wireframe={showWireframe}
      />
    </mesh>
  );
};

export default StatorMesh;

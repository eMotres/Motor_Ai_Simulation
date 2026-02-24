import React, { useMemo, useEffect, useState } from 'react';
import * as THREE from 'three';
import { useMotorStore, useUIStore } from '../../stores/motorStore';

// Use port 8013 for API
const API_BASE_URL = 'http://localhost:8013';

interface MeshData {
  vertices: number[] | number[][];
  faces?: number[][];
  outer_radius?: number;
  inner_radius?: number;
  radius?: number;
  slot_height?: number;
  slot_width?: number;
  num_slots?: number;
  stator_width: number;
}

interface MagnetsMeshData {
  vertices: number[] | number[][];
  faces?: number[][];
  num_poles?: number;
}

interface CoilsMeshData {
  vertices: number[] | number[][];
  faces?: number[][];
  num_slots?: number;
}

interface MaterialProps {
  color: string;
  metalness: number;
  roughness: number;
}

/**
 * Stator mesh that renders geometry from Python API
 */
export const ApiStatorMesh: React.FC<{ materialProps?: MaterialProps }> = ({ materialProps }) => {
  const { geometry, connectedToApi } = useMotorStore();
  const { envIntensity } = useUIStore();
  const [meshData, setMeshData] = useState<MeshData | null>(null);
  
  useEffect(() => {
    if (connectedToApi) {
      fetch(`${API_BASE_URL}/api/geometry/mesh`)
        .then(res => res.json())
        .then(data => setMeshData(data.stator_core))
        .catch(err => console.error('Failed to fetch mesh:', err));
    }
  }, [connectedToApi, geometry]);
  
  const statorGeometry = useMemo(() => {
    if (!meshData) {
      // Fallback to local geometry
      return createLocalStatorGeometry(geometry);
    }
    
    return createApiStatorGeometry(meshData);
  }, [meshData, geometry]);
  
  return (
    <mesh geometry={statorGeometry} castShadow receiveShadow>
      <meshStandardMaterial 
        color={materialProps?.color || '#7f8c8d'}
        metalness={materialProps?.metalness ?? 0.9}
        roughness={materialProps?.roughness ?? 0.3}
        envMapIntensity={envIntensity * 1.5} 
      />
    </mesh>
  );
};

/**
 * Rotor mesh that renders geometry from Python API
 */
export const ApiRotorMesh: React.FC<{ materialProps?: MaterialProps }> = ({ materialProps }) => {
  const { geometry, connectedToApi } = useMotorStore();
  const { envIntensity } = useUIStore();
  const [meshData, setMeshData] = useState<MeshData | null>(null);
  
  useEffect(() => {
    if (connectedToApi) {
      fetch(`${API_BASE_URL}/api/geometry/mesh`)
        .then(res => res.json())
        .then(data => setMeshData(data.rotor_core))
        .catch(err => console.error('Failed to fetch mesh:', err));
    }
  }, [connectedToApi, geometry]);
  
  const rotorGeometry = useMemo(() => {
    if (!meshData) {
      return createLocalRotorGeometry(geometry);
    }
    return createApiAnnulusGeometry(meshData);
  }, [meshData, geometry]);
  
  return (
    <mesh geometry={rotorGeometry} castShadow receiveShadow>
      <meshStandardMaterial 
        color={materialProps?.color || '#7f8c8d'}
        metalness={materialProps?.metalness ?? 0.9}
        roughness={materialProps?.roughness ?? 0.3}
        envMapIntensity={envIntensity * 1.5} 
      />
    </mesh>
  );
};

/**
 * Shaft mesh that renders geometry from Python API
 */
export const ApiShaftMesh: React.FC = () => {
  const { geometry, connectedToApi } = useMotorStore();
  const { envIntensity } = useUIStore();
  const [meshData, setMeshData] = useState<MeshData | null>(null);
  
  useEffect(() => {
    if (connectedToApi) {
      fetch(`${API_BASE_URL}/api/geometry/mesh`)
        .then(res => res.json())
        .then(data => setMeshData(data.shaft))
        .catch(err => console.error('Failed to fetch mesh:', err));
    }
  }, [connectedToApi, geometry]);
  
  const shaftGeometry = useMemo(() => {
    if (!meshData) {
      return createLocalShaftGeometry(geometry);
    }
    return createApiCylinderGeometry(meshData);
  }, [meshData, geometry]);
  
  return (
    <mesh geometry={shaftGeometry} castShadow receiveShadow>
      <meshStandardMaterial color="#505050" metalness={0.95} roughness={0.25} envMapIntensity={envIntensity * 1.5} />
    </mesh>
  );
};

/**
 * Magnets mesh that renders geometry from Python API
 */
export const ApiMagnetsMesh: React.FC = () => {
  const { geometry, connectedToApi } = useMotorStore();
  const { envIntensity } = useUIStore();
  const [meshData, setMeshData] = useState<MagnetsMeshData | null>(null);
  
  useEffect(() => {
    if (connectedToApi) {
      fetch(`${API_BASE_URL}/api/geometry/mesh`)
        .then(res => res.json())
        .then(data => setMeshData(data.magnets))
        .catch(err => console.error('Failed to fetch mesh:', err));
    }
  }, [connectedToApi, geometry]);
  
  const magnetGeometries = useMemo(() => {
    if (!meshData) {
      return createLocalMagnetsGeometry(geometry);
    }
    // New format: vertices and faces directly
    if (meshData.vertices && meshData.vertices.length > 0) {
      return [{
        geometry: createMeshFromVerticesAndFaces(meshData.vertices, meshData.faces),
        poleIndex: 0,
        direction: 'outward',
      }];
    }
    return [];
  }, [meshData, geometry]);
  
  return (
    <group>
      {magnetGeometries.map(({ geometry: geo, poleIndex, direction }) => (
        <mesh key={poleIndex} geometry={geo} castShadow receiveShadow>
          <meshStandardMaterial 
            color={direction === 'outward' ? '#ef4444' : '#3b82f6'}
            metalness={0.8} 
            roughness={0.4}
            envMapIntensity={envIntensity * 1.5}
          />
        </mesh>
      ))}
    </group>
  );
};

/**
 * Coils/Windings mesh that renders geometry from Python API
 */
export const ApiCoilsMesh: React.FC<{ materialProps?: MaterialProps }> = ({ materialProps }) => {
  const { geometry, connectedToApi } = useMotorStore();
  const { envIntensity } = useUIStore();
  const [meshData, setMeshData] = useState<CoilsMeshData | null>(null);
  
  useEffect(() => {
    if (connectedToApi) {
      fetch(`${API_BASE_URL}/api/geometry/mesh`)
        .then(res => res.json())
        .then(data => setMeshData(data.coils))
        .catch(err => console.error('Failed to fetch coils mesh:', err));
    }
  }, [connectedToApi, geometry]);
  
  const coilGeometries = useMemo(() => {
    if (!meshData || !meshData.vertices || meshData.vertices.length === 0) {
      return [];
    }
    // New format: vertices and faces directly
    const geo = createMeshFromVerticesAndFaces(meshData.vertices, meshData.faces);
    return [{
      geometry: geo,
      slotIndex: 0,
      phase: 0,
    }];
  }, [meshData]);
  
  // Phase colors (3-phase winding)
  const phaseColors = ['#b45309', '#c2410c', '#a16207'];  // Copper/amber tones
  
  return (
    <group>
      {coilGeometries.map(({ geometry: geo, slotIndex, phase }) => (
        <mesh key={`coil-${slotIndex}`} geometry={geo} castShadow receiveShadow>
          <meshStandardMaterial 
            color={materialProps?.color || phaseColors[phase] || '#b87333'}
            metalness={materialProps?.metalness ?? 0.8}
            roughness={materialProps?.roughness ?? 0.4}
            envMapIntensity={envIntensity * 1.5}
          />
        </mesh>
      ))}
    </group>
  );
};

// Helper functions for creating geometries

/**
 * Create a mesh from vertices and faces
 */
function createMeshFromVerticesAndFaces(
  vertices: number[] | number[][],
  faces?: number[][]
): THREE.BufferGeometry {
  // Handle both flat and nested array formats for vertices
  let flatPositions: number[];
  if (vertices.length > 0 && Array.isArray(vertices[0])) {
    flatPositions = (vertices as number[][]).flat();
  } else {
    flatPositions = vertices as number[];
  }
  
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(flatPositions, 3));
  
  // Add faces if available
  if (faces && faces.length > 0) {
    const flatIndices: number[] = [];
    for (const face of faces) {
      flatIndices.push(face[0], face[1], face[2]);
    }
    geometry.setIndex(flatIndices);
  }
  
  geometry.computeVertexNormals();
  return geometry;
}

function createApiStatorGeometry(data: MeshData): THREE.BufferGeometry {
  // Use vertices AND faces from API if available
  if (data.vertices && data.vertices.length > 0 && data.faces && data.faces.length > 0) {
    // Handle both flat and nested array formats for vertices
    let flatPositions: number[];
    if (data.vertices.length > 0 && Array.isArray(data.vertices[0])) {
      flatPositions = (data.vertices as number[][]).flat();
    } else {
      flatPositions = data.vertices as number[];
    }
    
    // Flatten faces
    const flatIndices: number[] = [];
    for (const face of data.faces) {
      flatIndices.push(face[0], face[1], face[2]);
    }
    
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(flatPositions, 3));
    geometry.setIndex(flatIndices);
    geometry.computeVertexNormals();
    return geometry;
  }
  
  // Fallback: create from parameters using vertices only
  if (data.vertices && data.vertices.length > 0) {
    let flatPositions: number[];
    if (data.vertices.length > 0 && Array.isArray(data.vertices[0])) {
      flatPositions = (data.vertices as number[][]).flat();
    } else {
      flatPositions = data.vertices as number[];
    }
    
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(flatPositions);
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    return geometry;
  }
  
  // Fallback: create from parameters
  const shape = new THREE.Shape();
  const outerR = data.outer_radius || 100;
  const innerR = data.inner_radius || 80;
  const numSlots = data.num_slots || 36;
  const slotHeight = data.slot_height || 16;
  const slotWidth = data.slot_width || 4.5;
  
  // Draw outer circle
  shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);
  
  // Create inner hole with slots
  const innerHole = new THREE.Path();
  const slotAngle = (2 * Math.PI) / numSlots;
  const halfSlotWidthRad = (slotWidth / innerR) / 2;
  
  for (let i = 0; i < numSlots; i++) {
    const angle = i * slotAngle;
    const slotStart = angle - halfSlotWidthRad;
    const slotEnd = angle + halfSlotWidthRad;
    
    if (i === 0) {
      innerHole.moveTo(Math.cos(slotEnd) * innerR, Math.sin(slotEnd) * innerR);
    }
    
    // Arc to next slot
    const nextSlotStart = (i + 1) * slotAngle - halfSlotWidthRad;
    innerHole.absarc(0, 0, innerR, slotEnd, nextSlotStart, false);
    
    // Draw slot
    const slotR = innerR + slotHeight;
    innerHole.lineTo(
      Math.cos(angle - halfSlotWidthRad * 0.5) * slotR,
      Math.sin(angle - halfSlotWidthRad * 0.5) * slotR
    );
    innerHole.lineTo(
      Math.cos(angle + halfSlotWidthRad * 0.5) * slotR,
      Math.sin(angle + halfSlotWidthRad * 0.5) * slotR
    );
    innerHole.lineTo(
      Math.cos(angle + halfSlotWidthRad) * innerR,
      Math.sin(angle + halfSlotWidthRad) * innerR
    );
  }
  
  shape.holes.push(innerHole);
  
  const extrudeSettings = { depth: data.stator_width, bevelEnabled: false };
  const extruded = new THREE.ExtrudeGeometry(shape, extrudeSettings);
  extruded.translate(0, 0, -data.stator_width / 2);
  
  return extruded;
}

function createApiAnnulusGeometry(data: MeshData): THREE.BufferGeometry {
  // Use vertices AND faces from API if available
  if (data.vertices && data.vertices.length > 0 && data.faces && data.faces.length > 0) {
    return createMeshFromVerticesAndFaces(data.vertices, data.faces);
  }
  
  // Use vertices only from API if available
  if (data.vertices && data.vertices.length > 0) {
    let flatPositions: number[];
    if (data.vertices.length > 0 && Array.isArray(data.vertices[0])) {
      flatPositions = (data.vertices as number[][]).flat();
    } else {
      flatPositions = data.vertices as number[];
    }
    
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(flatPositions);
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    return geometry;
  }
  
  // Fallback: create from parameters
  const outerR = data.outer_radius || 80;
  const innerR = data.inner_radius || 20;
  
  const shape = new THREE.Shape();
  shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);
  
  const hole = new THREE.Path();
  hole.absarc(0, 0, innerR, 0, Math.PI * 2, true);
  shape.holes.push(hole);
  
  const extrudeSettings = { depth: data.stator_width, bevelEnabled: false };
  const extruded = new THREE.ExtrudeGeometry(shape, extrudeSettings);
  extruded.translate(0, 0, -data.stator_width / 2);
  
  return extruded;
}

function createApiCylinderGeometry(data: MeshData): THREE.BufferGeometry {
  // Use vertices AND faces from API if available
  if (data.vertices && data.vertices.length > 0 && data.faces && data.faces.length > 0) {
    return createMeshFromVerticesAndFaces(data.vertices, data.faces);
  }
  
  // Use vertices only from API if available
  if (data.vertices && data.vertices.length > 0) {
    return createMeshFromVerticesAndFaces(data.vertices);
  }
  
  // Fallback: create from parameters
  const radius = data.outer_radius || data.radius || 20;
  const geometry = new THREE.CylinderGeometry(
    radius,
    radius,
    data.stator_width,
    32
  );
  // No rotation - cylinder default axis is Y which is correct for motor shaft
  return geometry;
}

function createApiMagnetGeometry(vertices: number[] | number[][]): THREE.BufferGeometry {
  // Create a simple sector geometry from vertices
  const geometry = new THREE.BufferGeometry();
  
  // Handle both flat array [x1,y1,z1,x2,y2,z2...] and nested array [[x1,y1,z1],[x2,y2,z2]...]
  let flatPositions: number[];
  if (vertices.length > 0 && Array.isArray(vertices[0])) {
    // Nested array format - flatten it
    flatPositions = (vertices as number[][]).flat();
  } else {
    // Already flat
    flatPositions = vertices as number[];
  }
  
  const positions = new Float32Array(flatPositions);
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.computeVertexNormals();
  return geometry;
}

// Fallback functions for local geometry

function createLocalStatorGeometry(geometry: any): THREE.BufferGeometry {
  // Handle API geometry parameters (stator_diameter, etc.)
  const outerR = (geometry.stator_outer_radius) || 
                 (geometry.stator_diameter ? geometry.stator_diameter / 2 : 100);
  const innerR = (geometry.stator_inner_radius) || 
                (outerR - (geometry.core_thickness || 20) - (geometry.slot_height || 16));
  const numSlots = geometry.num_slots || 36;
  const slotHeight = geometry.slot_height || 16;
  const slotWidth = geometry.slot_width || geometry.tooth_width * 0.8 || 4.5;
  
  const shape = new THREE.Shape();
  
  const innerHole = new THREE.Path();
  const slotAngle = (2 * Math.PI) / numSlots;
  const halfSlotWidthRad = (slotWidth / innerR) / 2;
  
  for (let i = 0; i < numSlots; i++) {
    const angle = i * slotAngle;
    const slotStart = angle - halfSlotWidthRad;
    const slotEnd = angle + halfSlotWidthRad;
    
    if (i === 0) {
      innerHole.moveTo(Math.cos(slotEnd) * innerR, Math.sin(slotEnd) * innerR);
    }
    
    const nextSlotStart = (i + 1) * slotAngle - halfSlotWidthRad;
    innerHole.absarc(0, 0, innerR, slotEnd, nextSlotStart, false);
    
    const slotR = innerR + slotHeight;
    innerHole.lineTo(
      Math.cos(angle - halfSlotWidthRad * 0.5) * slotR,
      Math.sin(angle - halfSlotWidthRad * 0.5) * slotR
    );
    innerHole.lineTo(
      Math.cos(angle + halfSlotWidthRad * 0.5) * slotR,
      Math.sin(angle + halfSlotWidthRad * 0.5) * slotR
    );
    innerHole.lineTo(
      Math.cos(angle + halfSlotWidthRad) * innerR,
      Math.sin(angle + halfSlotWidthRad) * innerR
    );
  }
  
  shape.holes.push(innerHole);
  
  const extrudeSettings = { depth: (geometry.core_thickness || 20), bevelEnabled: false };
  const extruded = new THREE.ExtrudeGeometry(shape, extrudeSettings);
  extruded.translate(0, 0, -(geometry.core_thickness || 20) / 2);
  
  return extruded;
}

function createLocalRotorGeometry(geometry: any): THREE.BufferGeometry {
  // Handle API geometry parameters
  const outerR = (geometry.rotor_outer_radius) || 
                 (geometry.stator_inner_radius) ||
                 ((geometry.stator_diameter ? geometry.stator_diameter / 2 : 100) - (geometry.core_thickness || 20) - (geometry.slot_height || 16));
  const innerR = (geometry.shaft_radius) || 20;
  const coreThickness = geometry.core_thickness || 20;
  
  const shape = new THREE.Shape();
  shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);
  
  const hole = new THREE.Path();
  hole.absarc(0, 0, innerR, 0, Math.PI * 2, true);
  shape.holes.push(hole);
  
  const extrudeSettings = { depth: coreThickness, bevelEnabled: false };
  const extruded = new THREE.ExtrudeGeometry(shape, extrudeSettings);
  extruded.translate(0, 0, -coreThickness / 2);
  
  return extruded;
}

function createLocalShaftGeometry(geometry: any): THREE.BufferGeometry {
  // Handle API geometry parameters
  const radius = (geometry.shaft_radius) || 
                (geometry.rotor_inner_radius) || 20;
  const length = geometry.core_thickness || 50;
  
  const geo = new THREE.CylinderGeometry(radius, radius, length, 32);
  // No rotation - cylinder default axis is Y which is correct for motor shaft
  return geo;
}

function createLocalMagnetsGeometry(geometry: any): { geometry: THREE.BufferGeometry; poleIndex: number; direction: string }[] {
  // Handle API geometry parameters
  const rotorOuterR = (geometry.rotor_outer_radius) || 
                     ((geometry.stator_diameter ? geometry.stator_diameter / 2 : 100) - (geometry.core_thickness || 20) - (geometry.slot_height || 16) - (geometry.air_gap || 2));
  const magnetHeight = geometry.magnet_height || 5;
  const innerR = rotorOuterR - magnetHeight;
  const numPoles = geometry.num_poles || 8;
  
  const magnets: { geometry: THREE.BufferGeometry; poleIndex: number; direction: string }[] = [];
  
  for (let i = 0; i < numPoles; i++) {
    const angle = (i * 2 * Math.PI) / numPoles;
    const poleAngle = (2 * Math.PI) / numPoles;
    
    // Create a simple magnet as a box
    const width = (2 * Math.PI * innerR) / numPoles * 0.8;
    const magnetGeo = new THREE.BoxGeometry(width, magnetHeight, geometry.core_thickness || 20);
    
    // Position and rotate the magnet
    magnetGeo.translate(
      Math.cos(angle + poleAngle / 2) * (innerR + magnetHeight / 2),
      Math.sin(angle + poleAngle / 2) * (innerR + magnetHeight / 2),
      0
    );
    
    // Rotate around Z axis
    magnetGeo.rotateZ(angle + poleAngle / 2);
    
    magnets.push({
      geometry: magnetGeo,
      poleIndex: i,
      direction: i % 2 === 0 ? 'outward' : 'inward'
    });
  }
  
  return magnets;
}

export default ApiStatorMesh;

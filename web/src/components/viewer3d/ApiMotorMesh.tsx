import React, { useMemo, useEffect, useState } from 'react';
import * as THREE from 'three';
import { useMotorStore, useUIStore } from '../../stores/motorStore';

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8013';

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
  const [allMeshData, setAllMeshData] = useState<Record<string, MagnetsMeshData> | null>(null);

  useEffect(() => {
    if (connectedToApi) {
      fetch(`${API_BASE_URL}/api/geometry/mesh`)
        .then(res => res.json())
        .then(data => {
          const magnets: Record<string, MagnetsMeshData> = {};
          Object.keys(data).forEach(key => {
            if (key.startsWith('magnet_')) magnets[key] = data[key];
          });
          setAllMeshData(Object.keys(magnets).length > 0 ? magnets : null);
        })
        .catch(err => console.error('Failed to fetch mesh:', err));
    }
  }, [connectedToApi, geometry]);

  const magnetGeometries = useMemo(() => {
    if (!allMeshData) {
      return createLocalMagnetsGeometry(geometry);
    }
    return Object.keys(allMeshData)
      .sort((a, b) => parseInt(a.split('_')[1]) - parseInt(b.split('_')[1]))
      .map((key, i) => {
        const data = allMeshData[key];
        return {
          geometry: createMeshFromVerticesAndFaces(data.vertices, data.faces),
          poleIndex: i,
          direction: i % 2 === 0 ? 'outward' : 'inward',
        };
      });
  }, [allMeshData, geometry]);

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
  const [allCoilData, setAllCoilData] = useState<Record<string, CoilsMeshData> | null>(null);

  useEffect(() => {
    if (connectedToApi) {
      fetch(`${API_BASE_URL}/api/geometry/mesh`)
        .then(res => res.json())
        .then(data => {
          const coils: Record<string, CoilsMeshData> = {};
          Object.keys(data).forEach(key => {
            if (key.startsWith('coil_')) coils[key] = data[key];
          });
          setAllCoilData(Object.keys(coils).length > 0 ? coils : null);
        })
        .catch(err => console.error('Failed to fetch coils mesh:', err));
    }
  }, [connectedToApi, geometry]);

  const phaseColors = ['#b45309', '#c2410c', '#a16207'];

  const coilGeometries = useMemo(() => {
    if (!allCoilData) return [];
    return Object.keys(allCoilData)
      .sort((a, b) => parseInt(a.split('_')[1]) - parseInt(b.split('_')[1]))
      .map((key, i) => {
        const data = allCoilData[key];
        return {
          geometry: createMeshFromVerticesAndFaces(data.vertices, data.faces),
          slotIndex: i,
          phase: i % 3,
        };
      });
  }, [allCoilData]);

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
  const outerR = (geometry.stator_outer_radius) ||
                 (geometry.stator_diameter ? geometry.stator_diameter / 2 : 100);
  const innerR = (geometry.stator_inner_radius) ||
                (outerR - (geometry.core_thickness || 3.8) - (geometry.slot_height || 16));
  const numSlots = geometry.num_slots || 36;
  const slotHeight = geometry.slot_height || 16;
  const slotWidth = geometry.slot_width || geometry.tooth_width * 0.6 || 4.5;
  const statorWidth = geometry.stator_width || 30;

  const shape = new THREE.Shape();
  shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);

  const innerHole = new THREE.Path();
  const slotAngle = (2 * Math.PI) / numSlots;
  const halfSlotWidthRad = (slotWidth / innerR) / 2;

  for (let i = 0; i < numSlots; i++) {
    const angle = i * slotAngle;
    const slotEnd = angle + halfSlotWidthRad;
    const nextSlotStart = (i + 1) * slotAngle - halfSlotWidthRad;

    if (i === 0) {
      innerHole.moveTo(Math.cos(slotEnd) * innerR, Math.sin(slotEnd) * innerR);
    }

    innerHole.absarc(0, 0, innerR, slotEnd, nextSlotStart, false);

    const slotR = innerR + slotHeight;
    innerHole.lineTo(
      Math.cos(nextSlotStart) * slotR,
      Math.sin(nextSlotStart) * slotR
    );
    innerHole.lineTo(
      Math.cos(slotEnd) * slotR,
      Math.sin(slotEnd) * slotR
    );
    innerHole.lineTo(
      Math.cos(slotEnd) * innerR,
      Math.sin(slotEnd) * innerR
    );
  }

  shape.holes.push(innerHole);

  const extruded = new THREE.ExtrudeGeometry(shape, { depth: statorWidth, bevelEnabled: false });
  extruded.translate(0, 0, -statorWidth / 2);
  return extruded;
}

function createLocalRotorGeometry(geometry: any): THREE.BufferGeometry {
  const outerR = geometry.rotor_outer_radius || 79.55;
  const innerR = geometry.rotor_inner_radius || 64.55;
  const statorWidth = geometry.stator_width || 30;

  const shape = new THREE.Shape();
  shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);

  const hole = new THREE.Path();
  hole.absarc(0, 0, innerR, 0, Math.PI * 2, true);
  shape.holes.push(hole);

  const extruded = new THREE.ExtrudeGeometry(shape, { depth: statorWidth, bevelEnabled: false });
  extruded.translate(0, 0, -statorWidth / 2);
  return extruded;
}

function createLocalShaftGeometry(geometry: any): THREE.BufferGeometry {
  const outerR = geometry.rotor_inner_radius || 64.55;
  const shaftHeight = geometry.shaft_height || 3;
  const innerR = outerR - shaftHeight;
  const statorWidth = geometry.stator_width || 30;

  const shape = new THREE.Shape();
  shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);

  const hole = new THREE.Path();
  hole.absarc(0, 0, innerR, 0, Math.PI * 2, true);
  shape.holes.push(hole);

  const extruded = new THREE.ExtrudeGeometry(shape, { depth: statorWidth, bevelEnabled: false });
  extruded.translate(0, 0, -statorWidth / 2);
  return extruded;
}

function createLocalMagnetsGeometry(geometry: any): { geometry: THREE.BufferGeometry; poleIndex: number; direction: string }[] {
  const rotorOuterR = geometry.rotor_outer_radius || 79.55;
  const rotorInnerR = geometry.rotor_inner_radius || 64.55;
  const rotorHouseH = geometry.rotor_house_height || 1.2;
  const magnetHeight = geometry.magnet_height || 13.8;
  const numPoles = geometry.num_poles || 42;
  const statorWidth = geometry.stator_width || 30;
  const magFillDown = geometry.magnet_fill_down || 0.9;

  const poleAngle = (2 * Math.PI) / numPoles;
  const magnetInnerR = rotorInnerR + rotorHouseH;
  const magnetOuterR = rotorOuterR - 0.5;
  const halfAngle = poleAngle * magFillDown / 2;

  const magnets: { geometry: THREE.BufferGeometry; poleIndex: number; direction: string }[] = [];

  for (let i = 0; i < numPoles; i++) {
    const centerAngle = i * poleAngle;
    const startAngle = centerAngle - halfAngle;
    const endAngle = centerAngle + halfAngle;

    const shape = new THREE.Shape();
    shape.absarc(0, 0, magnetOuterR, startAngle, endAngle, false);
    shape.absarc(0, 0, magnetInnerR, endAngle, startAngle, true);
    shape.closePath();

    const extruded = new THREE.ExtrudeGeometry(shape, { depth: statorWidth, bevelEnabled: false });
    extruded.translate(0, 0, -statorWidth / 2);

    magnets.push({
      geometry: extruded,
      poleIndex: i,
      direction: i % 2 === 0 ? 'outward' : 'inward',
    });
  }

  return magnets;
}

export default ApiStatorMesh;

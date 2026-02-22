import { useEffect, useRef } from 'react';
import * as THREE from 'three';

interface STLMeshProps {
  vertices: number[];
  faces: number[];
  color?: string;
  opacity?: number;
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: [number, number, number];
}

export function STLMesh({
  vertices,
  faces,
  color = '#4a90d9',
  opacity = 1,
  position = [0, 0, 0],
  rotation = [0, 0, 0],
  scale = [1, 1, 1],
}: STLMeshProps) {
  const meshRef = useRef<THREE.Mesh>(null);

  useEffect(() => {
    if (!vertices.length || !faces.length) {
      console.warn('STLMesh: Empty vertices or faces', { vertices: vertices.length, faces: faces.length });
      return;
    }

    console.log('STLMesh: Creating geometry with', vertices.length, 'vertices,', faces.length, 'faces');

    // Convert flat arrays to Three.js geometry
    const geometry = new THREE.BufferGeometry();
    
    // Convert array of arrays to flat array
    const flatVertices = vertices.flat();
    const flatFaces = faces.flat();
    
    // Set vertices
    const positionArray = new Float32Array(flatVertices);
    geometry.setAttribute('position', new THREE.BufferAttribute(positionArray, 3));
    
    // Set faces (triangles)
    const indexArray = new Uint32Array(flatFaces);
    geometry.setIndex(new THREE.BufferAttribute(indexArray, 1));
    
    // Compute normals
    geometry.computeVertexNormals();
    
    if (meshRef.current) {
      meshRef.current.geometry.dispose();
      meshRef.current.geometry = geometry;
    }

  }, [vertices, faces]);

  return (
    <mesh
      ref={meshRef}
      position={position}
      rotation={rotation}
      scale={scale}
    >
      <bufferGeometry />
      <meshStandardMaterial
        color={color}
        transparent={opacity < 1}
        opacity={opacity}
        side={THREE.DoubleSide}
        metalness={0.3}
        roughness={0.7}
      />
    </mesh>
  );
}

// Component to render multiple STL meshes
interface STLCollectionProps {
  meshes: Record<string, { vertices: number[]; faces: number[] }>;
}

const MATERIAL_COLORS: Record<string, string> = {
  stator_core: '#708090',    // Slate gray (steel)
  rotor_core: '#708090',     // Slate gray (steel)
  shaft: '#505050',          // Dark gray (steel)
  magnet: '#ff4444',        // Red (permanent magnet)
  coil: '#b87333',          // Copper color
  air_gap: '#87ceeb',       // Light blue (air)
  default: '#4a90d9',       // Default blue
};

function getColorForMesh(name: string): string {
  if (name.startsWith('magnet')) return MATERIAL_COLORS.magnet;
  if (name.startsWith('coil')) return MATERIAL_COLORS.coil;
  if (name.startsWith('stator')) return MATERIAL_COLORS.stator_core;
  if (name.startsWith('rotor')) return MATERIAL_COLORS.rotor_core;
  if (name.startsWith('shaft')) return MATERIAL_COLORS.shaft;
  return MATERIAL_COLORS.default;
}

export function STLCollection({ meshes }: STLCollectionProps) {
  const meshNames = Object.keys(meshes);
  console.log('STLCollection: Rendering', meshNames.length, 'meshes:', meshNames.slice(0, 5));
  
  if (meshNames.length === 0) {
    console.warn('STLCollection: No meshes to render! Check if stlMeshes state is populated.');
    return <group><mesh><boxGeometry args={[10, 10, 10]} /><meshBasicMaterial color="red" wireframe /></mesh></group>;
  }
  
  return (
    <group>
      {Object.entries(meshes).map(([name, mesh]) => (
        <STLMesh
          key={name}
          vertices={mesh.vertices}
          faces={mesh.faces}
          color={getColorForMesh(name)}
        />
      ))}
    </group>
  );
}

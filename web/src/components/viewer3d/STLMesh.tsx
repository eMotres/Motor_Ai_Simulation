import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { LineSegments } from 'three';
import { useUIStore } from '../../stores/motorStore';

interface STLMeshProps {
  vertices: number[];
  faces: number[];
  color?: string;
  opacity?: number;
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: [number, number, number];
  showEdges?: boolean;
  materialType?: 'stator' | 'coil' | 'magnet' | 'rotor' | 'shaft';
}

// PBR Material configurations for Fusion 360 style - base colors only, metalness/roughness from UI
const PBR_MATERIALS = {
  stator: {
    color: '#7f8c8d',
  },
  coil: {
    color: '#b87333',
  },
  magnet: {
    color: '#4a90d9',
  },
  rotor: {
    color: '#7f8c8d',
  },
  shaft: {
    color: '#505050',
  },
  default: {
    color: '#4a90d9',
  },
};

function getMaterialColor(materialType?: string): string {
  if (!materialType) return PBR_MATERIALS.default.color;
  return PBR_MATERIALS[materialType as keyof typeof PBR_MATERIALS]?.color || PBR_MATERIALS.default.color;
}

export function STLMesh({
  vertices,
  faces,
  color = '#4a90d9',
  opacity = 1,
  position = [0, 0, 0],
  rotation = [0, 0, 0],
  scale = [1, 1, 1],
  showEdges = true,
  materialType,
}: STLMeshProps) {
  const { metalness, roughness, envIntensity } = useUIStore();
  const meshRef = useRef<THREE.Mesh>(null);
  const edgesRef = useRef<THREE.LineSegments>(null);
  
  // Get base color from material type
  const baseColor = materialType ? getMaterialColor(materialType) : color;

  useEffect(() => {
    if (!vertices.length || !faces.length) {
      console.warn('STLMesh: Empty vertices or faces', { vertices: vertices.length, faces: faces.length });
      return;
    }

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

    // Create edge geometry for outlines
    if (showEdges && edgesRef.current) {
      const edgeGeometry = new THREE.EdgesGeometry(geometry, 15); // 15 degree threshold for sharp edges
      edgesRef.current.geometry.dispose();
      edgesRef.current.geometry = edgeGeometry;
    }

  }, [vertices, faces, showEdges]);

  return (
    <group position={position} rotation={rotation} scale={scale}>
      <mesh ref={meshRef}>
        <bufferGeometry />
        <meshStandardMaterial
          color={baseColor}
          transparent={opacity < 1}
          opacity={opacity}
          side={THREE.DoubleSide}
          metalness={metalness}
          roughness={roughness}
          envMapIntensity={envIntensity * 1.5}
        />
      </mesh>
      
      {/* Edge outlines for CAD look - solid dark grey lines */}
      {showEdges && (
        <lineSegments ref={edgesRef}>
          <lineBasicMaterial color={0x333333} linewidth={1} />
        </lineSegments>
      )}
    </group>
  );
}

// Component to render multiple STL meshes
interface STLCollectionProps {
  meshes: Record<string, { vertices: number[]; faces: number[] }>;
  showEdges?: boolean;
}

function getMaterialTypeFromName(name: string): 'stator' | 'coil' | 'magnet' | 'rotor' | 'shaft' | undefined {
  if (name.startsWith('stator')) return 'stator';
  if (name.startsWith('coil')) return 'coil';
  if (name.startsWith('magnet')) return 'magnet';
  if (name.startsWith('rotor')) return 'rotor';
  if (name.startsWith('shaft')) return 'shaft';
  return undefined;
}

export function STLCollection({ meshes, showEdges = true }: STLCollectionProps) {
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
          materialType={getMaterialTypeFromName(name)}
          showEdges={showEdges}
        />
      ))}
    </group>
  );
}

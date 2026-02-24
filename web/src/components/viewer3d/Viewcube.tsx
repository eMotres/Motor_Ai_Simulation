import React, { useRef, useState, useCallback, useEffect, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';

// Standard CAD coordinate system: X-Right, Y-Up, Z-Front (toward viewer)
interface ViewDirection {
  name: string;
  position: [number, number, number];  // Direction vector for camera
  rotation: [number, number, number];  // Euler rotation for face
  label: string;
}

// Face definitions aligned with CAD standard coordinates
// X+ = Right, Y+ = Up, Z+ = Front (toward viewer)
const FACE_VIEWS: ViewDirection[] = [
  { name: 'front', position: [0, 0, 1], rotation: [0, 0, 0], label: 'FRONT' },     // +Z = camera at +Z looks at -Z
  { name: 'back', position: [0, 0, -1], rotation: [0, Math.PI, 0], label: 'BACK' },    // -Z = camera at -Z looks at +Z
  { name: 'top', position: [0, 1, 0], rotation: [-Math.PI / 2, 0, 0], label: 'TOP' },    // +Y = looking down
  { name: 'bottom', position: [0, -1, 0], rotation: [Math.PI / 2, 0, 0], label: 'BOTTOM' }, // -Y = looking up
  { name: 'right', position: [-1, 0, 0], rotation: [0, Math.PI / 2, 0], label: 'RIGHT' },  // -X = looking from right
  { name: 'left', position: [1, 0, 0], rotation: [0, -Math.PI / 2, 0], label: 'LEFT' },   // +X = looking from left
];

// Pre-generate textures for all faces (normal and hover states)
function createFaceTextures(): Record<string, { normal: THREE.Texture; hover: THREE.Texture }> {
  const textures: Record<string, { normal: THREE.Texture; hover: THREE.Texture }> = {};
  
  FACE_VIEWS.forEach(view => {
    // Determine if we need to flip the texture
    const needsFlip = ['left', 'right', 'back'].includes(view.name);
    
    // Normal state - dark background, gray text
    const normalCanvas = document.createElement('canvas');
    normalCanvas.width = 256;
    normalCanvas.height = 256;
    const normalCtx = normalCanvas.getContext('2d')!;
    
    if (needsFlip) {
      normalCtx.translate(256, 0);
      normalCtx.scale(-1, 1);
    }
    
    normalCtx.fillStyle = '#1e293b';
    normalCtx.fillRect(0, 0, 256, 256);
    normalCtx.strokeStyle = '#475569';
    normalCtx.lineWidth = 8;
    normalCtx.strokeRect(4, 4, 248, 248);
    normalCtx.fillStyle = '#94a3b8';
    normalCtx.font = 'bold 42px Arial, sans-serif';
    normalCtx.textAlign = 'center';
    normalCtx.textBaseline = 'middle';
    normalCtx.fillText(view.label, 128, 128);
    
    // Reset transform for hover
    normalCtx.setTransform(1, 0, 0, 1, 0, 0);
    
    // Hover state - lighter background, white text
    const hoverCanvas = document.createElement('canvas');
    hoverCanvas.width = 256;
    hoverCanvas.height = 256;
    const hoverCtx = hoverCanvas.getContext('2d')!;
    
    if (needsFlip) {
      hoverCtx.translate(256, 0);
      hoverCtx.scale(-1, 1);
    }
    
    hoverCtx.fillStyle = '#334155';
    hoverCtx.fillRect(0, 0, 256, 256);
    hoverCtx.strokeStyle = '#64748b';
    hoverCtx.lineWidth = 8;
    hoverCtx.strokeRect(4, 4, 248, 248);
    hoverCtx.fillStyle = '#ffffff';
    hoverCtx.font = 'bold 42px Arial, sans-serif';
    hoverCtx.textAlign = 'center';
    hoverCtx.textBaseline = 'middle';
    hoverCtx.fillText(view.label, 128, 128);
    
    textures[view.name] = {
      normal: new THREE.CanvasTexture(normalCanvas),
      hover: new THREE.CanvasTexture(hoverCanvas)
    };
  });
  
  return textures;
}

// Pre-generate textures once
const FACE_TEXTURES = createFaceTextures();

interface ViewcubeSceneProps {
  onViewChange?: (view: string) => void;
}

const ViewcubeScene: React.FC<ViewcubeSceneProps> = ({ onViewChange }) => {
  const cubeRef = useRef<THREE.Group>(null);
  const [hoveredFace, setHoveredFace] = useState<string | null>(null);

  // Use identity quaternion - camera at [0, 0, 250] looks at +Z (FRONT)
  // ViewCube starts with FRONT face visible to match main camera
  const internalQuat = useRef(new THREE.Quaternion().identity());

  useEffect(() => {
    const handleCameraChange = (e: Event) => {
      const customEvent = e as CustomEvent;
      if (customEvent.detail && customEvent.detail.quaternion) {
        // Apply 180° Y offset and invert to fix rotation direction
        const offset = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);
        const cameraQuat = customEvent.detail.quaternion.clone();
        internalQuat.current.copy(cameraQuat).multiply(offset).invert();
      }
    };
    window.addEventListener('mainCameraChange', handleCameraChange);
    return () => window.removeEventListener('mainCameraChange', handleCameraChange);
  }, []);

  useFrame(() => {
    if (cubeRef.current) {
      cubeRef.current.quaternion.slerp(internalQuat.current, 0.15);
    }
  });

  const handleFaceClick = useCallback((view: ViewDirection) => {
    onViewChange?.(view.name);
    const distance = 200;
    const targetPosition = new THREE.Vector3(
      view.position[0] * distance,
      view.position[1] * distance,
      view.position[2] * distance
    );
    window.dispatchEvent(new CustomEvent('viewcubeNavigate', {
      detail: { position: targetPosition, rotation: view.rotation, name: view.name }
    }));
  }, [onViewChange]);

  const handlePointerOver = useCallback((faceName: string) => {
    setHoveredFace(faceName);
    document.body.style.cursor = 'pointer';
  }, []);

  const handlePointerOut = useCallback(() => {
    setHoveredFace(null);
    document.body.style.cursor = 'default';
  }, []);

  const cubeSize = 28;

  return (
    <>
      {/* Rotating Cube with axes inside */}
      <group ref={cubeRef}>
        {/* Main cube body */}
        <mesh>
          <boxGeometry args={[cubeSize, cubeSize, cubeSize]} />
          <meshStandardMaterial 
            color={hoveredFace ? '#334155' : '#0f172a'}
            roughness={0.8}
            metalness={0.1}
          />
        </mesh>

        {/* Wireframe edges */}
        <lineSegments>
          <edgesGeometry args={[new THREE.BoxGeometry(cubeSize, cubeSize, cubeSize)]} />
          <lineBasicMaterial color="#475569" />
        </lineSegments>

        {/* Face buttons with textures */}
        {FACE_VIEWS.map((view) => {
          const offset = cubeSize / 2 + 0.1;
          const pos = new THREE.Vector3(
            view.position[0] * offset,
            view.position[1] * offset,
            view.position[2] * offset
          );
          const rot = new THREE.Euler(view.rotation[0], view.rotation[1], view.rotation[2]);
          const isHovered = hoveredFace === view.name;
          const texture = isHovered ? FACE_TEXTURES[view.name].hover : FACE_TEXTURES[view.name].normal;
          
          return (
            <group key={view.name} position={pos} rotation={rot}>
              <mesh
                onClick={() => handleFaceClick(view)}
                onPointerOver={() => handlePointerOver(view.name)}
                onPointerOut={handlePointerOut}
              >
                <planeGeometry args={[cubeSize * 0.85, cubeSize * 0.85]} />
                <meshStandardMaterial
                  map={texture}
                  transparent
                  opacity={1}
                  side={THREE.DoubleSide}
                />
              </mesh>
            </group>
          );
        })}

        {/* XYZ Axis arrows - at back-bottom-left corner, 1.2x cube size */}
        {/* X Axis (Red) */}
        <group position={[-cubeSize / 2, -cubeSize / 2, -cubeSize / 2]} rotation={[0, 0, -Math.PI / 2]}>
          {/* Cylinder is 1.2x cube size (33.6 units), offset by half */}
          <mesh position={[0, 16.8, 0]}>
            <cylinderGeometry args={[0.5, 0.5, 33.6, 8]} />
            <meshStandardMaterial color="#ef4444" roughness={0.5} metalness={0.3} depthTest={false} />
          </mesh>
          <mesh position={[0, 37, 0]}>
            <coneGeometry args={[1.5, 5, 8]} />
            <meshStandardMaterial color="#ef4444" roughness={0.5} metalness={0.3} depthTest={false} />
          </mesh>
          <Html position={[0, 45, 0]} center style={{ pointerEvents: 'none' }}>
            <div style={{ color: '#ef4444', fontWeight: 'bold', fontSize: '11px', fontFamily: 'Arial', textShadow: '1px 1px 2px black' }}>X</div>
          </Html>
        </group>

        {/* Y Axis (Green) */}
        <group position={[-cubeSize / 2, -cubeSize / 2, -cubeSize / 2]}>
          <mesh position={[0, 16.8, 0]}>
            <cylinderGeometry args={[0.5, 0.5, 33.6, 8]} />
            <meshStandardMaterial color="#22c55e" roughness={0.5} metalness={0.3} depthTest={false} />
          </mesh>
          <mesh position={[0, 37, 0]}>
            <coneGeometry args={[1.5, 5, 8]} />
            <meshStandardMaterial color="#22c55e" roughness={0.5} metalness={0.3} depthTest={false} />
          </mesh>
          <Html position={[0, 45, 0]} center style={{ pointerEvents: 'none' }}>
            <div style={{ color: '#22c55e', fontWeight: 'bold', fontSize: '11px', fontFamily: 'Arial', textShadow: '1px 1px 2px black' }}>Y</div>
          </Html>
        </group>

        {/* Z Axis (Blue) */}
        <group position={[-cubeSize / 2, -cubeSize / 2, -cubeSize / 2]} rotation={[Math.PI / 2, 0, 0]}>
          <mesh position={[0, 16.8, 0]}>
            <cylinderGeometry args={[0.5, 0.5, 33.6, 8]} />
            <meshStandardMaterial color="#3b82f6" roughness={0.5} metalness={0.3} depthTest={false} />
          </mesh>
          <mesh position={[0, 37, 0]}>
            <coneGeometry args={[1.5, 5, 8]} />
            <meshStandardMaterial color="#3b82f6" roughness={0.5} metalness={0.3} depthTest={false} />
          </mesh>
          <Html position={[0, 45, 0]} center style={{ pointerEvents: 'none' }}>
            <div style={{ color: '#3b82f6', fontWeight: 'bold', fontSize: '11px', fontFamily: 'Arial', textShadow: '1px 1px 2px black' }}>Z</div>
          </Html>
        </group>
      </group>

      {/* Isometric view buttons - separate group */}
      <IsometricButtons onFaceClick={handleFaceClick} onPointerOver={handlePointerOver} onPointerOut={handlePointerOut} />
    </>
  );
};

// Isometric view button spheres
const IsometricButtons: React.FC<{
  onFaceClick: (view: ViewDirection) => void;
  onPointerOver: (name: string) => void;
  onPointerOut: () => void;
}> = ({ onFaceClick, onPointerOver, onPointerOut }) => {
  const groupRef = useRef<THREE.Group>(null);
  const internalQuat = useRef(new THREE.Quaternion());

  useEffect(() => {
    const handleCameraChange = (e: Event) => {
      const customEvent = e as CustomEvent;
      if (customEvent.detail && customEvent.detail.quaternion) {
        // Apply 180° Y offset and invert to fix rotation direction
        const offset = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);
        const cameraQuat = customEvent.detail.quaternion.clone();
        internalQuat.current.copy(cameraQuat).multiply(offset).invert();
      }
    };
    window.addEventListener('mainCameraChange', handleCameraChange);
    return () => window.removeEventListener('mainCameraChange', handleCameraChange);
  }, []);

  useFrame(() => {
    if (groupRef.current) {
      groupRef.current.quaternion.slerp(internalQuat.current, 0.15);
    }
  });

  const ISO_VIEWS: ViewDirection[] = [
    { name: 'iso1', position: [1, 1, 1], rotation: [-0.615, 0.785, 0], label: 'ISO' },
    { name: 'iso2', position: [-1, 1, 1], rotation: [-0.615, 2.356, 0], label: 'ISO' },
    { name: 'iso3', position: [1, 1, -1], rotation: [-0.615, -0.785, 0], label: 'ISO' },
    { name: 'iso4', position: [-1, 1, -1], rotation: [-0.615, -2.356, 0], label: 'ISO' },
  ];

  const cubeSize = 28;
  const [hoveredIso, setHoveredIso] = useState<string | null>(null);

  return (
    <group ref={groupRef}>
      {ISO_VIEWS.map((view) => {
        const offset = cubeSize / 2 + 14;
        const pos = new THREE.Vector3(
          view.position[0] * offset,
          view.position[1] * offset,
          view.position[2] * offset
        );

        return (
          <mesh
            key={view.name}
            position={pos}
            onClick={() => onFaceClick(view)}
            onPointerOver={() => {
              setHoveredIso(view.name);
              onPointerOver(view.name);
            }}
            onPointerOut={() => {
              setHoveredIso(null);
              onPointerOut();
            }}
          >
            <sphereGeometry args={[5, 16, 16]} />
            <meshStandardMaterial
              color={hoveredIso === view.name ? '#a855f7' : '#334155'}
              roughness={0.6}
              metalness={0.2}
            />
          </mesh>
        );
      })}
    </group>
  );
};

interface ViewcubeProps {
  size?: number;
  position?: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left';
}

const Viewcube: React.FC<ViewcubeProps> = ({ size = 140, position = 'top-right' }) => {
  const positionStyles: Record<string, React.CSSProperties> = {
    'top-right': { top: 80, right: 20 },
    'top-left': { top: 80, left: 20 },
    'bottom-right': { bottom: 20, right: 20 },
    'bottom-left': { bottom: 20, left: 20 },
  };

  return (
    <div style={{ position: 'absolute', ...positionStyles[position], width: size, height: size, zIndex: 100 }}>
      <Canvas
        camera={{ position: [0, 0, 80], fov: 45, near: 0.1, far: 1000 }}
        style={{ 
          background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)', 
          borderRadius: '12px', 
          border: '2px solid #334155',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.4)'
        }}
      >
        <ambientLight intensity={1.2} />
        <directionalLight position={[50, 50, 50]} intensity={1.5} />
        <directionalLight position={[-30, 30, -30]} intensity={0.5} />
        <ViewcubeScene />
      </Canvas>
    </div>
  );
};

export default Viewcube;
export { Viewcube };

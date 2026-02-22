import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';

interface ViewDirection {
  name: string;
  position: [number, number, number];
  rotation: [number, number, number];
  label: string;
  color: string;
}

const FACE_VIEWS: ViewDirection[] = [
  { name: 'front', position: [0, 0, 1], rotation: [0, 0, 0], label: 'Front', color: '#3b82f6' },
  { name: 'back', position: [0, 0, -1], rotation: [0, Math.PI, 0], label: 'Back', color: '#3b82f6' },
  { name: 'top', position: [0, 1, 0], rotation: [Math.PI / 2, 0, 0], label: 'Top', color: '#22c55e' },
  { name: 'bottom', position: [0, -1, 0], rotation: [-Math.PI / 2, 0, 0], label: 'Bottom', color: '#22c55e' },
  { name: 'right', position: [1, 0, 0], rotation: [0, Math.PI / 2, 0], label: 'Right', color: '#ef4444' },
  { name: 'left', position: [-1, 0, 0], rotation: [0, -Math.PI / 2, 0], label: 'Left', color: '#ef4444' },
];

const ISO_VIEWS: ViewDirection[] = [
  { name: 'iso1', position: [1, 1, 1], rotation: [-0.615, 0.785, 0], label: 'ISO', color: '#a855f7' },
  { name: 'iso2', position: [-1, 1, 1], rotation: [-0.615, 2.356, 0], label: 'ISO', color: '#a855f7' },
  { name: 'iso3', position: [1, 1, -1], rotation: [-0.615, -0.785, 0], label: 'ISO', color: '#a855f7' },
  { name: 'iso4', position: [-1, 1, -1], rotation: [-0.615, -2.356, 0], label: 'ISO', color: '#a855f7' },
  { name: 'iso5', position: [1, -1, 1], rotation: [0.615, 0.785, 0], label: 'ISO', color: '#a855f7' },
  { name: 'iso6', position: [-1, -1, 1], rotation: [0.615, 2.356, 0], label: 'ISO', color: '#a855f7' },
];

interface ViewcubeSceneProps {
  onViewChange?: (view: string) => void;
}

const ViewcubeScene: React.FC<ViewcubeSceneProps> = ({ onViewChange }) => {
  const cubeRef = useRef<THREE.Group>(null);
  const [hoveredFace, setHoveredFace] = useState<string | null>(null);
  const internalQuat = useRef(new THREE.Quaternion());

  useEffect(() => {
    const handleCameraChange = (e: Event) => {
      const customEvent = e as CustomEvent;
      if (customEvent.detail && customEvent.detail.quaternion) {
        internalQuat.current.copy(customEvent.detail.quaternion);
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

  const cubeSize = 20;

  return (
    <>
      {/* XYZ Axis Labels - Fixed position */}
      <group position={[0, 0, 0]}>
        {/* X Axis - Red */}
        <group position={[35, -15, 0]}>
          <mesh rotation={[0, 0, -Math.PI / 2]}>
            <cylinderGeometry args={[1.5, 1.5, 30, 8]} />
            <meshStandardMaterial color="#ef4444" />
          </mesh>
          <mesh position={[15, 0, 0]} rotation={[0, 0, -Math.PI / 2]}>
            <coneGeometry args={[3, 6, 8]} />
            <meshStandardMaterial color="#ef4444" />
          </mesh>
          <Html position={[18, 0, 0]} center style={{ pointerEvents: 'none' }}>
            <div style={{ color: '#ef4444', fontWeight: 'bold', fontSize: '12px', fontFamily: 'Arial' }}>X</div>
          </Html>
        </group>
        
        {/* Y Axis - Green */}
        <group position={[-15, 35, 0]}>
          <mesh>
            <cylinderGeometry args={[1.5, 1.5, 30, 8]} />
            <meshStandardMaterial color="#22c55e" />
          </mesh>
          <mesh position={[0, 15, 0]}>
            <coneGeometry args={[3, 6, 8]} />
            <meshStandardMaterial color="#22c55e" />
          </mesh>
          <Html position={[0, 18, 0]} center style={{ pointerEvents: 'none' }}>
            <div style={{ color: '#22c55e', fontWeight: 'bold', fontSize: '12px', fontFamily: 'Arial' }}>Y</div>
          </Html>
        </group>
        
        {/* Z Axis - Blue */}
        <group position={[-15, -15, 25]} rotation={[Math.PI / 2, 0, 0]}>
          <mesh>
            <cylinderGeometry args={[1.5, 1.5, 30, 8]} />
            <meshStandardMaterial color="#3b82f6" />
          </mesh>
          <mesh position={[0, 15, 0]}>
            <coneGeometry args={[3, 6, 8]} />
            <meshStandardMaterial color="#3b82f6" />
          </mesh>
          <Html position={[0, 18, 0]} center style={{ pointerEvents: 'none' }}>
            <div style={{ color: '#3b82f6', fontWeight: 'bold', fontSize: '12px', fontFamily: 'Arial' }}>Z</div>
          </Html>
        </group>
      </group>

      {/* Rotating Cube */}
      <group ref={cubeRef}>
        {/* Main cube body */}
        <mesh>
          <boxGeometry args={[cubeSize, cubeSize, cubeSize]} />
          <meshStandardMaterial 
            color={hoveredFace ? '#1e293b' : '#0f172a'} 
            transparent 
            opacity={0.9}
          />
        </mesh>
        
        {/* Wireframe edges */}
        <lineSegments>
          <edgesGeometry args={[new THREE.BoxGeometry(cubeSize, cubeSize, cubeSize)]} />
          <lineBasicMaterial color="#475569" />
        </lineSegments>
        
        {/* Face buttons */}
        {FACE_VIEWS.map((view) => {
          const offset = cubeSize / 2 + 0.5;
          const pos = new THREE.Vector3(
            view.position[0] * offset,
            view.position[1] * offset,
            view.position[2] * offset
          );
          const rot = new THREE.Euler(view.rotation[0], view.rotation[1], view.rotation[2]);
          
          return (
            <group key={view.name} position={pos} rotation={rot}>
              <mesh
                onClick={() => handleFaceClick(view)}
                onPointerOver={() => handlePointerOver(view.name)}
                onPointerOut={handlePointerOut}
              >
                <planeGeometry args={[12, 12]} />
                <meshStandardMaterial
                  color={hoveredFace === view.name ? view.color : '#1e293b'}
                  transparent
                  opacity={hoveredFace === view.name ? 1 : 0.7}
                  side={THREE.DoubleSide}
                />
              </mesh>
              <Html
                position={[0, 0, 0.1]}
                center
                style={{ pointerEvents: 'none', userSelect: 'none' }}
              >
                <div style={{
                  color: hoveredFace === view.name ? '#fff' : '#94a3b8',
                  fontSize: '9px',
                  fontWeight: 'bold',
                  fontFamily: 'Arial, sans-serif',
                  textShadow: '0 1px 2px rgba(0,0,0,0.8)',
                  whiteSpace: 'nowrap',
                }}>
                  {view.label}
                </div>
              </Html>
            </group>
          );
        })}
        
        {/* Corner spheres - isometric view buttons */}
        {ISO_VIEWS.map((view) => {
          const offset = cubeSize / 2 + 5;
          const pos = new THREE.Vector3(
            view.position[0] * offset,
            view.position[1] * offset,
            view.position[2] * offset
          );
          
          return (
            <mesh
              key={view.name}
              position={pos}
              onClick={() => handleFaceClick(view)}
              onPointerOver={() => handlePointerOver(view.name)}
              onPointerOut={handlePointerOut}
            >
              <sphereGeometry args={[6, 16, 16]} />
              <meshStandardMaterial 
                color={hoveredFace === view.name ? '#a855f7' : '#334155'}
                transparent
                opacity={0.9}
              />
            </mesh>
          );
        })}
      </group>
    </>
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
        style={{ 
          background: 'linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%)', 
          borderRadius: '12px', 
          border: '2px solid #334155',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.4)'
        }}
        camera={{ position: [60, 60, 60], fov: 45 }}
      >
        <ambientLight intensity={1.2} />
        <directionalLight position={[50, 50, 50]} intensity={1.5} />
        <directionalLight position={[-30, 30, -30]} intensity={0.5} />
        <ViewcubeScene />
      </Canvas>
    </div>
  );
};

export { Viewcube as default, Viewcube };

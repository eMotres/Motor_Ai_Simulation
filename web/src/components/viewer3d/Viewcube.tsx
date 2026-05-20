import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import * as THREE from 'three';

interface ViewDir {
  name: string;
  position: [number, number, number];
  rotation: [number, number, number];
  label: string;
}

// RIGHT = +X, LEFT = -X
const FACES: ViewDir[] = [
  { name: 'front',  position: [ 0,  0,  1], rotation: [0, 0, 0],            label: 'FRONT'  },
  { name: 'back',   position: [ 0,  0, -1], rotation: [0, Math.PI, 0],      label: 'BACK'   },
  { name: 'top',    position: [ 0,  1,  0], rotation: [-Math.PI / 2, 0, 0], label: 'TOP'    },
  { name: 'bottom', position: [ 0, -1,  0], rotation: [ Math.PI / 2, 0, 0], label: 'BOTTOM' },
  { name: 'right',  position: [ 1,  0,  0], rotation: [0,  Math.PI / 2, 0], label: 'RIGHT'  },
  { name: 'left',   position: [-1,  0,  0], rotation: [0, -Math.PI / 2, 0], label: 'LEFT'   },
];

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// Light faces on dark background — blue highlight on hover
function makeTexture(label: string, hovered: boolean, flip: boolean): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 256;
  const ctx = canvas.getContext('2d')!;

  if (flip) {
    ctx.translate(256, 0);
    ctx.scale(-1, 1);
  }

  ctx.fillStyle = hovered ? '#2563eb' : '#c8d8e8';
  ctx.fillRect(0, 0, 256, 256);

  ctx.strokeStyle = hovered ? '#1d4ed8' : '#7a96b0';
  ctx.lineWidth = 6;
  roundRect(ctx, 4, 4, 248, 248, 10);
  ctx.stroke();

  ctx.fillStyle = hovered ? '#ffffff' : '#1a2a3a';
  // Larger font: BOTTOM(6)=50, FRONT/RIGHT(5)=60, others=72
  const fontSize = label.length > 5 ? 50 : label.length > 4 ? 60 : 72;
  ctx.font = `bold ${fontSize}px "Segoe UI", Arial, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(label, 128, 128);

  return new THREE.CanvasTexture(canvas);
}

// Pre-generate textures (only BACK needs mirror-flip)
const FACE_TEX: Record<string, { n: THREE.CanvasTexture; h: THREE.CanvasTexture }> = {};
FACES.forEach((f) => {
  const flip = f.name === 'back';
  FACE_TEX[f.name] = {
    n: makeTexture(f.label, false, flip),
    h: makeTexture(f.label, true, flip),
  };
});

// ─── Cube + axes scene ────────────────────────────────────────────────────────

const CubeScene: React.FC = () => {
  const groupRef = useRef<THREE.Group>(null);
  const targetQ  = useRef(new THREE.Quaternion());
  const [hovered, setHovered] = useState<string | null>(null);

  useEffect(() => {
    const onCam = (e: Event) => {
      const q = (e as CustomEvent).detail?.quaternion as THREE.Quaternion | undefined;
      if (!q) return;
      const yFlip = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);
      targetQ.current.copy(q).multiply(yFlip).invert();
    };
    window.addEventListener('mainCameraChange', onCam);
    return () => window.removeEventListener('mainCameraChange', onCam);
  }, []);

  useFrame(() => {
    groupRef.current?.quaternion.slerp(targetQ.current, 0.12);
  });

  const navigate = useCallback((position: [number, number, number], name: string) => {
    const pos = new THREE.Vector3(...position).multiplyScalar(200);
    window.dispatchEvent(new CustomEvent('viewcubeNavigate', { detail: { position: pos, name } }));
  }, []);

  // S = half-size of cube
  const S = 13;
  // Arrow length from corner: tips reach -S + AL = -13 + 35 = 22 (past cube face at 13)
  const AL = S * 2.7;

  return (
    <group ref={groupRef}>
      {/* Cube body */}
      <mesh>
        <boxGeometry args={[S * 2, S * 2, S * 2]} />
        <meshStandardMaterial color="#b0c4d8" roughness={0.6} metalness={0.0} />
      </mesh>

      {/* Edges */}
      <lineSegments>
        <edgesGeometry args={[new THREE.BoxGeometry(S * 2, S * 2, S * 2)]} />
        <lineBasicMaterial color="#4a6070" />
      </lineSegments>

      {/* Face label planes */}
      {FACES.map((face) => {
        const off = S + 0.1;
        const pos  = new THREE.Vector3(...face.position).multiplyScalar(off);
        const rot  = new THREE.Euler(...face.rotation);
        const isHov = hovered === face.name;

        return (
          <group key={face.name} position={pos} rotation={rot}>
            <mesh
              onClick={(e) => { e.stopPropagation(); navigate(face.position, face.name); }}
              onPointerOver={(e) => { e.stopPropagation(); setHovered(face.name); document.body.style.cursor = 'pointer'; }}
              onPointerOut={() => { setHovered(null); document.body.style.cursor = 'default'; }}
            >
              <planeGeometry args={[S * 1.9, S * 1.9]} />
              <meshStandardMaterial
                map={isHov ? FACE_TEX[face.name].h : FACE_TEX[face.name].n}
                transparent
                side={THREE.DoubleSide}
              />
            </mesh>
          </group>
        );
      })}

      {/* Axis arrows from back-bottom-left corner [-S,-S,-S] */}
      <group position={[-S, -S, -S]}>
        {/* X – red */}
        <group rotation={[0, 0, -Math.PI / 2]}>
          <mesh position={[0, AL / 2, 0]}>
            <cylinderGeometry args={[0.6, 0.6, AL, 8]} />
            <meshStandardMaterial color="#e53e3e" roughness={0.4} depthTest={false} />
          </mesh>
          <mesh position={[0, AL + 2.5, 0]}>
            <coneGeometry args={[1.8, 5, 8]} />
            <meshStandardMaterial color="#e53e3e" roughness={0.4} depthTest={false} />
          </mesh>
        </group>

        {/* Y – green */}
        <mesh position={[0, AL / 2, 0]}>
          <cylinderGeometry args={[0.6, 0.6, AL, 8]} />
          <meshStandardMaterial color="#38a169" roughness={0.4} depthTest={false} />
        </mesh>
        <mesh position={[0, AL + 2.5, 0]}>
          <coneGeometry args={[1.8, 5, 8]} />
          <meshStandardMaterial color="#38a169" roughness={0.4} depthTest={false} />
        </mesh>

        {/* Z – blue */}
        <group rotation={[Math.PI / 2, 0, 0]}>
          <mesh position={[0, AL / 2, 0]}>
            <cylinderGeometry args={[0.6, 0.6, AL, 8]} />
            <meshStandardMaterial color="#3182ce" roughness={0.4} depthTest={false} />
          </mesh>
          <mesh position={[0, AL + 2.5, 0]}>
            <coneGeometry args={[1.8, 5, 8]} />
            <meshStandardMaterial color="#3182ce" roughness={0.4} depthTest={false} />
          </mesh>
        </group>
      </group>
    </group>
  );
};

// ─── Wrapper ──────────────────────────────────────────────────────────────────

const Viewcube: React.FC<{ size?: number }> = ({ size = 100 }) => {
  const [hovHome, setHovHome] = useState(false);

  const handleHome = useCallback(() => {
    const pos = new THREE.Vector3(0, 0, 200);
    window.dispatchEvent(new CustomEvent('viewcubeNavigate', { detail: { position: pos, name: 'front' } }));
  }, []);

  return (
    <div style={{ position: 'absolute', top: 80, right: 16, zIndex: 100, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>

      {/* Home button */}
      <button
        onClick={handleHome}
        onMouseEnter={() => setHovHome(true)}
        onMouseLeave={() => setHovHome(false)}
        title="Home view"
        style={{
          alignSelf: 'flex-start',
          background: hovHome ? 'rgba(37,99,235,0.18)' : 'rgba(255,255,255,0.06)',
          border: `1px solid ${hovHome ? 'rgba(37,99,235,0.5)' : 'rgba(120,160,190,0.3)'}`,
          borderRadius: 5,
          cursor: 'pointer',
          padding: '3px 5px',
          display: 'flex',
          alignItems: 'center',
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke={hovHome ? '#60a5fa' : '#7090a8'} strokeWidth="2.2"
          strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12L12 3l9 9" />
          <path d="M9 21V12h6v9" />
          <path d="M5 10v11h14V10" />
        </svg>
      </button>

      {/* 3D ViewCube — orthographic + true isometric angle like Fusion 360 */}
      <div style={{ width: size, height: size }}>
        <Canvas
          orthographic
          camera={{ position: [18, 28, 72], zoom: 1.1, near: 0.1, far: 500 }}
          style={{
            background: 'rgba(16, 22, 36, 0.90)',
            borderRadius: 8,
            border: '1px solid rgba(80,110,140,0.35)',
            boxShadow: '0 2px 10px rgba(0,0,0,0.45)',
          }}
        >
          <ambientLight intensity={1.5} />
          <directionalLight position={[50, 80, 60]} intensity={1.0} />
          <directionalLight position={[-30, 10, -20]} intensity={0.25} />
          <CubeScene />
        </Canvas>
      </div>
    </div>
  );
};

export default Viewcube;
export { Viewcube };

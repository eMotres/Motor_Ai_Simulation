import React, { useRef, useState, useCallback, useEffect } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import * as THREE from 'three';

interface ViewDir {
  name: string;
  position: [number, number, number];
  rotation: [number, number, number];
  label: string;
}

// Fixed: RIGHT = +X, LEFT = -X (was swapped before)
const FACES: ViewDir[] = [
  { name: 'front',  position: [ 0,  0,  1], rotation: [0, 0, 0],             label: 'FRONT'  },
  { name: 'back',   position: [ 0,  0, -1], rotation: [0, Math.PI, 0],       label: 'BACK'   },
  { name: 'top',    position: [ 0,  1,  0], rotation: [-Math.PI / 2, 0, 0],  label: 'TOP'    },
  { name: 'bottom', position: [ 0, -1,  0], rotation: [ Math.PI / 2, 0, 0],  label: 'BOTTOM' },
  { name: 'right',  position: [ 1,  0,  0], rotation: [0, -Math.PI / 2, 0],  label: 'RIGHT'  },
  { name: 'left',   position: [-1,  0,  0], rotation: [0,  Math.PI / 2, 0],  label: 'LEFT'   },
];

// Isometric corner views
const ISO_POSITIONS: [number, number, number][] = [
  [ 1,  1,  1], [-1,  1,  1],
  [ 1,  1, -1], [-1,  1, -1],
];

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
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

function makeTexture(label: string, hovered: boolean, flip: boolean): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 256;
  const ctx = canvas.getContext('2d')!;

  if (flip) {
    ctx.translate(256, 0);
    ctx.scale(-1, 1);
  }

  // Background
  ctx.fillStyle = hovered ? '#1d4ed8' : '#1a2744';
  ctx.fillRect(0, 0, 256, 256);

  // Rounded border
  ctx.strokeStyle = hovered ? '#60a5fa' : '#3b4e6a';
  ctx.lineWidth = 8;
  roundRect(ctx, 6, 6, 244, 244, 18);
  ctx.stroke();

  // Inner subtle separator lines (grid feel)
  if (!hovered) {
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, 128); ctx.lineTo(256, 128);
    ctx.moveTo(128, 0); ctx.lineTo(128, 256);
    ctx.stroke();
  }

  // Text
  ctx.fillStyle = hovered ? '#ffffff' : '#7fa8cc';
  const size = label.length > 5 ? 30 : label.length > 4 ? 36 : 42;
  ctx.font = `bold ${size}px "Segoe UI", "Inter", Arial, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  if (hovered) {
    ctx.shadowColor = 'rgba(147, 197, 253, 0.7)';
    ctx.shadowBlur = 14;
  }
  ctx.fillText(label, 128, 128);

  return new THREE.CanvasTexture(canvas);
}

// Pre-generate textures once (only BACK is mirrored)
const FACE_TEX: Record<string, { n: THREE.CanvasTexture; h: THREE.CanvasTexture }> = {};
FACES.forEach((f) => {
  const flip = f.name === 'back';
  FACE_TEX[f.name] = { n: makeTexture(f.label, false, flip), h: makeTexture(f.label, true, flip) };
});

// ─── 3D cube scene ────────────────────────────────────────────────────────────

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

  const S = 28; // half-size → cube edge = 56 units

  return (
    <group ref={groupRef}>
      {/* Cube body */}
      <mesh>
        <boxGeometry args={[S * 2, S * 2, S * 2]} />
        <meshStandardMaterial color="#080f1e" roughness={0.9} metalness={0.1} />
      </mesh>

      {/* Edge lines */}
      <lineSegments>
        <edgesGeometry args={[new THREE.BoxGeometry(S * 2, S * 2, S * 2)]} />
        <lineBasicMaterial color="#2d4060" />
      </lineSegments>

      {/* Face label planes */}
      {FACES.map((face) => {
        const off = S + 0.15;
        const pos = new THREE.Vector3(...face.position).multiplyScalar(off);
        const rot = new THREE.Euler(...face.rotation);
        const isHov = hovered === face.name;

        return (
          <group key={face.name} position={pos} rotation={rot}>
            <mesh
              onClick={(e) => { e.stopPropagation(); navigate(face.position, face.name); }}
              onPointerOver={(e) => { e.stopPropagation(); setHovered(face.name); document.body.style.cursor = 'pointer'; }}
              onPointerOut={() => { setHovered(null); document.body.style.cursor = 'default'; }}
            >
              <planeGeometry args={[S * 1.88, S * 1.88]} />
              <meshStandardMaterial
                map={isHov ? FACE_TEX[face.name].h : FACE_TEX[face.name].n}
                transparent
                side={THREE.DoubleSide}
              />
            </mesh>
          </group>
        );
      })}

      {/* Corner spheres for isometric views */}
      {ISO_POSITIONS.map((pos, i) => (
        <mesh
          key={`iso-${i}`}
          position={new THREE.Vector3(...pos).multiplyScalar(S + 5)}
          onClick={(e) => { e.stopPropagation(); navigate(pos, `iso${i}`); }}
          onPointerOver={(e) => { e.stopPropagation(); document.body.style.cursor = 'pointer'; }}
          onPointerOut={() => { document.body.style.cursor = 'default'; }}
        >
          <sphereGeometry args={[4, 12, 12]} />
          <meshStandardMaterial color="#2d4060" roughness={0.4} metalness={0.6} />
        </mesh>
      ))}

      {/* Axis arrows from back-bottom-left corner */}
      <AxisArrows S={S} />
    </group>
  );
};

// ─── Axis arrows ──────────────────────────────────────────────────────────────

const AxisArrows: React.FC<{ S: number }> = ({ S }) => {
  const L = S * 1.35;
  const R = 0.55;

  return (
    <group position={[-S, -S, -S]}>
      {/* X – Red */}
      <group rotation={[0, 0, -Math.PI / 2]}>
        <mesh position={[0, L / 2, 0]}>
          <cylinderGeometry args={[R, R, L, 8]} />
          <meshStandardMaterial color="#ef4444" roughness={0.4} depthTest={false} />
        </mesh>
        <mesh position={[0, L + 3.5, 0]}>
          <coneGeometry args={[1.8, 6, 8]} />
          <meshStandardMaterial color="#ef4444" roughness={0.4} depthTest={false} />
        </mesh>
      </group>
      {/* Y – Green */}
      <mesh position={[0, L / 2, 0]}>
        <cylinderGeometry args={[R, R, L, 8]} />
        <meshStandardMaterial color="#22c55e" roughness={0.4} depthTest={false} />
      </mesh>
      <mesh position={[0, L + 3.5, 0]}>
        <coneGeometry args={[1.8, 6, 8]} />
        <meshStandardMaterial color="#22c55e" roughness={0.4} depthTest={false} />
      </mesh>
      {/* Z – Blue */}
      <group rotation={[Math.PI / 2, 0, 0]}>
        <mesh position={[0, L / 2, 0]}>
          <cylinderGeometry args={[R, R, L, 8]} />
          <meshStandardMaterial color="#3b82f6" roughness={0.4} depthTest={false} />
        </mesh>
        <mesh position={[0, L + 3.5, 0]}>
          <coneGeometry args={[1.8, 6, 8]} />
          <meshStandardMaterial color="#3b82f6" roughness={0.4} depthTest={false} />
        </mesh>
      </group>
    </group>
  );
};

// ─── Wrapper ──────────────────────────────────────────────────────────────────

const Viewcube: React.FC<{ size?: number }> = ({ size = 155 }) => {
  const handleHome = useCallback(() => {
    const pos = new THREE.Vector3(0, 0, 200);
    window.dispatchEvent(new CustomEvent('viewcubeNavigate', { detail: { position: pos, name: 'front' } }));
  }, []);

  return (
    <div style={{ position: 'absolute', top: 80, right: 20, zIndex: 100, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5 }}>
      <div style={{ width: size, height: size }}>
        <Canvas
          camera={{ position: [28, 32, 88], fov: 42, near: 0.1, far: 1000 }}
          style={{
            background: 'linear-gradient(145deg, rgba(8,15,30,0.96) 0%, rgba(15,25,50,0.96) 100%)',
            borderRadius: 14,
            border: '1px solid rgba(45,64,96,0.8)',
            boxShadow: '0 8px 32px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.05)',
          }}
        >
          <ambientLight intensity={0.9} />
          <directionalLight position={[60, 90, 60]} intensity={1.6} />
          <directionalLight position={[-40, 20, -40]} intensity={0.35} />
          <CubeScene />
        </Canvas>
      </div>

      {/* Home / reset button */}
      <HomeButton onClick={handleHome} />
    </div>
  );
};

const HomeButton: React.FC<{ onClick: () => void }> = ({ onClick }) => {
  const [hov, setHov] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        background: hov ? 'rgba(30,64,100,0.9)' : 'rgba(10,20,40,0.85)',
        border: `1px solid ${hov ? 'rgba(96,165,250,0.6)' : 'rgba(45,64,96,0.7)'}`,
        borderRadius: 6,
        color: hov ? '#93c5fd' : '#6b8cb0',
        cursor: 'pointer',
        fontSize: 10,
        fontWeight: 600,
        padding: '4px 16px',
        fontFamily: '"Segoe UI", "Inter", system-ui, sans-serif',
        letterSpacing: '0.08em',
        transition: 'all 0.15s ease',
      }}
    >
      HOME
    </button>
  );
};

export default Viewcube;
export { Viewcube };

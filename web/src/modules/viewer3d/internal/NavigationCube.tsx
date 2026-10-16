import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import type { StandardView } from './model3d';
import { CUBE_EDGE_DIRECTIONS, cubeCameraTransform, cubeViewForLocalDirection } from './navigationCubeMath';

export interface NavigationCubeProps {
  onView: (view: StandardView) => void;
  onHome: () => void;
  label: (view: StandardView) => string;
  title: string;
  homeLabel: string;
  /** Normalized vector from the orbit target towards the camera, in CAD coordinates. */
  cameraDirection?: readonly [number, number, number];
  /** Actual camera screen-up vector in CAD coordinates; this retains orbit roll. */
  cameraUp?: readonly [number, number, number];
}

type Vec = readonly [number, number, number];
type Face = { name: StandardView; normal: Vec; position: Vec; rotation: [number, number, number] };
const S = 26;
const FACES: Face[] = [
  { name:'front', normal:[0,0,1], position:[0,0,1], rotation:[0,0,0] },
  { name:'back', normal:[0,0,-1], position:[0,0,-1], rotation:[0,Math.PI,0] },
  { name:'top', normal:[0,1,0], position:[0,1,0], rotation:[-Math.PI/2,0,0] },
  { name:'bottom', normal:[0,-1,0], position:[0,-1,0], rotation:[Math.PI/2,0,0] },
  { name:'right', normal:[1,0,0], position:[1,0,0], rotation:[0,Math.PI/2,0] },
  { name:'left', normal:[-1,0,0], position:[-1,0,0], rotation:[0,-Math.PI/2,0] },
];
const EDGE_SEGMENTS: Array<{ from: Vec; to: Vec; viewDirection: Vec }> = [
  { from:[-S,-S,-S], to:[S,-S,-S], viewDirection:[0,-1,-1] }, { from:[-S,S,-S], to:[S,S,-S], viewDirection:[0,1,-1] },
  { from:[-S,-S,S], to:[S,-S,S], viewDirection:[0,-1,1] }, { from:[-S,S,S], to:[S,S,S], viewDirection:[0,1,1] },
  { from:[-S,-S,-S], to:[-S,S,-S], viewDirection:[-1,0,-1] }, { from:[S,-S,-S], to:[S,S,-S], viewDirection:[1,0,-1] },
  { from:[-S,-S,S], to:[-S,S,S], viewDirection:[-1,0,1] }, { from:[S,-S,S], to:[S,S,S], viewDirection:[1,0,1] },
  { from:[-S,-S,-S], to:[-S,-S,S], viewDirection:[-1,-1,0] }, { from:[S,-S,-S], to:[S,-S,S], viewDirection:[1,-1,0] },
  { from:[-S,S,-S], to:[-S,S,S], viewDirection:[-1,1,0] }, { from:[S,S,-S], to:[S,S,S], viewDirection:[1,1,0] },
];

function makeFaceTexture(text: string, hovered: boolean, flip: boolean): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 256;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Unable to create the view-cube label texture');
  if (flip) { ctx.translate(256, 0); ctx.scale(-1, 1); }
  ctx.fillStyle = hovered ? '#2563eb' : '#c8d8e8';
  ctx.fillRect(0, 0, 256, 256);
  ctx.strokeStyle = hovered ? '#1d4ed8' : '#7a96b0';
  ctx.lineWidth = 6;
  ctx.beginPath(); ctx.moveTo(14,4); ctx.lineTo(242,4); ctx.quadraticCurveTo(252,4,252,14); ctx.lineTo(252,242); ctx.quadraticCurveTo(252,252,242,252); ctx.lineTo(14,252); ctx.quadraticCurveTo(4,252,4,242); ctx.lineTo(4,14); ctx.quadraticCurveTo(4,4,14,4); ctx.stroke();
  ctx.fillStyle = hovered ? '#ffffff' : '#1a2a3a';
  ctx.font = `bold ${text.length > 5 ? 50 : text.length > 4 ? 60 : 72}px "Segoe UI", Arial, sans-serif`;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(text, 128, 128);
  const texture = new THREE.CanvasTexture(canvas);
  return texture;
}

function CubeScene({ cameraDirection, cameraUp, onView, label }: Required<Pick<NavigationCubeProps, 'onView' | 'label'>> & { cameraDirection: Vec; cameraUp: Vec }) {
  const group = useRef<THREE.Group>(null);
  const target = useRef(new THREE.Quaternion());
  const [hovered, setHovered] = useState<StandardView | null>(null);
  const invalidate = useThree((s) => s.invalidate);
  const canvasElement = useThree((s) => s.gl.domElement);
  const upperLabels = Object.fromEntries(FACES.map((face) => [face.name, label(face.name).toUpperCase()])) as Record<StandardView, string>;
  const textureKey = FACES.map((face) => upperLabels[face.name]).join('\u0000');
  const [textures, setTextures] = useState<Record<StandardView, { normal: THREE.CanvasTexture; hover: THREE.CanvasTexture }> | null>(null);
  useEffect(() => {
    const created = Object.fromEntries(FACES.map((face) => [face.name, {
      normal: makeFaceTexture(upperLabels[face.name], false, face.name === 'back'),
      hover: makeFaceTexture(upperLabels[face.name], true, face.name === 'back'),
    }])) as Record<StandardView, { normal: THREE.CanvasTexture; hover: THREE.CanvasTexture }>;
    setTextures(created);
    return () => { for (const texture of Object.values(created)) { texture.normal.dispose(); texture.hover.dispose(); } };
  }, [textureKey]);

  const orientation = useMemo(() => {
    const m = cubeCameraTransform(cameraDirection, cameraUp);
    const matrix = new THREE.Matrix4().set(m[0],m[1],m[2],0, m[3],m[4],m[5],0, m[6],m[7],m[8],0, 0,0,0,1);
    return new THREE.Quaternion().setFromRotationMatrix(matrix);
  }, [cameraDirection[0], cameraDirection[1], cameraDirection[2], cameraUp[0], cameraUp[1], cameraUp[2]]);
  useEffect(() => { target.current.copy(orientation); invalidate(); }, [orientation, invalidate]);
  useFrame(() => {
    const g = group.current;
    if (!g || g.quaternion.angleTo(target.current) < 1e-3) return;
    g.quaternion.slerp(target.current, 0.12);
    invalidate();
  });
  const activate = useCallback((direction: Vec) => onView(cubeViewForLocalDirection(direction)), [onView]);
  const stop = (event: { stopPropagation: () => void }) => event.stopPropagation();
  const pointer = (view: StandardView) => ({
    onPointerOver: (event: { stopPropagation: () => void }) => { event.stopPropagation(); setHovered(view); canvasElement.style.cursor = 'pointer'; },
    onPointerOut: () => { setHovered(null); canvasElement.style.cursor = ''; },
  });
  useEffect(() => () => { canvasElement.style.cursor = ''; }, [canvasElement]);
  const [edgeGeometry, setEdgeGeometry] = useState<THREE.EdgesGeometry | null>(null);
  useEffect(() => {
    const source = new THREE.BoxGeometry(S*2,S*2,S*2);
    const edges = new THREE.EdgesGeometry(source);
    source.dispose();
    setEdgeGeometry(edges);
    return () => edges.dispose();
  }, []);
  const axisLength = S * 2.7;

  return <group ref={group}>
    <mesh onClick={stop}>
      <boxGeometry args={[S*2,S*2,S*2]} />
      <meshStandardMaterial color="#b0c4d8" roughness={0.6} metalness={0} />
    </mesh>
    <lineSegments geometry={edgeGeometry ?? undefined}><lineBasicMaterial color="#4a6070" /></lineSegments>
    {FACES.map((face) => <group key={face.name} position={new THREE.Vector3(...face.position).multiplyScalar(S+0.1)} rotation={face.rotation}>
      <mesh onClick={(event) => { event.stopPropagation(); activate(face.normal); }} {...pointer(face.name)}>
        <planeGeometry args={[S*1.9,S*1.9]} />
        <meshStandardMaterial map={textures ? (hovered === face.name ? textures[face.name].hover : textures[face.name].normal) : null} transparent side={THREE.DoubleSide} />
      </mesh>
    </group>)}
    {EDGE_SEGMENTS.map((edge, index) => {
      const start = new THREE.Vector3(...edge.from), end = new THREE.Vector3(...edge.to);
      const axis = end.clone().sub(start);
      return <mesh key={index} position={start.add(end).multiplyScalar(0.5)} quaternion={new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0), axis.normalize())}
        onClick={(event) => { event.stopPropagation(); activate(edge.viewDirection); }} {...pointer(cubeViewForLocalDirection(edge.viewDirection))}>
        <cylinderGeometry args={[1.5,1.5,52,8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>;
    })}
    <group position={[-S,-S,-S]}>
      <group rotation={[0,0,-Math.PI/2]}><Arrow length={axisLength} color="#e53e3e" /></group>
      <Arrow length={axisLength} color="#38a169" />
      <group rotation={[Math.PI/2,0,0]}><Arrow length={axisLength} color="#3182ce" /></group>
    </group>
  </group>;
}

function Arrow({ length, color }: { length: number; color: string }) {
  return <>
    <mesh position={[0,length/2,0]}><cylinderGeometry args={[0.6,0.6,length,8]} /><meshStandardMaterial color={color} roughness={0.4} depthTest={false} /></mesh>
    <mesh position={[0,length+2.5,0]}><coneGeometry args={[1.8,5,8]} /><meshStandardMaterial color={color} roughness={0.4} depthTest={false} /></mesh>
  </>;
}

function CanvasLifecycle() {
  const gl = useThree((s) => s.gl);
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    const canvas = gl.domElement;
    const lost = (event: Event) => event.preventDefault();
    const restored = () => invalidate();
    canvas.addEventListener('webglcontextlost', lost);
    canvas.addEventListener('webglcontextrestored', restored);
    return () => { canvas.removeEventListener('webglcontextlost', lost); canvas.removeEventListener('webglcontextrestored', restored); };
  }, [gl, invalidate]);
  return null;
}

const NavigationCube: React.FC<NavigationCubeProps> = ({ onView, onHome, label, title, homeLabel, cameraDirection = [1,-1,1], cameraUp = [0,0,1] }) => {
  const [homeHovered, setHomeHovered] = useState(false);
  const faceViews = FACES.map((face) => cubeViewForLocalDirection(face.normal));
  const edgeViews = CUBE_EDGE_DIRECTIONS.map(cubeViewForLocalDirection);
  return <div style={{ position:'relative', width:100, display:'flex', flexDirection:'column', alignItems:'center', gap:4 }}>
    <button type="button" onClick={onHome} onMouseEnter={() => setHomeHovered(true)} onMouseLeave={() => setHomeHovered(false)} title={homeLabel} aria-label={homeLabel}
      style={{ alignSelf:'flex-start', background:homeHovered?'rgba(37,99,235,0.18)':'var(--line-soft)', border:`1px solid ${homeHovered?'rgba(37,99,235,0.5)':'rgba(120,160,190,0.3)'}`, borderRadius:5, cursor:'pointer', padding:'3px 5px', display:'flex', alignItems:'center' }}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={homeHovered?'#60a5fa':'#7090a8'} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M3 12L12 3l9 9" /><path d="M9 21V12h6v9" /><path d="M5 10v11h14V10" />
      </svg>
    </button>
    <div style={{ width:100, height:100, flex:'0 0 100px' }}><Canvas orthographic frameloop="demand" camera={{ position:[0,0,200], zoom:1.05, near:0.1, far:1000 }}
      style={{ width:100, height:100, background:'var(--overlay)', borderRadius:8, border:'1px solid rgba(80,110,140,0.35)', boxShadow:'0 2px 10px rgba(0,0,0,0.45)' }}>
      <ambientLight intensity={1.5} /><directionalLight position={[50,80,60]} intensity={1} /><directionalLight position={[-30,10,-20]} intensity={0.25} />
      <CanvasLifecycle /><CubeScene cameraDirection={cameraDirection} cameraUp={cameraUp} onView={onView} label={label} />
    </Canvas></div>
    <div role="group" aria-label={title} style={{ position:'absolute', width:1, height:1, padding:0, margin:-1, overflow:'hidden', clip:'rect(0,0,0,0)', whiteSpace:'nowrap', border:0 }}>
      {faceViews.map((view, index) => <button key={`face-${index}`} type="button" aria-label={label(view)} onClick={() => onView(view)} />)}
      {edgeViews.map((view, index) => <button key={`edge-${index}`} type="button" aria-label={label(view)} onClick={() => onView(view)} />)}
    </div>
  </div>;
};
export default NavigationCube;

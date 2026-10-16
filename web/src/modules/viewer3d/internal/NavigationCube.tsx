import React, { useMemo } from 'react';
import { Box } from '@mui/material';
import type { StandardView } from './model3d';
import { projectNavigationCube, type CubePoint } from './navigationCubeMath';

export interface NavigationCubeProps {
  onView: (view: StandardView) => void;
  label: (view: StandardView) => string;
  title: string;
  /** Normalized vector from the orbit target towards the camera, in CAD coordinates. */
  cameraDirection?: readonly [number, number, number];
  /** Actual screen-up direction from camera.matrixWorld column 1; preserves camera roll. */
  cameraUp?: readonly [number, number, number];
}

const NavigationCube: React.FC<NavigationCubeProps> = ({ onView, label, title, cameraDirection = [1, -1, 1], cameraUp }) => {
  const geometry = useMemo(() => projectNavigationCube(cameraDirection, cameraUp),
    [cameraDirection[0], cameraDirection[1], cameraDirection[2], cameraUp?.[0], cameraUp?.[1], cameraUp?.[2]]);
  const activate = (view: StandardView) => ({ role: 'button' as const, tabIndex: 0, 'aria-label': label(view), onClick: () => onView(view),
    onKeyDown: (event: React.KeyboardEvent<SVGElement>) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onView(view); } } });
  const points = (value: readonly CubePoint[]) => value.map((p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(' ');
  const hidden = geometry.edges.filter((edge) => !edge.visible);
  const visible = geometry.edges.filter((edge) => edge.visible);
  const edge = (item: typeof geometry.edges[number]) => <g key={item.view}>
    <line x1={item.from[0]} y1={item.from[1]} x2={item.to[0]} y2={item.to[1]} stroke="#58748c" strokeWidth={item.visible ? 1.5 : 0.8}
      strokeDasharray={item.visible ? undefined : '2 2'} opacity={item.visible ? 1 : 0.22} pointerEvents="none" />
    <line className="nav-cube-edge" x1={item.from[0]} y1={item.from[1]} x2={item.to[0]} y2={item.to[1]}
      pointerEvents={item.visible ? 'stroke' : 'none'} {...activate(item.view)}><title>{label(item.view)}</title></line>
  </g>;
  return <Box aria-label={title} sx={{ width: 112, height: 102, flex: '0 0 auto' }}>
    <svg viewBox="0 0 112 102" width="112" height="102" role="group" aria-label={title}>
      <style>{`.nav-cube-face{cursor:pointer;transition:filter 100ms ease}.nav-cube-face:hover,.nav-cube-face:focus{filter:brightness(1.12);outline:none}.nav-cube-edge{cursor:pointer;stroke:transparent;stroke-width:11}.nav-cube-edge:hover,.nav-cube-edge:focus{stroke:#38bdf8;stroke-opacity:.72;outline:none}`}</style>
      <title>{title}</title>
      {/* Rear edges remain keyboard reachable, but are painted first and never intercept the pointer. */}
      {hidden.map(edge)}
      {geometry.faces.map((face, index) => <g key={face.view}>
        <polygon className="nav-cube-face" points={points(face.points)} fill={['#9fb6ca', '#bfd0df', '#dce8f2'][index] ?? '#bfd0df'} stroke="#58748c" strokeWidth="1.2" {...activate(face.view)}><title>{label(face.view)}</title></polygon>
        <text x={face.centre[0]} y={face.centre[1] + 3} textAnchor="middle" fontSize="7.5" fontWeight="700" fill="#172b3c" pointerEvents="none">{label(face.view)}</text>
      </g>)}
      {visible.map(edge)}
    </svg>
  </Box>;
};
export default NavigationCube;

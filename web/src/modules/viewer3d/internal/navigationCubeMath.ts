import type { StandardView } from './model3d';

export type CubeVec3 = readonly [number, number, number];

/** The legacy cube's local +Z front/+Y top basis mapped into the CAD Z-up frame. */
export function cubeLocalToCad([x, y, z]: CubeVec3): [number, number, number] {
  const clean = (v: number) => v === 0 ? 0 : v;
  return [clean(x), clean(-z), clean(y)];
}

/** Row-major rotation taking legacy cube-local points into the fixed CAD camera's screen frame. */
export function cubeCameraTransform(cameraDirection: CubeVec3, cameraUp: CubeVec3): number[] {
  const length = (v: CubeVec3) => Math.hypot(...v);
  const directionLength = length(cameraDirection) || 1;
  const direction = cameraDirection.map((v) => v / directionLength) as [number, number, number];
  const upLength0 = length(cameraUp) || 1;
  const up0 = cameraUp.map((v) => v / upLength0) as [number, number, number];
  let up = up0.map((v, i) => v - direction[i] * (up0[0]*direction[0] + up0[1]*direction[1] + up0[2]*direction[2])) as [number, number, number];
  let upLength = length(up);
  if (upLength < 1e-8) {
    const fallback: [number, number, number] = Math.abs(direction[2]) > 0.95 ? [0,-1,0] : [0,0,1];
    const projection = fallback[0]*direction[0] + fallback[1]*direction[1] + fallback[2]*direction[2];
    up = fallback.map((v,i)=>v-direction[i]*projection) as [number,number,number];
    upLength = length(up);
  }
  up = up.map((v)=>v/(upLength||1)) as [number,number,number];
  const right: [number,number,number] = [up[1]*direction[2]-up[2]*direction[1], up[2]*direction[0]-up[0]*direction[2], up[0]*direction[1]-up[1]*direction[0]];
  const screenUp: [number,number,number] = [direction[1]*right[2]-direction[2]*right[1], direction[2]*right[0]-direction[0]*right[2], direction[0]*right[1]-direction[1]*right[0]];
  return [right[0],right[2],-right[1], screenUp[0],screenUp[2],-screenUp[1], direction[0],direction[2],-direction[1]].map((v) => v === 0 ? 0 : v);
}

const DIRECTIONS: Record<StandardView, [number, number, number]> = {
  front:[0,-1,0], back:[0,1,0], left:[-1,0,0], right:[1,0,0], top:[0,0,1], bottom:[0,0,-1],
  frontTop:[0,-1,1], frontBottom:[0,-1,-1], backTop:[0,1,1], backBottom:[0,1,-1],
  leftTop:[-1,0,1], leftBottom:[-1,0,-1], rightTop:[1,0,1], rightBottom:[1,0,-1],
  frontLeft:[-1,-1,0], frontRight:[1,-1,0], backLeft:[-1,1,0], backRight:[1,1,0],
};

/** Resolve an oriented cube face/edge normal pair to the nearest supported camera view. */
export function cubeViewForLocalDirection(direction: CubeVec3): StandardView {
  const cad = cubeLocalToCad(direction);
  let best: StandardView = 'front';
  let score = -Infinity;
  for (const [view, candidate] of Object.entries(DIRECTIONS) as Array<[StandardView, [number, number, number]]>) {
    const length = Math.hypot(...candidate);
    const next = (cad[0] * candidate[0] + cad[1] * candidate[1] + cad[2] * candidate[2]) / length;
    if (next > score) { best = view; score = next; }
  }
  return best;
}

export const CUBE_FACE_VIEWS = ['front', 'back', 'top', 'bottom', 'right', 'left'] as const;
export const CUBE_EDGE_DIRECTIONS: readonly CubeVec3[] = [
  [-1,-1,0], [1,-1,0], [-1,1,0], [1,1,0],
  [-1,0,-1], [1,0,-1], [-1,0,1], [1,0,1],
  [0,-1,-1], [0,1,-1], [0,-1,1], [0,1,1],
];

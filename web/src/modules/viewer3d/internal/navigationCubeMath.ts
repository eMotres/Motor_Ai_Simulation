import type { StandardView, Vec3 } from './model3d';

export type CubePoint = readonly [number, number];
type Vertex = readonly [number, number, number];
export interface ProjectedCubeFace { view: StandardView; points: CubePoint[]; centre: CubePoint; depth: number }
export interface ProjectedCubeEdge { view: StandardView; from: CubePoint; to: CubePoint; visible: boolean }

const VERTICES: Vertex[] = [[-1,-1,-1],[1,-1,-1],[-1,1,-1],[1,1,-1],[-1,-1,1],[1,-1,1],[-1,1,1],[1,1,1]];
const FACES: Array<{ view: StandardView; normal: Vertex; vertices: number[] }> = [
  {view:'front',normal:[0,-1,0],vertices:[0,1,5,4]},{view:'back',normal:[0,1,0],vertices:[2,6,7,3]},
  {view:'left',normal:[-1,0,0],vertices:[0,4,6,2]},{view:'right',normal:[1,0,0],vertices:[1,3,7,5]},
  {view:'top',normal:[0,0,1],vertices:[4,5,7,6]},{view:'bottom',normal:[0,0,-1],vertices:[0,2,3,1]},
];
const EDGES: Array<{ view: StandardView; vertices: readonly [number,number]; normals: readonly [Vertex,Vertex] }> = [
  {view:'frontBottom',vertices:[0,1],normals:[[0,-1,0],[0,0,-1]]},{view:'backBottom',vertices:[2,3],normals:[[0,1,0],[0,0,-1]]},
  {view:'frontTop',vertices:[4,5],normals:[[0,-1,0],[0,0,1]]},{view:'backTop',vertices:[6,7],normals:[[0,1,0],[0,0,1]]},
  {view:'leftBottom',vertices:[0,2],normals:[[-1,0,0],[0,0,-1]]},{view:'rightBottom',vertices:[1,3],normals:[[1,0,0],[0,0,-1]]},
  {view:'leftTop',vertices:[4,6],normals:[[-1,0,0],[0,0,1]]},{view:'rightTop',vertices:[5,7],normals:[[1,0,0],[0,0,1]]},
  {view:'frontLeft',vertices:[0,4],normals:[[0,-1,0],[-1,0,0]]},{view:'frontRight',vertices:[1,5],normals:[[0,-1,0],[1,0,0]]},
  {view:'backLeft',vertices:[2,6],normals:[[0,1,0],[-1,0,0]]},{view:'backRight',vertices:[3,7],normals:[[0,1,0],[1,0,0]]},
];
const dot=(a:readonly number[],b:readonly number[])=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
const cross=(a:readonly number[],b:readonly number[]):Vec3=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit=(v:readonly number[],fallback:Vec3):Vec3=>{const n=Math.hypot(v[0],v[1],v[2]);return n>1e-7?[v[0]/n,v[1]/n,v[2]/n]:fallback;};
const mean=(p:readonly CubePoint[]):CubePoint=>[p.reduce((s,v)=>s+v[0],0)/p.length,p.reduce((s,v)=>s+v[1],0)/p.length];

/** Project the CAD Z-up cube using the camera's actual screen-up vector when supplied. */
export function projectNavigationCube(cameraDirection: readonly number[], cameraUp?: readonly number[]): { faces: ProjectedCubeFace[]; edges: ProjectedCubeEdge[] } {
  const direction=unit(cameraDirection,[1,-1,1]);
  const fallback:Vec3=Math.abs(direction[2])>0.96?[0,-1,0]:[0,0,1];
  const reference=unit(cameraUp ?? fallback,fallback);
  const right=unit(cross(reference,direction),[1,0,0]);
  const up=unit(cross(direction,right),[0,0,1]);
  const projected=VERTICES.map<CubePoint>(v=>[56+dot(v,right)*27,51-dot(v,up)*27]);
  // Tangent faces have zero projected area and must not intercept the front face or its edges.
  const visible=(normal:Vertex)=>dot(normal,direction)>1e-6;
  const faces=FACES.filter(f=>visible(f.normal)).map<ProjectedCubeFace>(f=>{const points=f.vertices.map(i=>projected[i]);return{view:f.view,points,centre:mean(points),depth:dot(f.normal,direction)};}).sort((a,b)=>a.depth-b.depth);
  const edges=EDGES.map<ProjectedCubeEdge>(e=>({view:e.view,from:projected[e.vertices[0]],to:projected[e.vertices[1]],visible:e.normals.some(visible)}));
  return {faces,edges};
}

import { Box3, BufferGeometry, Float32BufferAttribute, Group, Mesh, MeshStandardMaterial, Vector3, type Object3D } from 'three';
import type { Box, PartGroup, Vec3 } from './model3d';

export interface ActiveMeshPart {
  key: string;
  family: string;
  colour: string;
  vertices: number[] | number[][];
  faces?: number[][];
  opacity?: number;
}
export interface ActiveScene { root: Object3D; groups: PartGroup[]; box: Box; key: string }

function geometryOf(part: ActiveMeshPart): BufferGeometry {
  const vertices = Array.isArray(part.vertices[0]) ? (part.vertices as number[][]).flat() : part.vertices as number[];
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(vertices, 3));
  if (part.faces?.length) {
    geometry.setIndex(part.faces.flatMap((face) => [face[0], face[1], face[2]]));
    const flat = geometry.toNonIndexed(); geometry.dispose(); flat.computeVertexNormals(); return flat;
  }
  geometry.computeVertexNormals(); return geometry;
}
function radialDirection(key: string, center: Vector3): Vec3 {
  const r = new Vector3(center.x, center.y, 0);
  if (r.lengthSq() < 1e-8) {
    let h = 0; for (const c of key) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    const a = (h % 360) * Math.PI / 180; r.set(Math.cos(a), Math.sin(a), 0);
  }
  r.normalize(); return [r.x, r.y, r.z];
}
export function buildActiveScene(parts: readonly ActiveMeshPart[], key: string): ActiveScene | null {
  const root = new Group(); root.name = 'ActiveMotor';
  const groups: PartGroup[] = [];
  for (const part of parts) {
    if (!part.vertices.length) continue;
    const name = part.key.startsWith('coil_') ? `coil:${Number(part.key.slice(5))}`
      : part.key.startsWith('magnet_') ? `magnet:${Number(part.key.slice(7))}` : part.key;
    const node = new Group(); node.name = name;
    const opacity = part.opacity ?? 1;
    const mesh = new Mesh(geometryOf(part), new MeshStandardMaterial({ color: part.colour, roughness: 0.62, metalness: 0.12,
      transparent: opacity < 1, opacity, depthWrite: opacity >= 1 }));
    mesh.name = part.key; node.add(mesh); root.add(node);
    const box = new Box3().setFromObject(node);
    groups.push({ name, family: part.family, explode: 0.35, explodeDirection: radialDirection(part.key, box.getCenter(new Vector3())), children: [part.key], colour: part.colour });
  }
  const box = new Box3().setFromObject(root);
  if (box.isEmpty()) return null;
  const bounds: Box = { min:[box.min.x,box.min.y,box.min.z], max:[box.max.x,box.max.y,box.max.z] };
  return { root, groups, box: bounds, key };
}

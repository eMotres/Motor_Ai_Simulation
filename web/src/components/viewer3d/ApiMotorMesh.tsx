import React, { useMemo, useEffect, useState, useRef } from 'react';
import * as THREE from 'three';
import { useMotorStore, useUIStore, useBuildTimingStore } from '../../stores/motorStore';

const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

interface ComponentMeshData {
  vertices: number[] | number[][];
  faces?: number[][];
}

type AllMeshData = Record<string, ComponentMeshData>;

interface MaterialProps {
  color: string;
  metalness: number;
  roughness: number;
}

// ─── Shared fetch hook ───────────────────────────────────────────────────────
// All mesh components call this — only ONE network request is made per geometry
// change (subsequent calls within the same render cycle reuse the in-flight promise).
// Exported so ComponentTree can read coil/magnet counts without extra fetches.

const meshCache = new Map<string, AllMeshData>();
let inflightRequest: Promise<AllMeshData> | null = null;
let inflightGeometryKey = '';

export function useMotorMesh() {
  const { geometry, connectedToApi, setGeometryUpdating } = useMotorStore();
  const { setMesh3dTime } = useBuildTimingStore();
  const [data, setData] = useState<AllMeshData | null>(null);

  const geometryKey = JSON.stringify(geometry);

  useEffect(() => {
    if (!connectedToApi) {
      setData(null);
      return;
    }

    const cached = meshCache.get(geometryKey);
    if (cached) {
      setData(cached);
      setGeometryUpdating(false);
      return;
    }

    if (!inflightRequest || inflightGeometryKey !== geometryKey) {
      inflightGeometryKey = geometryKey;
      const t0 = performance.now();
      inflightRequest = fetch(`${API_BASE_URL}/api/geometry/mesh`)
        .then(res => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json() as Promise<AllMeshData>;
        })
        .then(result => {
          setMesh3dTime(parseFloat(((performance.now() - t0) / 1000).toFixed(2)));
          meshCache.set(geometryKey, result);
          if (meshCache.size > 3) {
            meshCache.delete(meshCache.keys().next().value!);
          }
          return result;
        })
        .catch(err => {
          console.error('Failed to fetch motor mesh:', err);
          return null as unknown as AllMeshData;
        })
        .finally(() => {
          inflightRequest = null;
        });
    }

    inflightRequest.then(result => {
      if (result) {
        setData(result);
        setGeometryUpdating(false);
      }
    });
  }, [connectedToApi, geometryKey]);

  return data;
}

// ─── Geometry helpers ────────────────────────────────────────────────────────

/**
 * Build a Three.js BufferGeometry from CadQuery STL vertices+faces.
 * Uses non-indexed (per-triangle) vertices so each face gets its own
 * vertex normals — this eliminates spike artifacts on sharp CAD edges.
 */
function buildGeometry(data: ComponentMeshData): THREE.BufferGeometry {
  const verts = data.vertices;
  let flatPos: number[];
  if (verts.length > 0 && Array.isArray(verts[0])) {
    flatPos = (verts as number[][]).flat();
  } else {
    flatPos = verts as number[];
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(flatPos, 3));

  if (data.faces && data.faces.length > 0) {
    const idx: number[] = [];
    for (const face of data.faces) idx.push(face[0], face[1], face[2]);
    geo.setIndex(idx);
    // Non-indexed = each triangle owns its vertices → flat CAD normals, no spikes
    const ni = geo.toNonIndexed();
    ni.computeVertexNormals();
    return ni;
  }

  geo.computeVertexNormals();
  return geo;
}

// ─── API mesh components ─────────────────────────────────────────────────────

export const ApiStatorMesh: React.FC<{ materialProps?: MaterialProps; visible?: boolean }> = ({ materialProps, visible = true }) => {
  const { envIntensity } = useUIStore();
  const meshData = useMotorMesh();

  const geo = useMemo(() => {
    if (meshData?.stator_core) return buildGeometry(meshData.stator_core);
    return null;
  }, [meshData]);

  if (!geo) return null;

  return (
    <mesh geometry={geo} castShadow receiveShadow visible={visible}>
      <meshStandardMaterial
        color={materialProps?.color || '#7f8c8d'}
        metalness={materialProps?.metalness ?? 0.9}
        roughness={materialProps?.roughness ?? 0.3}
        envMapIntensity={envIntensity * 1.5}
      />
    </mesh>
  );
};

export const ApiRotorMesh: React.FC<{ materialProps?: MaterialProps; visible?: boolean }> = ({ materialProps, visible = true }) => {
  const { envIntensity } = useUIStore();
  const meshData = useMotorMesh();

  const geo = useMemo(() => {
    if (meshData?.rotor_core) return buildGeometry(meshData.rotor_core);
    return null;
  }, [meshData]);

  if (!geo) return null;

  return (
    <mesh geometry={geo} castShadow receiveShadow visible={visible}>
      <meshStandardMaterial
        color={materialProps?.color || '#7f8c8d'}
        metalness={materialProps?.metalness ?? 0.9}
        roughness={materialProps?.roughness ?? 0.3}
        envMapIntensity={envIntensity * 1.5}
      />
    </mesh>
  );
};

export const ApiShaftMesh: React.FC<{ visible?: boolean }> = ({ visible = true }) => {
  const { envIntensity } = useUIStore();
  const meshData = useMotorMesh();

  const geo = useMemo(() => {
    if (meshData?.shaft) return buildGeometry(meshData.shaft);
    return null;
  }, [meshData]);

  if (!geo) return null;

  return (
    <mesh geometry={geo} castShadow receiveShadow visible={visible}>
      <meshStandardMaterial
        color="#505050" metalness={0.95} roughness={0.25}
        envMapIntensity={envIntensity * 1.5}
      />
    </mesh>
  );
};

export const ApiMagnetsMesh: React.FC<{ visible?: boolean }> = ({ visible = true }) => {
  const { envIntensity } = useUIStore();
  const { magnetVisibility } = useUIStore();
  const meshData = useMotorMesh();

  const magnetEntries = useMemo(() => {
    if (!meshData) return null;
    const keys = Object.keys(meshData)
      .filter(k => k.startsWith('magnet_'))
      .sort((a, b) => parseInt(a.split('_')[1]) - parseInt(b.split('_')[1]));
    if (keys.length === 0) return null;
    return keys.map((key, i) => ({
      geometry: buildGeometry(meshData[key]),
      poleIndex: i,
      direction: i % 2 === 0 ? 'outward' : 'inward',
    }));
  }, [meshData]);

  if (!magnetEntries) return null;

  return (
    <group visible={visible}>
      {magnetEntries.map(({ geometry: geo, poleIndex, direction }) => (
        <mesh key={poleIndex} geometry={geo} castShadow receiveShadow
          visible={magnetVisibility[poleIndex] ?? true}>
          <meshStandardMaterial
            color={direction === 'outward' ? '#ef4444' : '#3b82f6'}
            metalness={0.7} roughness={0.5}
            envMapIntensity={envIntensity * 1.5}
            side={THREE.DoubleSide}
          />
        </mesh>
      ))}
    </group>
  );
};

export const ApiCoilsMesh: React.FC<{ materialProps?: MaterialProps; visible?: boolean }> = ({ materialProps, visible = true }) => {
  const { envIntensity } = useUIStore();
  const { coilVisibility } = useUIStore();
  const meshData = useMotorMesh();

  const phaseColors = ['#b87333', '#c2410c', '#a16207'];

  const coilEntries = useMemo(() => {
    if (!meshData) return [];
    const keys = Object.keys(meshData)
      .filter(k => k.startsWith('coil_'))
      .sort((a, b) => parseInt(a.split('_')[1]) - parseInt(b.split('_')[1]));
    return keys.map((key, i) => ({
      geometry: buildGeometry(meshData[key]),
      slotIndex: i,
      phase: i % 3,
    }));
  }, [meshData]);

  return (
    <group visible={visible}>
      {coilEntries.map(({ geometry: geo, slotIndex, phase }) => (
        <mesh key={`coil-${slotIndex}`} geometry={geo} castShadow receiveShadow
          visible={coilVisibility[slotIndex] ?? true}>
          <meshStandardMaterial
            color={materialProps?.color || phaseColors[phase]}
            metalness={materialProps?.metalness ?? 0.6}
            roughness={materialProps?.roughness ?? 0.5}
            envMapIntensity={envIntensity * 1.5}
            side={THREE.DoubleSide}
          />
        </mesh>
      ))}
    </group>
  );
};

// ─── Extruded mesh hook (2D + server-side extrusion, no CadQuery) ────────────

const meshExtrudedCache = new Map<string, AllMeshData>();
let inflightRequestExt: Promise<AllMeshData> | null = null;
let inflightGeometryKeyExt = '';

export function useMotorMeshExtruded() {
  const { geometry, connectedToApi, setGeometryUpdating } = useMotorStore();
  const { setMeshExtTime, setMesh2dTime } = useBuildTimingStore();
  const [data, setData] = useState<AllMeshData | null>(null);
  const geometryKey = JSON.stringify(geometry);

  useEffect(() => {
    if (!connectedToApi) { setData(null); return; }
    const cached = meshExtrudedCache.get(geometryKey);
    if (cached) { setData(cached); setGeometryUpdating(false); return; }

    if (!inflightRequestExt || inflightGeometryKeyExt !== geometryKey) {
      inflightGeometryKeyExt = geometryKey;
      inflightRequestExt = fetch(`${API_BASE_URL}/api/geometry/mesh_extruded`)
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<AllMeshData>; })
        .then(result => {
          // extract timing then strip from mesh data
          const timing = (result as any)._timing;
          if (timing?.extruded_s != null) setMeshExtTime(timing.extruded_s);
          if (timing?.mesh2d_s   != null) setMesh2dTime(timing.mesh2d_s);
          const { _timing: _, ...meshOnly } = result as any;
          meshExtrudedCache.set(geometryKey, meshOnly);
          return meshOnly as AllMeshData;
        })
        .catch(err => { console.error('Failed to fetch extruded mesh:', err); return null as unknown as AllMeshData; })
        .finally(() => { inflightRequestExt = null; });
    }
    inflightRequestExt.then(result => {
      if (result) { setData(result); setGeometryUpdating(false); }
    });
  }, [connectedToApi, geometryKey]);

  return data;
}

/** 3D extruded motor — same colours as the 3D CadQuery view, rendered solid. */
export const ApiMotorExtruded: React.FC = () => {
  const { componentVisibility, magnetVisibility, coilVisibility, metalness, roughness, envIntensity, selectedPart, setSelectedPart } = useUIStore();
  const meshData = useMotorMeshExtruded();

  const geometries = useMemo(() => {
    if (!meshData) return null;
    const out: Record<string, THREE.BufferGeometry> = {};
    for (const [key, data] of Object.entries(meshData)) {
      if (!(data as any).vertices) continue;
      out[key] = buildGeometry(data);
    }
    return out;
  }, [meshData]);

  if (!geometries) return null;

  const sel = (part: string) => selectedPart === part;
  const click = (part: string) => (e: any) => { e.stopPropagation(); setSelectedPart(sel(part) ? null : part as any); };
  const emissive = (part: string, color: string) => sel(part) ? color : '#000000';
  const emissiveIntensity = (part: string) => sel(part) ? 0.5 : 0;

  return (
    <group>
      {geometries.shaft && componentVisibility.shaft && (
        <mesh geometry={geometries.shaft} onClick={click('shaft')}>
          <meshStandardMaterial color="#505050" metalness={metalness} roughness={roughness} envMapIntensity={envIntensity * 1.5} side={THREE.DoubleSide}
            emissive={emissive('shaft', '#64748b')} emissiveIntensity={emissiveIntensity('shaft')} />
        </mesh>
      )}
      {geometries.rotor_core && componentVisibility.rotor && (
        <mesh geometry={geometries.rotor_core} onClick={click('rotor')}>
          <meshStandardMaterial color="#7f8c8d" metalness={metalness} roughness={roughness} envMapIntensity={envIntensity * 1.5} side={THREE.DoubleSide}
            emissive={emissive('rotor', '#3b82f6')} emissiveIntensity={emissiveIntensity('rotor')} />
        </mesh>
      )}
      {geometries.stator_core && componentVisibility.stator && (
        <mesh geometry={geometries.stator_core} onClick={click('stator')}>
          <meshStandardMaterial color="#7f8c8d" metalness={metalness} roughness={roughness} envMapIntensity={envIntensity * 1.5} side={THREE.DoubleSide}
            emissive={emissive('stator', '#3b82f6')} emissiveIntensity={emissiveIntensity('stator')} />
        </mesh>
      )}
      {componentVisibility.magnets && Object.entries(geometries)
        .filter(([k]) => k.startsWith('magnet_'))
        .sort(([a], [b]) => parseInt(a.split('_')[1]) - parseInt(b.split('_')[1]))
        .map(([key, geo]) => {
          const idx = parseInt(key.split('_')[1]);
          return (
            <mesh key={key} geometry={geo} visible={magnetVisibility[idx] ?? true} onClick={click('magnets')}>
              <meshStandardMaterial color={idx % 2 === 0 ? '#ef4444' : '#3b82f6'} metalness={metalness * 0.7} roughness={roughness} envMapIntensity={envIntensity * 1.5} side={THREE.DoubleSide}
                emissive={emissive('magnets', '#ef4444')} emissiveIntensity={emissiveIntensity('magnets')} />
            </mesh>
          );
        })
      }
      {componentVisibility.coils && Object.entries(geometries)
        .filter(([k]) => k.startsWith('coil_'))
        .sort(([a], [b]) => parseInt(a.split('_')[1]) - parseInt(b.split('_')[1]))
        .map(([key, geo]) => {
          const idx = parseInt(key.split('_')[1]);
          return (
            <mesh key={key} geometry={geo} visible={coilVisibility[idx] ?? true} onClick={click('coils')}>
              <meshStandardMaterial color="#b87333" metalness={metalness * 0.6} roughness={roughness} envMapIntensity={envIntensity * 1.5} side={THREE.DoubleSide}
                emissive={emissive('coils', '#f59e0b')} emissiveIntensity={emissiveIntensity('coils')} />
            </mesh>
          );
        })
      }
      {/* Slot liner (Nomex/ceramic) — green, between coils and iron */}
      {geometries.slot_insulation && componentVisibility.slot_insulation && (
        <mesh geometry={geometries.slot_insulation} onClick={click('slot_insulation')}>
          <meshStandardMaterial color="#3fae5a" metalness={0} roughness={0.9} envMapIntensity={envIntensity}
            side={THREE.DoubleSide}
            emissive={emissive('slot_insulation', '#3fae5a')} emissiveIntensity={emissiveIntensity('slot_insulation')} />
        </mesh>
      )}
      {/* Wire enamel (polyimide) — orange, wraps each conductor */}
      {geometries.wire_insulation && componentVisibility.wire_insulation && (
        <mesh geometry={geometries.wire_insulation} onClick={click('wire_insulation')}>
          <meshStandardMaterial color="#d98a3a" metalness={0} roughness={0.85} envMapIntensity={envIntensity}
            side={THREE.DoubleSide}
            emissive={emissive('wire_insulation', '#d98a3a')} emissiveIntensity={emissiveIntensity('wire_insulation')} />
        </mesh>
      )}
      {/* Sliding-band air rings — translucent so the user can see them as distinct domains */}
      {geometries.in_band && componentVisibility.in_band && (
        <mesh geometry={geometries.in_band} onClick={click('in_band')}>
          <meshStandardMaterial color="#22c55e" transparent opacity={0.32}
            metalness={0} roughness={1} envMapIntensity={envIntensity * 0.5}
            side={THREE.DoubleSide}
            emissive={emissive('in_band', '#22c55e')} emissiveIntensity={emissiveIntensity('in_band')} />
        </mesh>
      )}
      {geometries.out_band && componentVisibility.out_band && (
        <mesh geometry={geometries.out_band} onClick={click('out_band')}>
          <meshStandardMaterial color="#a855f7" transparent opacity={0.32}
            metalness={0} roughness={1} envMapIntensity={envIntensity * 0.5}
            side={THREE.DoubleSide}
            emissive={emissive('out_band', '#a855f7')} emissiveIntensity={emissiveIntensity('out_band')} />
        </mesh>
      )}
    </group>
  );
};

// ─── 2D flat mesh hook + component ───────────────────────────────────────────

const mesh2dCache = new Map<string, AllMeshData>();
let inflightRequest2d: Promise<AllMeshData> | null = null;
let inflightGeometryKey2d = '';

export function useMotorMesh2d() {
  const { geometry, connectedToApi } = useMotorStore();
  const [data, setData] = useState<AllMeshData | null>(null);
  const geometryKey = JSON.stringify(geometry);

  useEffect(() => {
    if (!connectedToApi) { setData(null); return; }
    const cached = mesh2dCache.get(geometryKey);
    if (cached) { setData(cached); return; }

    if (!inflightRequest2d || inflightGeometryKey2d !== geometryKey) {
      inflightGeometryKey2d = geometryKey;
      inflightRequest2d = fetch(`${API_BASE_URL}/api/geometry/mesh2d`)
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<AllMeshData>; })
        .then(result => { mesh2dCache.set(geometryKey, result); return result; })
        .catch(err => { console.error('Failed to fetch 2d mesh:', err); return null as unknown as AllMeshData; })
        .finally(() => { inflightRequest2d = null; });
    }
    inflightRequest2d.then(result => { if (result) setData(result); });
  }, [connectedToApi, geometryKey]);

  return data;
}

// Colors per component type — kept in sync with 3D material defaults above
// (shaft=#505050, stator/rotor fallback=#7f8c8d, magnets/coils identical)
const COLORS_2D: Record<string, string> = {
  shaft:       '#505050',  // matches ApiShaftMesh hardcoded color
  rotor_core:  '#7f8c8d',  // matches ApiRotorMesh fallback
  stator_core: '#7f8c8d',  // matches ApiStatorMesh fallback
};
const getMagnetColor2d = (index: number) => index % 2 === 0 ? '#ef4444' : '#3b82f6';
const getCoilColor2d   = (index: number) => ['#b87333', '#c2410c', '#a16207'][index % 3];

export const ApiMotor2dFlat: React.FC = () => {
  const { componentVisibility, magnetVisibility, coilVisibility, selectedPart, setSelectedPart } = useUIStore();
  const meshData = useMotorMesh2d();

  const geometries = useMemo(() => {
    if (!meshData) return null;
    const out: Record<string, THREE.BufferGeometry> = {};
    for (const [key, data] of Object.entries(meshData)) {
      out[key] = buildGeometry(data);
    }
    return out;
  }, [meshData]);

  if (!geometries) return null;

  const sel = (part: string) => selectedPart === part;
  const click = (part: string) => (e: any) => { e.stopPropagation(); setSelectedPart(sel(part) ? null : part as any); };
  const emissive = (part: string, color: string) => sel(part) ? color : '#000000';
  const eI = (part: string) => sel(part) ? 0.6 : 0;

  return (
    <group>
      {/* shaft */}
      {geometries.shaft && componentVisibility.shaft && (
        <mesh geometry={geometries.shaft} onClick={click('shaft')}>
          <meshStandardMaterial color={COLORS_2D.shaft} metalness={0} roughness={1} side={THREE.DoubleSide}
            emissive={emissive('shaft', '#64748b')} emissiveIntensity={eI('shaft')} />
        </mesh>
      )}

      {/* rotor_core */}
      {geometries.rotor_core && componentVisibility.rotor && (
        <mesh geometry={geometries.rotor_core} onClick={click('rotor')}>
          <meshStandardMaterial color={COLORS_2D.rotor_core} metalness={0} roughness={1} side={THREE.DoubleSide}
            emissive={emissive('rotor', '#3b82f6')} emissiveIntensity={eI('rotor')} />
        </mesh>
      )}

      {/* magnets */}
      {componentVisibility.magnets && Object.entries(geometries)
        .filter(([k]) => k.startsWith('magnet_'))
        .sort(([a], [b]) => parseInt(a.split('_')[1]) - parseInt(b.split('_')[1]))
        .map(([key, geo]) => {
          const idx = parseInt(key.split('_')[1]);
          return (
            <mesh key={key} geometry={geo} visible={magnetVisibility[idx] ?? true} onClick={click('magnets')}>
              <meshStandardMaterial color={getMagnetColor2d(idx)} metalness={0} roughness={1} side={THREE.DoubleSide}
                emissive={emissive('magnets', '#ef4444')} emissiveIntensity={eI('magnets')} />
            </mesh>
          );
        })
      }

      {/* stator_core */}
      {geometries.stator_core && componentVisibility.stator && (
        <mesh geometry={geometries.stator_core} onClick={click('stator')}>
          <meshStandardMaterial color={COLORS_2D.stator_core} metalness={0} roughness={1} side={THREE.DoubleSide}
            emissive={emissive('stator', '#3b82f6')} emissiveIntensity={eI('stator')} />
        </mesh>
      )}

      {/* coils */}
      {componentVisibility.coils && Object.entries(geometries)
        .filter(([k]) => k.startsWith('coil_'))
        .sort(([a], [b]) => parseInt(a.split('_')[1]) - parseInt(b.split('_')[1]))
        .map(([key, geo]) => {
          const idx = parseInt(key.split('_')[1]);
          return (
            <mesh key={key} geometry={geo} visible={coilVisibility[idx] ?? true} onClick={click('coils')}>
              <meshStandardMaterial color={getCoilColor2d(idx)} metalness={0} roughness={1} side={THREE.DoubleSide}
                emissive={emissive('coils', '#f59e0b')} emissiveIntensity={eI('coils')} />
            </mesh>
          );
        })
      }

      {/* slot liner (green) + wire enamel (orange) */}
      {geometries.slot_insulation && componentVisibility.slot_insulation && (
        <mesh geometry={geometries.slot_insulation} onClick={click('slot_insulation')}>
          <meshStandardMaterial color="#3fae5a" metalness={0} roughness={1} side={THREE.DoubleSide}
            emissive={emissive('slot_insulation', '#3fae5a')} emissiveIntensity={eI('slot_insulation')} />
        </mesh>
      )}
      {geometries.wire_insulation && componentVisibility.wire_insulation && (
        <mesh geometry={geometries.wire_insulation} onClick={click('wire_insulation')}>
          <meshStandardMaterial color="#d98a3a" metalness={0} roughness={1} side={THREE.DoubleSide}
            emissive={emissive('wire_insulation', '#d98a3a')} emissiveIntensity={eI('wire_insulation')} />
        </mesh>
      )}
      {/* Sliding-band: in_band (green) and out_band (purple) — translucent overlays */}
      {geometries.in_band && componentVisibility.in_band && (
        <mesh geometry={geometries.in_band} onClick={click('in_band')}>
          <meshStandardMaterial color="#22c55e" transparent opacity={0.35} side={THREE.DoubleSide}
            emissive={emissive('in_band', '#22c55e')} emissiveIntensity={eI('in_band')} />
        </mesh>
      )}
      {geometries.out_band && componentVisibility.out_band && (
        <mesh geometry={geometries.out_band} onClick={click('out_band')}>
          <meshStandardMaterial color="#a855f7" transparent opacity={0.35} side={THREE.DoubleSide}
            emissive={emissive('out_band', '#a855f7')} emissiveIntensity={eI('out_band')} />
        </mesh>
      )}
    </group>
  );
};

// ─── Local fallback geometry (used when API is unavailable) ──────────────────

function createLocalStatorGeometry(geometry: any): THREE.BufferGeometry {
  const outerR = geometry.stator_outer_radius || (geometry.stator_diameter ? geometry.stator_diameter / 2 : 100);
  const innerR = geometry.stator_inner_radius || (outerR - (geometry.core_thickness || 3.8) - (geometry.slot_height || 16));
  const numSlots = geometry.num_slots || 36;
  const slotHeight = geometry.slot_height || 16;
  const slotWidth = geometry.slot_width || (geometry.tooth_width ? geometry.tooth_width * 0.6 : 4.5);
  const statorWidth = geometry.motor_length || 30;

  const shape = new THREE.Shape();
  shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);

  const innerHole = new THREE.Path();
  const slotAngle = (2 * Math.PI) / numSlots;
  const halfSlotWidthRad = (slotWidth / innerR) / 2;

  for (let i = 0; i < numSlots; i++) {
    const angle = i * slotAngle;
    const slotEnd = angle + halfSlotWidthRad;
    const nextSlotStart = (i + 1) * slotAngle - halfSlotWidthRad;

    if (i === 0) innerHole.moveTo(Math.cos(slotEnd) * innerR, Math.sin(slotEnd) * innerR);

    innerHole.absarc(0, 0, innerR, slotEnd, nextSlotStart, false);
    const slotR = innerR + slotHeight;
    innerHole.lineTo(Math.cos(nextSlotStart) * slotR, Math.sin(nextSlotStart) * slotR);
    innerHole.lineTo(Math.cos(slotEnd) * slotR, Math.sin(slotEnd) * slotR);
    innerHole.lineTo(Math.cos(slotEnd) * innerR, Math.sin(slotEnd) * innerR);
  }

  shape.holes.push(innerHole);
  const extruded = new THREE.ExtrudeGeometry(shape, { depth: statorWidth, bevelEnabled: false });
  extruded.translate(0, 0, -statorWidth / 2);
  return extruded;
}

function createLocalRotorGeometry(geometry: any): THREE.BufferGeometry {
  const outerR = geometry.rotor_outer_radius || 79.55;
  const innerR = geometry.rotor_inner_radius || 64.55;
  const statorWidth = geometry.motor_length || 30;

  const shape = new THREE.Shape();
  shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);
  const hole = new THREE.Path();
  hole.absarc(0, 0, innerR, 0, Math.PI * 2, true);
  shape.holes.push(hole);

  const extruded = new THREE.ExtrudeGeometry(shape, { depth: statorWidth, bevelEnabled: false });
  extruded.translate(0, 0, -statorWidth / 2);
  return extruded;
}

function createLocalShaftGeometry(geometry: any): THREE.BufferGeometry {
  const outerR = geometry.rotor_inner_radius || 64.55;
  const innerR = outerR - (geometry.shaft_height || 3);
  const statorWidth = geometry.motor_length || 30;

  const shape = new THREE.Shape();
  shape.absarc(0, 0, outerR, 0, Math.PI * 2, false);
  const hole = new THREE.Path();
  hole.absarc(0, 0, innerR, 0, Math.PI * 2, true);
  shape.holes.push(hole);

  const extruded = new THREE.ExtrudeGeometry(shape, { depth: statorWidth, bevelEnabled: false });
  extruded.translate(0, 0, -statorWidth / 2);
  return extruded;
}

function createLocalMagnetsGeometry(geometry: any): { geometry: THREE.BufferGeometry; poleIndex: number; direction: string }[] {
  const rotorOuterR = geometry.rotor_outer_radius || 79.55;
  const rotorInnerR = geometry.rotor_inner_radius || 64.55;
  const rotorHouseH = geometry.rotor_house_height || 1.2;
  const numPoles = geometry.num_poles || 42;
  const statorWidth = geometry.motor_length || 30;
  const magFillDown = geometry.magnet_fill_down || 0.9;

  const poleAngle = (2 * Math.PI) / numPoles;
  const magnetInnerR = rotorInnerR + rotorHouseH;
  const magnetOuterR = rotorOuterR - 0.5;
  const halfAngle = (poleAngle * magFillDown) / 2;

  return Array.from({ length: numPoles }, (_, i) => {
    const center = i * poleAngle;
    const shape = new THREE.Shape();
    shape.absarc(0, 0, magnetOuterR, center - halfAngle, center + halfAngle, false);
    shape.absarc(0, 0, magnetInnerR, center + halfAngle, center - halfAngle, true);
    shape.closePath();

    const extruded = new THREE.ExtrudeGeometry(shape, { depth: statorWidth, bevelEnabled: false });
    extruded.translate(0, 0, -statorWidth / 2);

    return { geometry: extruded, poleIndex: i, direction: i % 2 === 0 ? 'outward' : 'inward' };
  });
}

export default ApiStatorMesh;

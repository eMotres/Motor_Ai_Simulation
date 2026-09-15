/**
 * THE HEAT-PATH VIEW — the machine, with the cooled surfaces glowing.
 *
 * User, 2026-09-15: *"лучше нарисовать 3D модель с катушками (end windings) и на
 * ней прямо показывать, куда и сколько тепла может отводиться, чтобы
 * пользователю было всё ясно и понятно"*.
 *
 * Nothing here is solved, fetched or meshed: the model comes from
 * `heatPaths.buildHeatPathModel` on the payload the tab already has, and the
 * machine is built out of PRIMITIVES — extruded annuli for the yoke, the core,
 * the magnets, the rotor and the shaft, and two bands standing ℓ_end proud of
 * the core for the END WINDINGS, which on a Ø85 robot joint are half the
 * machine's exposed area and the reason this picture exists.  No STL, no
 * `/api/geometry/*` round trip, so it draws on a machine whose CAD kernel is
 * blocked (WDAC) exactly as it does on one whose is not.
 *
 * Each cooled surface is tinted by its SHARE of the removed heat and carries an
 * arrow whose length follows the same number, plus one label: "mount 48.5 W ·
 * 86 %".  A path that is off is drawn grey and says so by name — "nothing
 * sticks out of this housing" is an answer about the machine, and a blank
 * surface would read as a measurement of zero.
 *
 * DEMAND-MODE RENDERING, like every other canvas in this app: `frameloop
 * ="demand"` plus `guardCanvas` (which redraws after a WebGL context restore —
 * the 2026-09-13 freeze left two "Context Lost" lines and a blank canvas
 * because nothing asked for a frame afterwards).  Anything that changes the
 * scene outside of an orbit — a new solve, a toggle — has to call `invalidate`,
 * which is what `<Invalidator>` below is for.
 *
 * Units are millimetres, the cross-section is in XY and the stack runs along
 * +Z — the same convention as the 3-D tab.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { Html, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import { Box, Paper, Switch, Tooltip, Typography } from '@mui/material';

import { guardCanvas } from '../viewer3d/webglGuard';
import { PART_COLORS } from '../../lib/partColors';
import { TIP_PROPS } from './HelpTip';
import {
  buildHeatPathModel, fmtW, sinkColour, sinkLabel, sinkTooltip,
} from './heatPaths';
import type { HeatPathModel, HeatSink, MachineEnvelope } from './heatPaths';

const lbl = { fontSize: 11, color: 'var(--text-3)' } as const;

/* ── primitives ──────────────────────────────────────────────────────────── */

/** An annular solid: the ring [rIn, rOut] extruded `len` along +Z, centred on
 *  z = 0.  `thetaLength < 2π` cuts a sector, which is how the machine is opened
 *  up so the rotor, the magnets and the shaft can be seen at all. */
function ringSolid(rIn: number, rOut: number, len: number,
                   thetaStart = 0, thetaLength = Math.PI * 2): THREE.BufferGeometry {
  const ro = Math.max(rOut, 1e-4);
  const ri = Math.max(Math.min(rIn, ro - 1e-4), 0);
  const full = thetaLength >= Math.PI * 2 - 1e-6;
  const shape = new THREE.Shape();
  if (full) {
    shape.absarc(0, 0, ro, 0, Math.PI * 2, false);
    if (ri > 1e-4) {
      const hole = new THREE.Path();
      hole.absarc(0, 0, ri, 0, Math.PI * 2, true);
      shape.holes.push(hole);
    }
  } else {
    shape.absarc(0, 0, ro, thetaStart, thetaStart + thetaLength, false);
    if (ri > 1e-4) shape.absarc(0, 0, ri, thetaStart + thetaLength, thetaStart, true);
    else shape.lineTo(0, 0);
    shape.closePath();
  }
  const geo = new THREE.ExtrudeGeometry(shape, {
    depth: Math.max(len, 1e-3), bevelEnabled: false, curveSegments: 72,
  });
  geo.translate(0, 0, -Math.max(len, 1e-3) / 2);
  geo.computeVertexNormals();
  return geo;
}

/** A lateral surface — the skin a film acts on.  Open-ended, double-sided, so
 *  the bore reads from inside and the housing from outside. */
function skin(r: number, len: number, thetaStart: number,
              thetaLength: number): THREE.BufferGeometry {
  const g = new THREE.CylinderGeometry(r, r, Math.max(len, 1e-3), 72, 1, true,
                                       thetaStart, thetaLength);
  g.rotateX(Math.PI / 2);          // the viewer's stack runs along +Z
  return g;
}

/** A flat ring at one axial station — an end face. */
function faceRing(rIn: number, rOut: number, thetaStart: number,
                  thetaLength: number): THREE.BufferGeometry {
  return new THREE.RingGeometry(Math.max(rIn, 0), Math.max(rOut, rIn + 1e-3),
                                72, 1, thetaStart, thetaLength);
}

/* ── the machine ─────────────────────────────────────────────────────────── */

interface PartSpec {
  key: string; rIn: number; rOut: number; len: number; z: number;
  colour: string; opacity: number;
}

/** The solids, from the envelope alone.  Every one of them is a ring: this is a
 *  radial machine, and a picture made of anything else would be inventing
 *  detail the model does not have. */
function partsOf(g: MachineEnvelope): PartSpec[] {
  const L = g.stack_length_mm ?? 0;
  const half = L / 2;
  const lEnd = g.end_winding_overhang_mm ?? 0;
  const ext = g.shaft_extension_mm ?? 0;
  const out: PartSpec[] = [];
  const push = (key: string, rIn: number | undefined, rOut: number | undefined,
                len: number, z: number, colour: string, opacity = 1) => {
    if (rIn === undefined || rOut === undefined) return;
    if (!(rOut > rIn + 1e-4) || !(len > 1e-4)) return;
    out.push({ key, rIn, rOut, len, z, colour, opacity });
  };
  // the yoke / housing — the surface the film and the bolts act on
  push('housing', g.yoke_r_in_mm, g.housing_r_mm, L, 0, PART_COLORS.statorIron);
  // the toothed band, drawn as one ring: the slot pitch is not what this
  // picture is about, and 24 teeth would hide the end turns behind them
  push('teeth', g.slot_r_in_mm, g.slot_r_out_mm, L, 0, '#4d5f7d', 0.92);
  // THE END WINDINGS — ℓ_end proud of the core on each side
  if (lEnd > 1e-4) {
    push('endwind+', g.end_winding_r_in_mm, g.end_winding_r_out_mm, lEnd,
         half + lEnd / 2, PART_COLORS.copper);
    push('endwind-', g.end_winding_r_in_mm, g.end_winding_r_out_mm, lEnd,
         -(half + lEnd / 2), PART_COLORS.copper);
  }
  push('sleeve', g.sleeve_r_in_mm ?? undefined, g.sleeve_r_out_mm ?? undefined,
       L, 0, PART_COLORS.sleeve);
  push('magnets', g.magnet_r_in_mm, g.magnet_r_out_mm, L, 0, PART_COLORS.magnetN, 0.95);
  push('rotor', g.rotor_iron_r_in_mm, g.rotor_iron_r_out_mm, L, 0, PART_COLORS.rotorIron);
  push('shaft', g.bore_r_mm, g.shaft_r_out_mm, L + 2 * ext, 0, PART_COLORS.shaft);
  return out;
}

/* ── where a sink's skin, arrow and label go ─────────────────────────────── */

interface Overlay {
  sink: HeatSink;
  geo: THREE.BufferGeometry;
  /** mid-point of the surface, and the direction the heat leaves along */
  at: THREE.Vector3;
  dir: THREE.Vector3;
}

const EPS = 0.35;   // mm — lift a tint off the solid it sits on, so it shows

function overlaysOf(model: HeatPathModel, thetaStart: number,
                    thetaLength: number): Overlay[] {
  const g = model.geometry;
  const half = (g.stack_length_mm ?? 0) / 2;
  const out: Overlay[] = [];
  // EACH SINK GETS ITS OWN AZIMUTH for the arrow and the label, fanned across
  // the drawn sector.  The tint still covers the whole surface — it is the
  // surface that is cooled — but six billboards anchored on the same meridian
  // land on top of each other and the picture becomes unreadable, which is the
  // one thing this view exists to avoid.
  const shown = model.sinks.filter((s) => s.active);
  const angleOf = (id: string): number => {
    const i = shown.findIndex((s) => s.id === id);
    if (i < 0 || shown.length === 0) return thetaStart + thetaLength / 2;
    return thetaStart + thetaLength * (i + 0.5) / shown.length;
  };
  for (const s of model.sinks) {
    const p = s.placement;
    const mid = angleOf(s.id);
    if (p.kind === 'cylinder') {
      const r = p.r_mm ?? 0;
      if (!(r > 0)) continue;
      const outward = p.facing !== 'in';
      const rr = r + (outward ? EPS : -EPS);
      const geo = skin(rr, (p.z1_mm ?? half) - (p.z0_mm ?? -half), thetaStart, thetaLength);
      out.push({
        sink: s, geo,
        at: new THREE.Vector3(rr * Math.cos(mid), rr * Math.sin(mid), 0),
        // the bore hands its heat to air that then leaves ALONG the bore, so
        // its arrow is axial; the housing's is radial, which is where it goes
        dir: outward
          ? new THREE.Vector3(Math.cos(mid), Math.sin(mid), 0)
          : new THREE.Vector3(0, 0, 1),
      });
    } else if (p.kind === 'annulus' || p.kind === 'band' || p.kind === 'stub') {
      const sides = p.sides?.length ? p.sides : [1];
      for (const side of sides) {
        const rIn = (p.kind === 'stub' ? p.r_in_mm : p.r_in_mm) ?? 0;
        const rOut = (p.kind === 'stub' ? p.r_mm : p.r_out_mm) ?? 0;
        if (!(rOut > 0)) continue;
        const z = side * ((p.z_mm ?? half)
          + (p.kind === 'band' ? (p.length_mm ?? 0) : 0)
          + (p.kind === 'stub' ? (p.length_mm ?? 0) : 0)) + side * EPS;
        const geo = faceRing(rIn, rOut, thetaStart, thetaLength);
        const rMid = (rIn + rOut) / 2;
        out.push({
          sink: s, geo,
          at: new THREE.Vector3(rMid * Math.cos(mid), rMid * Math.sin(mid), z),
          dir: new THREE.Vector3(0, 0, side),
        });
      }
    }
  }
  return out;
}

/* ── the scene ───────────────────────────────────────────────────────────── */

/**
 * Demand mode draws nothing on its own.  Every change that is not an orbit —
 * a new solve, a toggle — has to ask for a frame, or the canvas keeps showing
 * the previous machine until the mouse moves over it.
 *
 * `camera` and `size` are dependencies and not decoration.  On a FIRST mount
 * r3f draws its one frame through its OWN camera (at z = 5 mm, inside the
 * rotor) and only then does drei's `<PerspectiveCamera makeDefault>` take over
 * — nothing asks for a frame after that swap, so the canvas stays black until
 * the user happens to drag it.  That is exactly the shape of the 2026-09-13
 * freeze (a correct scene, no error, no frame), and it cost this view a
 * debugging round: the fix is to invalidate when the default camera or the
 * canvas size changes, which is when the previous frame stopped being valid.
 */
const Invalidator: React.FC<{ dep: unknown }> = ({ dep }) => {
  const invalidate = useThree((s) => s.invalidate);
  const camera = useThree((s) => s.camera);
  const w = useThree((s) => s.size.width);
  const h = useThree((s) => s.size.height);
  useEffect(() => {
    invalidate();
    // …and once more after the commit settles: a layout that measures 0 on the
    // first pass (a collapsed panel, a tab mounting off-screen) produces a
    // frame of nothing, and no further change would ask for another.
    const t = setTimeout(invalidate, 120);
    return () => clearTimeout(t);
  }, [dep, camera, w, h, invalidate]);
  return null;
};

/** Point the default camera at the machine and frame it.  Runs on mount and
 *  whenever the machine's size changes, and asks for the frame that shows it. */
const Fit: React.FC<{ radiusMm: number }> = ({ radiusMm }) => {
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls) as
    { target?: THREE.Vector3; update?: () => void } | null;
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    const R = Math.max(radiusMm, 1);
    camera.up.set(0, 0, 1);                       // the stack runs along +Z
    camera.position.set(R * 1.7, -R * 2.1, R * 1.6);
    camera.near = Math.max(R * 0.01, 0.1);
    camera.far = R * 60;
    const persp = camera as THREE.PerspectiveCamera;
    if (persp.isPerspectiveCamera) persp.fov = 38;
    camera.updateProjectionMatrix();
    camera.lookAt(0, 0, 0);
    controls?.target?.set(0, 0, 0);
    controls?.update?.();
    invalidate();
  }, [radiusMm, camera, controls, invalidate]);
  return null;
};

const Arrow: React.FC<{ at: THREE.Vector3; dir: THREE.Vector3; len: number;
                        colour: string }> = ({ at, dir, len, colour }) => {
  const q = useMemo(() => new THREE.Quaternion().setFromUnitVectors(
    new THREE.Vector3(0, 1, 0), dir.clone().normalize()), [dir]);
  const shaftLen = Math.max(len * 0.7, 0.6);
  const headLen = Math.max(len * 0.3, 0.4);
  const rad = Math.max(len * 0.07, 0.25);
  const mid = at.clone().addScaledVector(dir.clone().normalize(), shaftLen / 2);
  const tip = at.clone().addScaledVector(dir.clone().normalize(), shaftLen + headLen / 2);
  return (
    <group>
      <mesh position={mid} quaternion={q}>
        <cylinderGeometry args={[rad, rad, shaftLen, 12]} />
        <meshBasicMaterial color={colour} />
      </mesh>
      <mesh position={tip} quaternion={q}>
        <coneGeometry args={[rad * 2.4, headLen, 14]} />
        <meshBasicMaterial color={colour} />
      </mesh>
    </group>
  );
};

const Scene: React.FC<{ model: HeatPathModel; cut: boolean; labels: boolean }> =
({ model, cut, labels }) => {
  const g = model.geometry;
  const thetaStart = cut ? Math.PI * 0.25 : 0;
  const thetaLength = cut ? Math.PI * 1.5 : Math.PI * 2;
  const parts = useMemo(() => partsOf(g), [g]);
  const partGeos = useMemo(
    () => parts.map((p) => ringSolid(p.rIn, p.rOut, p.len, thetaStart, thetaLength)),
    [parts, thetaStart, thetaLength]);
  const overlays = useMemo(
    () => overlaysOf(model, thetaStart, thetaLength),
    [model, thetaStart, thetaLength]);

  useEffect(() => () => {
    partGeos.forEach((x) => x.dispose());
  }, [partGeos]);
  useEffect(() => () => {
    overlays.forEach((o) => o.geo.dispose());
  }, [overlays]);

  const R = Math.max(g.housing_r_mm ?? 50, 1);
  // The arrow length follows √(share of the biggest path), not the share: on
  // this joint the mount is 86 % and a linear scale would leave every other
  // arrow shorter than its own head.  The floor is not decoration either — an
  // arrow shorter than its own label's leader would put the billboard back on
  // the surface it is meant to point away from.
  const arrowLen = (s: HeatSink) => R * (0.22 + 0.5 * Math.sqrt(s.intensity));

  return (
    <>
      <ambientLight intensity={0.85} />
      <directionalLight position={[R * 2, R * 2, R * 3]} intensity={1.1} />
      <directionalLight position={[-R * 2, -R, -R * 2]} intensity={0.5} />
      {parts.map((p, i) => (
        <mesh key={p.key} geometry={partGeos[i]} position={[0, 0, p.z]}>
          <meshStandardMaterial
            color={p.colour} roughness={0.55} metalness={0.25}
            transparent={p.opacity < 1} opacity={p.opacity} />
        </mesh>
      ))}
      {overlays.map((o, i) => (
        <mesh key={`${o.sink.id}-${i}`} geometry={o.geo}>
          <meshBasicMaterial
            color={sinkColour(o.sink)} transparent
            opacity={o.sink.active ? 0.55 + 0.35 * o.sink.intensity : 0.22}
            side={THREE.DoubleSide} depthWrite={false} />
        </mesh>
      ))}
      {overlays.filter((o) => o.sink.active).map((o, i) => (
        <Arrow key={`a-${o.sink.id}-${i}`} at={o.at} dir={o.dir}
               len={arrowLen(o.sink)} colour={sinkColour(o.sink)} />
      ))}
      {labels && overlays.filter((o) => o.sink.active)
        // ONE label per sink, on its first surface: two identical billboards on
        // the two ends of the machine say nothing the one does not.
        .filter((o, i, all) => all.findIndex((x) => x.sink.id === o.sink.id) === i)
        .map((o, i) => (
          <Html key={`l-${o.sink.id}`} zIndexRange={[20, 0]} center
                position={o.at.clone().addScaledVector(
                  o.dir.clone().normalize(), arrowLen(o.sink) * 1.35)}>
            <div title={sinkTooltip(o.sink)}
                 style={{
                   fontFamily: 'monospace', fontSize: 10.5, whiteSpace: 'nowrap',
                   padding: '1px 5px', borderRadius: 3, cursor: 'help',
                   background: 'rgba(15,20,28,0.82)', color: '#e6edf5',
                   border: `1px solid ${sinkColour(o.sink)}`,
                   // Fanning the anchors by azimuth separates most of them; two
                   // paths that leave the SAME end at neighbouring angles still
                   // project within a line of each other, so the billboards are
                   // staggered in screen space as well.  Purely cosmetic: the
                   // arrow, not the label, says where the surface is.
                   transform: `translateY(${(i % 3) * 15 - 15}px)`,
                 }}>
              {sinkLabel(o.sink)}
            </div>
          </Html>
        ))}
      <OrbitControls makeDefault enablePan enableDamping={false} target={[0, 0, 0]} />
      {/* The camera is r3f's OWN, aimed here — not a `<PerspectiveCamera
          makeDefault>`.  A drei camera swaps the default one after the first
          frame has already been drawn through r3f's (which sits at z = 5 mm,
          inside the rotor), and in demand mode nothing asks for the frame that
          would show the swap: the canvas stays black, with no error and a
          scene that is perfectly correct.  Aiming the existing camera and
          calling `invalidate` is one code path, and it works on the first
          mount as well as on a machine that changes size. */}
      <Fit radiusMm={R} />
      <Invalidator dep={`${model.schema_version}:${model.totals.removed_W}:${cut}:${labels}`} />
    </>
  );
};

/* ── the panel ───────────────────────────────────────────────────────────── */

export interface HeatPathView3DProps {
  /** the thermal result — `/api/thermal/field`'s payload, or a stored record */
  res: unknown;
  /** the live geometry, as the API serves it (`motorStore.geometry`) */
  geometry: Record<string, unknown> | null | undefined;
  /** 'result is for a previous geometry' — shown, never hidden */
  staleNote?: string | null;
}

const HeatPathView3D: React.FC<HeatPathView3DProps> = ({ res, geometry, staleNote }) => {
  const [cut, setCut] = useState(true);
  const [labels, setLabels] = useState(true);
  const wrap = useRef<HTMLDivElement>(null);

  const model = useMemo(
    () => buildHeatPathModel(res as Record<string, unknown> | null, geometry ?? null),
    [res, geometry]);

  if (!model.ok) return null;
  const drawable = model.geometry.known;
  const t = model.totals;
  const active = model.sinks.filter((s) => s.active);

  return (
    <Paper sx={{ p: 1.25, bgcolor: 'var(--panel)' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap', mb: 0.75 }}>
        <Typography sx={{ ...lbl, color: 'var(--text-2)', fontWeight: 600 }}>
          Heat paths
        </Typography>
        {/* One short line, the rest in the tooltip — the project's rule. */}
        <Tooltip {...TIP_PROPS} title={`Every watt that left the model, on the surface it left through. The colour and the arrow follow the share of the BIGGEST path (here ${active[0]?.short ?? '—'}), not of the total — on a joint whose mount takes 86 % a share scale would make every other path invisible. Shares are of what LEFT, so they add to 100 % whatever the closure error is; the residual is the line on the right. Off paths are grey and named: "nothing sticks out of this housing" is an answer about the machine, not a missing number.`}>
          <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace',
                            borderBottom: '1px dotted var(--text-4)' }}>
            in {fmtW(t.generated_W)} · out {fmtW(t.removed_W)} · residual {fmtW(t.residual_W)}
            {t.residual_pct !== null ? ` (${t.residual_pct} %)` : ''}
          </Typography>
        </Tooltip>
        <Box sx={{ flex: 1 }} />
        <Tooltip {...TIP_PROPS} title="Cut a quarter out of the machine so the magnets, the rotor and the bore can be seen. Nothing is removed from the model — this is the same solids, drawn over three quarters of a turn.">
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <Switch size="small" checked={cut} onChange={(e) => setCut(e.target.checked)} />
            <Typography sx={lbl}>Section</Typography>
          </Box>
        </Tooltip>
        <Tooltip {...TIP_PROPS} title="The watts and the share, written on the surface they left through. Hover a label for the film or the conductance behind it.">
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <Switch size="small" checked={labels} onChange={(e) => setLabels(e.target.checked)} />
            <Typography sx={lbl}>Labels</Typography>
          </Box>
        </Tooltip>
      </Box>

      {staleNote && (
        <Typography sx={{ ...lbl, color: '#fbbf24', mb: 0.5 }}>{staleNote}</Typography>
      )}

      {!drawable ? (
        <Typography sx={lbl}>
          no geometry loaded — the watts are in the legend below
        </Typography>
      ) : (
        <Box ref={wrap} sx={{ height: 420, position: 'relative',
                              bgcolor: 'var(--panel-2, #0e1319)', borderRadius: 1 }}>
          <Canvas frameloop="demand" dpr={[1, 2]}
                  onCreated={guardCanvas('thermal heat-path view')}>
            <Scene model={model} cut={cut} labels={labels} />
          </Canvas>
        </Box>
      )}

      {/* ── the legend: every path, off ones included ─────────────────────── */}
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', mt: 0.75 }}>
        {model.sinks.map((s) => (
          <Tooltip key={s.id} {...TIP_PROPS} title={sinkTooltip(s)}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, cursor: 'help' }}>
              <Box sx={{ width: 9, height: 9, borderRadius: '2px',
                         bgcolor: sinkColour(s), opacity: s.active ? 1 : 0.5 }} />
              <Typography sx={{ ...lbl, fontFamily: 'monospace',
                                opacity: s.active ? 1 : 0.55 }}>
                {sinkLabel(s)}
              </Typography>
            </Box>
          </Tooltip>
        ))}
      </Box>
      <Typography sx={{ ...lbl, mt: 0.5, fontFamily: 'monospace' }}>
        stator side {t.stator_side_pct ?? '—'} % · rotor side {t.rotor_side_pct ?? '—'} %
        {model.geometry.end_winding_overhang_mm
          ? ` · end turns ${model.geometry.end_winding_overhang_mm} mm proud each side (k_end ${model.geometry.k_end})`
          : ''}
      </Typography>
    </Paper>
  );
};

export default HeatPathView3D;

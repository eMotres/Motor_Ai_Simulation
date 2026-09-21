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
 * arrow whose length follows the same number, plus one label: "mount 2 W/K @
 * 40 °C · 48.5 W · 86 %" — what the surface is SET to, and what that bought.  A
 * path that is off is drawn grey and says so by name — "nothing sticks out of
 * this housing" is an answer about the machine, and a blank surface would read
 * as a measurement of zero.
 *
 * …AND IT IS WHERE THE VALUES ARE SET (user, 2026-09-15: *"дай возможность
 * задавать значения прямо в нём — так намного удобнее, и определи его в это
 * окно, где всё и задаётся"*).  Clicking a surface opens a small popover
 * anchored to it with exactly the fields that surface owns — ε and the room on
 * the housing, W/K and the mount temperature on the flange, open/closed and how
 * many ends on the end faces, the mode in the bore, millimetres on the shaft
 * stubs — and every one of them writes the SAME `thermalStore` field the
 * panel's own text box writes.  There is one state, so the panel and the model
 * cannot disagree, and neither of them solves: `Solve` stays the one button.
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
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { Html, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import { Box, Paper, Switch, Tooltip, Typography } from '@mui/material';

import { guardCanvas } from '../viewer3d/webglGuard';
import { PART_COLORS } from '../../lib/partColors';
import { TIP_PROPS } from './HelpTip';
import {
  arrowTooltip, brighten, buildHeatPathModel, coolingFromSettings, editorFor,
  fmtW, settingLabel, sinkColour, sinkLabel, sinkTooltip,
} from './heatPaths';
import type {
  CoolingSettings, HeatPathModel, HeatSink, MachineEnvelope, SinkEditor, SinkId,
} from './heatPaths';
/* The SAME words the Thermal panel's rows carry (owner 2026-09-17: the cooling
   menu must say what each parameter is for).  Imported, not re-typed: a
   surface's popover and the row it mirrors cannot be allowed to explain one
   parameter two different ways. */
import { ROBOTICS_HELP, setByLine } from './roboticsHelp';

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
                    thetaLength: number, fanIds: SinkId[] = []): Overlay[] {
  const g = model.geometry;
  const half = (g.stack_length_mm ?? 0) / 2;
  const out: Overlay[] = [];
  // EACH LABELLED SINK GETS ITS OWN AZIMUTH for the arrow and the label, fanned
  // across the drawn sector.  The tint still covers the whole surface — it is
  // the surface that is cooled — but six billboards anchored on the same
  // meridian land on top of each other and the picture becomes unreadable,
  // which is the one thing this view exists to avoid.  `fanIds` is the list the
  // caller will actually label, so a machine with NOTHING solved (every sink at
  // 0 W) fans its settings rather than stacking them all on one meridian.
  const angleOf = (id: string): number => {
    const i = fanIds.indexOf(id as SinkId);
    if (i < 0 || fanIds.length === 0) return thetaStart + thetaLength / 2;
    return thetaStart + thetaLength * (i + 0.5) / fanIds.length;
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

/**
 * ONE CHANNEL, drawn.  Its length already follows the watts (√share of the
 * biggest path) and that does not change on hover — an arrow that grew when
 * pointed at would be reporting the mouse, not the machine.  What hover changes
 * is only the READING: the arrow goes brighter and thicker, and its tooltip
 * says which channel it is and how much goes down it.
 *
 * The pointer events sit on an INVISIBLE fat cylinder around the whole arrow
 * (opacity 0, not `visible={false}`, which r3f's raycaster skips): a 0.25 mm
 * shaft on a Ø85 machine is a few screen pixels and nobody can hit it.  Its
 * radius is set by the MACHINE (`hitR`), not by the arrow — a 3 % path draws a
 * thin arrow and is exactly the one a reader wants explained, so its target
 * must not be thin too.
 */
const Arrow: React.FC<{
  at: THREE.Vector3; dir: THREE.Vector3; len: number; colour: string;
  hitR?: number; hot?: boolean; onOver?: () => void; onOut?: () => void;
}> = ({ at, dir, len, colour, hitR, hot, onOver, onOut }) => {
  const q = useMemo(() => new THREE.Quaternion().setFromUnitVectors(
    new THREE.Vector3(0, 1, 0), dir.clone().normalize()), [dir]);
  const shaftLen = Math.max(len * 0.7, 0.6);
  const headLen = Math.max(len * 0.3, 0.4);
  const rad = Math.max(len * 0.07, 0.25) * (hot ? 1.5 : 1);
  const n = dir.clone().normalize();
  const mid = at.clone().addScaledVector(n, shaftLen / 2);
  const tip = at.clone().addScaledVector(n, shaftLen + headLen / 2);
  const hit = at.clone().addScaledVector(n, (shaftLen + headLen) / 2);
  const shown = hot ? brighten(colour) : colour;
  return (
    <group>
      <mesh position={mid} quaternion={q}>
        <cylinderGeometry args={[rad, rad, shaftLen, 12]} />
        <meshBasicMaterial color={shown} />
      </mesh>
      <mesh position={tip} quaternion={q}>
        <coneGeometry args={[rad * 2.4, headLen, 14]} />
        <meshBasicMaterial color={shown} />
      </mesh>
      <mesh position={hit} quaternion={q}
            onPointerOver={onOver ? (e) => { e.stopPropagation(); onOver(); } : undefined}
            onPointerOut={onOut ? () => onOut() : undefined}>
        <cylinderGeometry args={[Math.max(rad * 3.2, hitR ?? 1.2),
                                 Math.max(rad * 3.2, hitR ?? 1.2),
                                 shaftLen + headLen, 10]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
    </group>
  );
};

/* ── the popovers: the same fields the panel has, on the surface they set ─── */

const FIELD: React.CSSProperties = {
  background: '#0c1118', color: '#e6edf5', border: '1px solid #33435a',
  borderRadius: 3, fontSize: 11, fontFamily: 'monospace', padding: '2px 4px',
  width: 70,
};
const CAP: React.CSSProperties = { fontSize: 10, color: '#9fb0c4' };

const Num: React.FC<{ cap: string; value: string; hint: string; width?: number;
                      step?: number; onChange: (v: string) => void }> =
({ cap, value, hint, width, step, onChange }) => (
  <label title={hint} style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'help' }}>
    <span style={CAP}>{cap}</span>
    <input type="number" value={value} step={step ?? 'any'}
           onChange={(e) => onChange(e.target.value)}
           style={{ ...FIELD, width: width ?? FIELD.width }} />
  </label>
);

const Sel: React.FC<{ cap: string; value: string; hint: string;
                      opts: [string, string][]; onChange: (v: string) => void }> =
({ cap, value, hint, opts, onChange }) => (
  <label title={hint} style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'help' }}>
    <span style={CAP}>{cap}</span>
    <select value={value} onChange={(e) => onChange(e.target.value)}
            style={{ ...FIELD, width: 'auto' }}>
      {opts.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
    </select>
  </label>
);

/**
 * What one surface owns, as the panel owns it.
 *
 * EVERY field here writes a `thermalStore` key through the same `set` the
 * panel's own text box calls — there is no second copy of the state, so a value
 * typed on the model is on the panel before the popover closes and vice versa.
 * And nothing here solves: a cooling edit has never re-run the map in this tab,
 * and making the 3-D the one place that did would be the silent state mutation
 * this project forbids.
 */
const SinkEditorCard: React.FC<{
  editor: SinkEditor; sink: HeatSink; s: CoolingSettings;
  onChange: (k: keyof CoolingSettings, v: string) => void; onClose: () => void;
}> = ({ editor, sink, s, onChange, onClose }) => {
  const rows: React.ReactNode[] = [];
  if (editor === 'housing') {
    if (s.coolMode === 'robotics') {
      rows.push(<Num key="e" cap={ROBOTICS_HELP.emissivity.short} value={s.emissivity}
                     step={0.05} width={58}
                     hint={ROBOTICS_HELP.emissivity.tip}
                     onChange={(v) => onChange('emissivity', v)} />);
    } else if (s.coolMode === 'air') {
      rows.push(<Num key="v" cap="m/s" value={s.airSpeed} width={58}
                     hint="Blow speed over the housing, m/s. Still air is not zero cooling — it is natural convection, about 7 W/m²K."
                     onChange={(v) => onChange('airSpeed', v)} />);
    } else if (s.coolMode === 'liquid') {
      rows.push(<Num key="q" cap="L/min" value={s.flowLpm} width={58}
                     hint="Coolant flow through the jacket, litres per minute."
                     onChange={(v) => onChange('flowLpm', v)} />);
      rows.push(<Num key="ti" cap="in °C" value={s.tIn} width={58}
                     hint="Coolant inlet temperature, °C."
                     onChange={(v) => onChange('tIn', v)} />);
    } else if (s.coolMode === 'manual') {
      rows.push(<Num key="h" cap="h" value={s.hConv} width={68}
                     hint="The film coefficient you are imposing on the housing, W/m²K."
                     onChange={(v) => onChange('hConv', v)} />);
    }
    if (s.coolMode !== 'liquid') {
      rows.push(<Num key="a" cap={s.coolMode === 'robotics'
                       ? ROBOTICS_HELP.ambientT.short : 'air °C'}
                     value={s.ambientT} width={58}
                     hint={s.coolMode === 'robotics' ? ROBOTICS_HELP.ambientT.tip
                       : 'The temperature this surface works against. It is also the sink the end faces, the bore and the mount fall back to.'}
                     onChange={(v) => onChange('ambientT', v)} />);
    }
  } else if (editor === 'mount') {
    rows.push(<Num key="g" cap={ROBOTICS_HELP.mountG.short} value={s.mountG} width={62}
                   hint={ROBOTICS_HELP.mountG.tip}
                   onChange={(v) => onChange('mountG', v)} />);
    rows.push(<Num key="t" cap={ROBOTICS_HELP.mountT.short} value={s.mountT} width={62}
                   hint={ROBOTICS_HELP.mountT.tip}
                   onChange={(v) => onChange('mountT', v)} />);
  } else if (editor === 'end_faces') {
    rows.push(<Sel key="m" cap={ROBOTICS_HELP.endFaces.short}
                   value={s.endFaces === 'none' ? 'none' : 'still'}
                   hint={ROBOTICS_HELP.endFaces.tip}
                   opts={[['still', 'open to air'], ['none', 'closed off']]}
                   onChange={(v) => onChange('endFaces', v)} />);
    if (s.endFaces !== 'none') {
      rows.push(<Sel key="n" cap={ROBOTICS_HELP.endFaceSides.short}
                     value={String(Number(s.endFaceSides) === 1 ? 1 : 2)}
                     hint={ROBOTICS_HELP.endFaceSides.tip}
                     opts={[['2', 'both'], ['1', '1 (mount shut)']]}
                     onChange={(v) => onChange('endFaceSides', v)} />);
    }
  } else if (editor === 'bore') {
    rows.push(<Sel key="m" cap={ROBOTICS_HELP.boreMode.short} value={s.boreMode}
                   hint={ROBOTICS_HELP.boreMode.tip}
                   opts={[['none', 'closed'], ['still', 'still air'], ['air', 'air'], ['liquid', 'liquid']]}
                   onChange={(v) => onChange('boreMode', v)} />);
    if (s.boreMode === 'air') {
      rows.push(<Num key="v" cap="m/s" value={s.boreAirSpeed} width={58}
                     hint="Air speed through the bore, m/s, at the ambient above — it is the same air."
                     onChange={(v) => onChange('boreAirSpeed', v)} />);
    }
    if (s.boreMode === 'liquid') {
      rows.push(<Num key="q" cap="L/min" value={s.boreFlowLpm} width={58}
                     hint="Bore pump, litres per minute — required and greater than zero."
                     onChange={(v) => onChange('boreFlowLpm', v)} />);
    }
  } else if (editor === 'shaft_ends') {
    rows.push(<Num key="l" cap={ROBOTICS_HELP.shaftExtMm.short} value={s.shaftExtMm}
                   width={62} hint={ROBOTICS_HELP.shaftExtMm.tip}
                   onChange={(v) => onChange('shaftExtMm', v)} />);
    rows.push(<Sel key="n" cap={ROBOTICS_HELP.shaftExtSides.short}
                   value={String(Number(s.shaftExtSides) === 1 ? 1 : 2)}
                   hint={ROBOTICS_HELP.shaftExtSides.tip}
                   opts={[['2', '2'], ['1', '1']]}
                   onChange={(v) => onChange('shaftExtSides', v)} />);
  } else if (editor === 'frame') {
    rows.push(<Sel key="f" cap={ROBOTICS_HELP.frame.short} value={s.frame}
                   hint={ROBOTICS_HELP.frame.tip}
                   opts={[['housed', 'housed'], ['open', 'open']]}
                   onChange={(v) => onChange('frame', v)} />);
    // No wash-speed input here any more (2026-09-21): the end turns and the
    // slot channels now always take the housing's own outer-surface air
    // speed, so a second "m/s" a click away from this one would only be a
    // second place for it to disagree.
  }
  return (
    <div style={{
      background: 'rgba(12,17,24,0.96)', border: `1px solid ${sinkColour(sink)}`,
      borderRadius: 5, padding: '5px 7px', display: 'flex', flexDirection: 'column',
      gap: 4, boxShadow: '0 6px 18px rgba(0,0,0,0.55)', minWidth: 130,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 10.5, color: '#e6edf5', fontWeight: 600 }}>
          {sink.short}
        </span>
        <span style={{ flex: 1 }} />
        <button type="button" onClick={onClose} title="Close (Esc)"
                style={{ background: 'none', border: 'none', color: '#9fb0c4',
                         cursor: 'pointer', fontSize: 12, lineHeight: 1, padding: 0 }}>
          ×
        </button>
      </div>
      {rows}
      <div style={{ ...CAP, maxWidth: 210 }} title={sinkTooltip(sink)}>
        {sinkLabel(sink)} — nothing solves until you press Solve
      </div>
    </div>
  );
};

interface SceneProps {
  model: HeatPathModel; cut: boolean; labels: boolean;
  settings: CoolingSettings | null;
  selected: SinkId | null;
  hovered: SinkId | null;
  /** which ARROW the pointer is on — `${sink.id}-${surface index}`, because a
   *  path drawn on two ends has two arrows and only the one under the pointer
   *  may carry the tooltip */
  arrow: string | null;
  onSelect: (id: SinkId | null) => void;
  onHover: (id: SinkId | null) => void;
  onArrow: (key: string | null, id: SinkId | null) => void;
  onChange: (k: keyof CoolingSettings, v: string) => void;
}

const Scene: React.FC<SceneProps> =
({ model, cut, labels, settings, selected, hovered, arrow, onSelect, onHover,
   onArrow, onChange }) => {
  const g = model.geometry;
  const thetaStart = cut ? Math.PI * 0.25 : 0;
  const thetaLength = cut ? Math.PI * 1.5 : Math.PI * 2;
  const parts = useMemo(() => partsOf(g), [g]);
  const partGeos = useMemo(
    () => parts.map((p) => ringSolid(p.rIn, p.rOut, p.len, thetaStart, thetaLength)),
    [parts, thetaStart, thetaLength]);
  /* WHICH SINKS GET A BILLBOARD.  Everything that carries watts, plus — when
     the settings are in hand — one per SETTING for the paths that carry none:
     four end-face labels all saying "closed" is four times the clutter for one
     switch, and the switch is what the user came here for.  The list is also
     what the azimuths are fanned across, so the labels never stack. */
  const labelIds = useMemo<SinkId[]>(() => {
    const out: SinkId[] = [];
    const seenEditor = new Set<string>();
    for (const s of model.sinks) {
      if (s.active) { out.push(s.id); seenEditor.add(String(editorFor(s.id))); }
    }
    if (settings) {
      for (const s of model.sinks) {
        const ed = editorFor(s.id);
        if (!ed || s.active || seenEditor.has(ed)) continue;
        seenEditor.add(ed);
        out.push(s.id);
      }
    }
    return out;
  }, [model, settings]);
  const overlays = useMemo(
    () => overlaysOf(model, thetaStart, thetaLength, labelIds),
    [model, thetaStart, thetaLength, labelIds]);

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
      {/* THE COOLED SURFACES — tinted, and each of them a control.  The
          pointer events stop propagating so a click lands on the surface it
          was aimed at and not on the one behind it. */}
      {overlays.map((o, i) => {
        const on = o.sink.id === selected;
        const hot = o.sink.id === hovered;
        const editable = editorFor(o.sink.id) !== null && !!settings;
        return (
          <mesh key={`${o.sink.id}-${i}`} geometry={o.geo}
                onPointerOver={editable ? (e) => { e.stopPropagation(); onHover(o.sink.id); } : undefined}
                onPointerOut={editable ? () => onHover(null) : undefined}
                onClick={editable ? (e) => {
                  e.stopPropagation();
                  onSelect(on ? null : o.sink.id);
                } : undefined}>
            <meshBasicMaterial
              color={sinkColour(o.sink)} transparent
              opacity={(o.sink.active ? 0.55 + 0.35 * o.sink.intensity : 0.22)
                       + (on ? 0.3 : hot ? 0.18 : 0)}
              side={THREE.DoubleSide} depthWrite={false} />
          </mesh>
        );
      })}
      {/* The SELECTED surface, outlined: a tint alone is ambiguous on a
          surface that is already saturated (the mount at 86 %). */}
      {overlays.filter((o) => o.sink.id === selected).map((o, i) => (
        <lineSegments key={`s-${o.sink.id}-${i}`}>
          <edgesGeometry args={[o.geo]} />
          <lineBasicMaterial color="#ffffff" transparent opacity={0.9} />
        </lineSegments>
      ))}
      {/* THE ARROWS — one per cooled surface, each of them a hover target.
          User, 2026-09-16: an arrow has to say what it MEANS and how much goes
          down it, not only how long it is. */}
      {overlays.filter((o) => o.sink.active).map((o, i) => {
        const key = `${o.sink.id}-${i}`;
        return (
          <Arrow key={`a-${key}`} at={o.at} dir={o.dir}
                 len={arrowLen(o.sink)} colour={sinkColour(o.sink)}
                 hitR={R * 0.12}
                 hot={arrow === key || hovered === o.sink.id}
                 onOver={() => onArrow(key, o.sink.id)}
                 onOut={() => onArrow(null, null)} />
        );
      })}
      {/* …and the tooltip, ONE at a time, floating off the tip of the arrow the
          pointer is on.  `pointerEvents: none` is what keeps it a tooltip: a
          card that can itself be hovered steals the leave event and the label
          never goes away. */}
      {overlays.filter((o) => o.sink.active).map((o, i) => ({ o, key: `${o.sink.id}-${i}` }))
        .filter(({ key }) => key === arrow)
        .map(({ o, key }) => {
          const tip = arrowTooltip(o.sink);
          return (
            <Html key={`t-${key}`} zIndexRange={[90, 70]} center
                  style={{ pointerEvents: 'none' }}
                  position={o.at.clone().addScaledVector(
                    o.dir.clone().normalize(), arrowLen(o.sink) * 1.15)}>
              <div style={{
                pointerEvents: 'none', fontFamily: 'monospace', fontSize: 10.5,
                lineHeight: 1.45, padding: '3px 7px', borderRadius: 4,
                background: 'rgba(8,12,18,0.94)', color: '#e6edf5',
                border: `1px solid ${brighten(sinkColour(o.sink))}`,
                boxShadow: '0 6px 18px rgba(0,0,0,0.55)',
                // A WIDTH, not a max-width: drei's `<Html>` wrapper is a
                // shrink-to-fit box with nothing to shrink against, so a
                // max-width alone leaves the text in a one-word column.
                width: 'min(440px, 76vw)',
              }}>
                <div style={{ fontWeight: 600 }}>{tip.head}</div>
                <div style={{ color: '#9fb0c4' }}>{tip.mech}</div>
                {/* …and WHICH field decides it, named exactly as the cooling
                    menu labels it, so the reader knows where to go. */}
                {setByLine(o.sink.id) && (
                  <div style={{ color: '#7f8ea3' }}>{setByLine(o.sink.id)}</div>
                )}
              </div>
            </Html>
          );
        })}
      {labels && overlays
        // ONE label per sink, on its first surface: two identical billboards on
        // the two ends of the machine say nothing the one does not.
        .filter((o) => labelIds.includes(o.sink.id))
        .filter((o, i, all) => all.findIndex((x) => x.sink.id === o.sink.id) === i)
        .map((o, i) => (
          <Html key={`l-${o.sink.id}`} zIndexRange={[20, 0]} center
                position={o.at.clone().addScaledVector(
                  o.dir.clone().normalize(), arrowLen(o.sink) * 1.6)}>
            <div title={`${sinkTooltip(o.sink)} ${setByLine(o.sink.id)} Click the surface to set it.`}
                 onClick={() => onSelect(o.sink.id === selected ? null : o.sink.id)}
                 onMouseEnter={() => onHover(o.sink.id)}
                 onMouseLeave={() => onHover(null)}
                 style={{
                   fontFamily: 'monospace', fontSize: 10.5, whiteSpace: 'nowrap',
                   padding: '1px 5px', borderRadius: 3,
                   cursor: settings ? 'pointer' : 'help',
                   background: 'rgba(15,20,28,0.82)',
                   color: o.sink.active ? '#e6edf5' : '#9fb0c4',
                   border: `1px solid ${sinkColour(o.sink)}`,
                   outline: o.sink.id === selected ? '1px solid #ffffff' : 'none',
                   // Fanning the anchors by azimuth separates most of them; two
                   // paths that leave the SAME end at neighbouring angles still
                   // project within a line of each other, so the billboards are
                   // staggered in screen space as well.  Purely cosmetic: the
                   // arrow, not the label, says where the surface is.
                   transform: `translateY(${(i % 4) * 19 - 28}px)`,
                 }}>
              {sinkLabel(o.sink, settingLabel(o.sink.id, settings))}
            </div>
          </Html>
        ))}
      {/* THE POPOVER — one at a time, anchored to its own surface.  One at a
          time is not a simplification: two cards on neighbouring end faces
          overlap each other and the machine, and a control you cannot read is
          worse than one more click. */}
      {selected && settings && (() => {
        const o = overlays.find((x) => x.sink.id === selected);
        const editor = editorFor(selected);
        if (!o || !editor) return null;
        return (
          <Html key={`p-${selected}`} zIndexRange={[60, 40]} center
                position={o.at.clone().addScaledVector(
                  o.dir.clone().normalize(), arrowLen(o.sink) * 1.6)}>
            <div style={{ transform: 'translateY(26px)' }}>
              <SinkEditorCard editor={editor} sink={o.sink} s={settings}
                              onChange={onChange} onClose={() => onSelect(null)} />
            </div>
          </Html>
        );
      })()}
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
      <Invalidator dep={`${model.schema_version}:${model.totals.removed_W}:${cut}:${labels}`
                        + `:${selected ?? ''}:${hovered ?? ''}:${arrow ?? ''}`
                        + `:${settings ? JSON.stringify(settings) : ''}`} />
    </>
  );
};

/* ── the panel ───────────────────────────────────────────────────────────── */

export interface HeatPathView3DProps {
  /** the thermal result — `/api/thermal/field`'s payload, or a stored record.
   *  `null` is legal: the machine is then drawn from the SETTINGS alone, with
   *  no watts, so the surfaces can be set before the first Solve. */
  res: unknown;
  /** the live geometry, as the API serves it (`motorStore.geometry`) */
  geometry: Record<string, unknown> | null | undefined;
  /** 'result is for a previous geometry' — shown, never hidden */
  staleNote?: string | null;
  /** the panel's OWN cooling fields (`thermalStore`).  Omitted = read-only. */
  settings?: CoolingSettings | null;
  /** writes one of them — the panel passes `thermalStore.set` straight in, so
   *  a value set on the model and a value typed in the panel are one edit. */
  onChange?: (k: keyof CoolingSettings, v: string) => void;
}

const HeatPathView3D: React.FC<HeatPathView3DProps> = ({
  res, geometry, staleNote, settings, onChange,
}) => {
  const [cut, setCut] = useState(true);
  const [labels, setLabels] = useState(true);
  const [selected, setSelected] = useState<SinkId | null>(null);
  const [hovered, setHovered] = useState<SinkId | null>(null);
  // which ARROW carries the tooltip — one at a time, by design
  const [arrow, setArrow] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  const editable = !!settings && !!onChange;
  const set = useCallback((k: keyof CoolingSettings, v: string) => {
    onChange?.(k, v);
  }, [onChange]);

  // ESC closes the popover AND drops the arrow tooltip — the same key that
  // closes every other transient thing in this app.  Click-outside is
  // `onPointerMissed` on the canvas.
  useEffect(() => {
    if (!selected && !arrow) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setSelected(null);
      setArrow(null);
      setHovered(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selected, arrow]);

  /** Hovering an arrow lights the arrow AND tints the surface it leaves
   *  through: the two are one channel, and the picture has to say so. */
  const onArrow = useCallback((key: string | null, id: SinkId | null) => {
    setArrow(key);
    setHovered(id);
  }, []);

  const model = useMemo(() => {
    const direct = buildHeatPathModel(res as Record<string, unknown> | null,
                                      geometry ?? null);
    if (direct.ok || !settings) return direct;
    // NOTHING SOLVED YET — draw the machine from the settings, with no watts.
    // This is not a result and never reads as one: every share is absent and
    // every label is the SETTING, which is exactly what there is to look at
    // before the first Solve.
    return buildHeatPathModel({ cooling: coolingFromSettings(settings) },
                              geometry ?? null);
  }, [res, geometry, settings]);

  if (!model.ok) return null;
  const drawable = model.geometry.known;
  const t = model.totals;
  const active = model.sinks.filter((s) => s.active);
  const solved = active.length > 0 && Math.abs(t.removed_W) > 1e-9;

  return (
    <Paper variant="outlined"
           sx={{ p: 1, mt: 1, bgcolor: 'var(--panel-2, #0e1319)',
                 borderColor: 'var(--text-4)' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap', mb: 0.75 }}>
        <Typography sx={{ ...lbl, color: 'var(--text-2)', fontWeight: 600 }}>
          Heat paths
        </Typography>
        {/* One short line, the rest in the tooltip — the project's rule. */}
        <Tooltip {...TIP_PROPS} title={`${editable ? 'Click a surface to set what it is — ε and the room on the housing, W/K and its temperature on the mount, open/closed on the end faces, the mode in the bore, millimetres on the shaft ends. Every field writes the same setting as the boxes above, and nothing here solves: press Solve when you are done. ' : ''}${solved ? `Each surface also carries what LEFT through it on the last solve. The colour and the arrow follow the share of the BIGGEST path (here ${active[0]?.short ?? '—'}), not of the total — on a joint whose mount takes 86 % a share scale would make every other path invisible. Shares are of what left, so they add to 100 % whatever the closure error is; the residual is on the line above. Hover an ARROW for what that channel is — the mechanism, its coefficient and the two temperatures — and how much goes down it. ` :'Nothing has been solved for this machine yet, so there are no watts on it — the labels are the settings. '}Off paths are grey and named: "nothing sticks out of this housing" is an answer about the machine, not a missing number.`}>
          <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace',
                            borderBottom: '1px dotted var(--text-4)' }}>
            {solved
              ? <>in {fmtW(t.generated_W)} · out {fmtW(t.removed_W)} · residual {fmtW(t.residual_W)}
                {t.residual_pct !== null ? ` (${t.residual_pct} %)` : ''}</>
              : <>not solved yet — labels show the settings{editable ? '; click a surface to set it' : ''}</>}
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
        <Box ref={wrap} sx={{ height: 430, position: 'relative', borderRadius: 1,
                              bgcolor: 'var(--panel, #141a22)',
                              cursor: hovered ? 'pointer' : 'default' }}>
          <Canvas frameloop="demand" dpr={[1, 2]}
                  onCreated={guardCanvas('thermal heat-path view')}
                  onPointerMissed={() => setSelected(null)}>
            <Scene model={model} cut={cut} labels={labels}
                   settings={editable ? settings ?? null : null}
                   selected={selected} hovered={hovered} arrow={arrow}
                   onSelect={setSelected} onHover={setHovered}
                   onArrow={onArrow} onChange={set} />
          </Canvas>
        </Box>
      )}

      {/* ── the legend: every path, off ones included.  A row is the same
             control as its surface, for a path the section has turned away. */}
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', mt: 0.75 }}>
        {model.sinks.map((s) => {
          const canEdit = editable && editorFor(s.id) !== null;
          return (
            <Tooltip key={s.id} {...TIP_PROPS}
                     title={`${sinkTooltip(s)} ${setByLine(s.id)}${canEdit ? ' Click to set it.' : ''}`}>
              <Box onClick={canEdit ? () => setSelected(s.id === selected ? null : s.id) : undefined}
                   onMouseEnter={canEdit ? () => setHovered(s.id) : undefined}
                   onMouseLeave={canEdit ? () => setHovered(null) : undefined}
                   sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
                         cursor: canEdit ? 'pointer' : 'help',
                         textDecoration: s.id === selected ? 'underline' : 'none' }}>
                <Box sx={{ width: 9, height: 9, borderRadius: '2px',
                           bgcolor: sinkColour(s), opacity: s.active ? 1 : 0.5 }} />
                <Typography sx={{ ...lbl, fontFamily: 'monospace',
                                  opacity: s.active ? 1 : 0.55 }}>
                  {sinkLabel(s, settingLabel(s.id, editable ? settings : null))}
                </Typography>
              </Box>
            </Tooltip>
          );
        })}
      </Box>
      <Typography sx={{ ...lbl, mt: 0.5, fontFamily: 'monospace' }}>
        {solved
          ? `stator side ${t.stator_side_pct ?? '—'} % · rotor side ${t.rotor_side_pct ?? '—'} %`
          : 'no watts yet — Solve'}
        {model.geometry.end_winding_overhang_mm
          ? ` · end turns ${model.geometry.end_winding_overhang_mm} mm proud each side (k_end ${model.geometry.k_end})`
          : ''}
      </Typography>
    </Paper>
  );
};

export default HeatPathView3D;

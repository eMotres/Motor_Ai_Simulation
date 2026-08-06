/**
 * The "3D" tab — look at what `simulation/static3d` built.
 *
 * Geometry | Mesh | Fields, one selector, one scene.  Every control that
 * changes what is TRUE about the picture (which sector, which half, which
 * solve, how much of the mesh survived the cap) states its answer on the
 * status line; the reasoning lives in a HelpTip, never in a paragraph.
 *
 * The one rule this panel exists to enforce: the model is HALF A MACHINE — one
 * anti-periodic sector of the z >= 0 half — and nothing on screen is allowed to
 * suggest otherwise.  The sector banner is not dismissible, the mirror and
 * repeat toggles are labelled as VIEWING transforms, and a field whose solve
 * was for a different machine is painted red before it is painted at all.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, FormControlLabel,
  LinearProgress, MenuItem, Select, Slider, Switch, ToggleButton,
  ToggleButtonGroup, Typography,
} from '@mui/material';
import HelpTip from '../common/HelpTip';
import { N_BANDS, bandColor } from '../simulation/fieldView';
import Viewcube from '../viewer3d/Viewcube';
import Static3DScene from './Static3DScene';
import {
  cancelSolve, fetchField, fetchGeometry, fetchMachine, fetchMesh,
  fetchSolveProgress, quote, startSolve,
} from './api';
import type {
  FieldOffer, FieldPayload, GeometryPayload, MachinePayload, SolveProgress,
  SurfacePayload,
} from './api';

type Panel = 'geometry' | 'mesh' | 'fields';

const PRESET = 'my_40mm_last';
const FIDELITIES = ['coarse', 'medium', 'passport'] as const;

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;
const val = { fontSize: 11, color: 'var(--text-1)', fontWeight: 700 } as const;

/** A number the user is meant to read, not admire. */
const Stat: React.FC<{ k: string; v: React.ReactNode; tip?: React.ReactNode }> = ({ k, v, tip }) => (
  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
    <Typography sx={lbl}>{k}</Typography>
    <Typography sx={val}>{v}</Typography>
    {tip && <HelpTip title={tip} />}
  </Box>
);

const ColorBar: React.FC<{ vmin: number; vmax: number; unit: string; note: string }> = ({
  vmin, vmax, unit, note,
}) => {
  const bands = Array.from({ length: N_BANDS }, (_, k) => bandColor(k, N_BANDS));
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
      <Typography sx={lbl}>{vmin.toPrecision(3)}</Typography>
      <Box sx={{ display: 'flex', height: 12, borderRadius: 0.5, overflow: 'hidden', border: '1px solid var(--line)' }}>
        {bands.map((c, i) => (
          <Box key={i} sx={{ width: 14, bgcolor: `rgb(${c[0]},${c[1]},${c[2]})` }} />
        ))}
      </Box>
      <Typography sx={lbl}>{vmax.toPrecision(3)} {unit}</Typography>
      <HelpTip title={note} />
    </Box>
  );
};

const Static3DPanel: React.FC = () => {
  const [panel, setPanel] = useState<Panel>('geometry');
  const [fidelity, setFidelity] = useState<string>('coarse');
  const [nonlinear, setNonlinear] = useState(true);
  const [quantity, setQuantity] = useState<'Bmag' | 'demag'>('Bmag');

  const [cutZ, setCutZ] = useState<number | null>(null);
  const [cutTheta, setCutTheta] = useState<number | null>(null);
  const [showAir, setShowAir] = useState(false);
  const [wireframe, setWireframe] = useState(true);
  const [showVectors, setShowVectors] = useState(false);
  const [fullRing, setFullRing] = useState(false);
  const [mirrorZ, setMirrorZ] = useState(false);
  const [modelledHalfOnly, setModelledHalfOnly] = useState(false);
  const [showCoils, setShowCoils] = useState(true);
  const [showEndTurns, setShowEndTurns] = useState(false);
  const [showArrows, setShowArrows] = useState(true);
  const [showGrid, setShowGrid] = useState(false);

  const [machine, setMachine] = useState<MachinePayload | null>(null);
  const [geom, setGeom] = useState<GeometryPayload | null>(null);
  const [surface, setSurface] = useState<SurfacePayload | null>(null);
  const [field, setField] = useState<FieldPayload | null>(null);
  const [offer, setOffer] = useState<FieldOffer | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [prog, setProg] = useState<SolveProgress | null>(null);
  const poll = useRef<number | null>(null);

  const MATERIALS = 'stage_a';

  // ---- loads -------------------------------------------------------------
  useEffect(() => {
    let dead = false;
    fetchMachine(PRESET, MATERIALS)
      .then((m) => { if (!dead) setMachine(m); })
      .catch((e) => { if (!dead) setErr(String(e)); });
    return () => { dead = true; };
  }, []);

  // Dragging a cut slider fires a request per step, and they do NOT come back in
  // order.  Without this guard the panel settles on whichever response happened
  // to land last — which is how the status line ends up reporting 12 828 faces
  // for a cut that has 7 788.  A number beside a picture that describes a
  // DIFFERENT picture is the exact failure this tab exists to avoid.
  const seq = useRef(0);
  const begin = () => { setBusy(true); setErr(null); return ++seq.current; };
  const current = (n: number) => n === seq.current;

  const loadGeometry = useCallback(() => {
    const n = begin();
    fetchGeometry(PRESET, MATERIALS)
      .then((d) => { if (current(n)) setGeom(d); })
      .catch((e) => { if (current(n)) setErr(String(e)); })
      .finally(() => { if (current(n)) setBusy(false); });
  }, []);

  const loadMesh = useCallback(() => {
    const n = begin();
    fetchMesh(PRESET, MATERIALS, fidelity, { z: cutZ, theta: cutTheta }, showAir, 35000)
      .then((d) => { if (current(n)) setSurface(d); })
      .catch((e) => { if (current(n)) setErr(String(e)); })
      .finally(() => { if (current(n)) setBusy(false); });
  }, [fidelity, cutZ, cutTheta, showAir]);

  const loadField = useCallback(() => {
    const n = begin();
    fetchField(PRESET, MATERIALS, fidelity, nonlinear, quantity,
      { z: cutZ, theta: cutTheta }, showVectors, 35000)
      .then((d) => {
        if (!current(n)) return;
        if (d.available) { setField(d as FieldPayload); setOffer(null); }
        else { setField(null); setOffer(d as FieldOffer); }
      })
      .catch((e) => { if (current(n)) setErr(String(e)); })
      .finally(() => { if (current(n)) setBusy(false); });
  }, [fidelity, nonlinear, quantity, cutZ, cutTheta, showVectors]);

  useEffect(() => {
    if (panel === 'geometry' && !geom) loadGeometry();
    if (panel === 'mesh') loadMesh();
    if (panel === 'fields') loadField();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel, fidelity, cutZ, cutTheta, showAir, nonlinear, quantity, showVectors]);

  // ---- the solve ---------------------------------------------------------
  const pollProgress = useCallback(() => {
    fetchSolveProgress().then((p) => {
      setProg(p);
      if (!p.running) {
        if (poll.current) { window.clearInterval(poll.current); poll.current = null; }
        if (p.phase === 'done') loadField();
      }
    }).catch(() => undefined);
  }, [loadField]);

  useEffect(() => () => { if (poll.current) window.clearInterval(poll.current); }, []);

  const runSolve = useCallback(() => {
    setErr(null);
    startSolve({ preset: PRESET, materials: MATERIALS, fidelity, nonlinear })
      .then(() => {
        if (poll.current) window.clearInterval(poll.current);
        poll.current = window.setInterval(pollProgress, 2000);
        pollProgress();
      })
      .catch((e) => setErr(String(e)));
  }, [fidelity, nonlinear, pollProgress]);

  // ---- derived -----------------------------------------------------------
  const sector = machine?.machine ?? field?.sector ?? surface?.sector ?? geom?.machine ?? null;
  const sectorDeg = sector?.sector_deg ?? 180;
  const nSectors = sector?.n_sectors ?? 2;
  const radius = (sector?.stator_od_mm ?? 40) / 2;
  const stackHalf = (sector?.stack_mm ?? 12) / 2;
  const zBox = surface?.counts?.z_box_mm ?? field?.counts?.z_box_mm ?? stackHalf * 4;

  const shown: SurfacePayload | null = panel === 'fields' ? field : panel === 'mesh' ? surface : null;
  const scale = panel === 'fields' && field ? { vmin: field.scale.vmin, vmax: field.scale.vmax } : null;
  const stale = field?.stale_geometry === true;

  const counts = shown?.counts;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 0.75, p: 1 }}>

      {/* ── the one thing that must never scroll off ─────────────────────── */}
      <Box sx={{
        display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap',
        px: 1, py: 0.5, borderRadius: 1, bgcolor: 'rgba(59,130,246,0.10)',
        border: '1px solid #1d4ed8',
      }}>
        <Typography sx={{ fontSize: 11.5, fontWeight: 700, color: '#93c5fd' }}>
          {sectorDeg}° sector, {sector?.antiperiodic ? 'anti-periodic' : 'periodic'} — the machine is {nSectors}× this
          {panel !== 'geometry' || modelledHalfOnly ? `, and z ≥ 0 only (mirror plane at z = 0)` : ''}
        </Typography>
        <HelpTip title={`12 slots / 14 poles repeats every ${sectorDeg}°, and that half holds an odd number of poles, so a ${sectorDeg}° rotation maps the machine onto itself with every magnet reversed. The solver models one sector of the z ≥ 0 half; "repeat" and "mirror" below are viewing transforms of that one solved body, not extra physics.`} />
        {machine?.passport?.match?.matches === false && (
          <Chip size="small" color="warning" label="passport is for a different geometry"
            sx={{ height: 18, fontSize: 10 }} />
        )}
      </Box>

      {stale && (
        <Box sx={{
          display: 'flex', alignItems: 'center', gap: 0.5, px: 1.25, py: 0.75,
          borderRadius: 1, bgcolor: 'rgba(239,68,68,0.10)', border: '1px solid #b91c1c',
          color: '#f87171', fontSize: 12, fontWeight: 700,
        }}>
          ⚠ STALE — this field was solved for a DIFFERENT machine · re-solve
          <HelpTip title={`Solved for ${field?.fingerprint_solved}; the machine on screen is ${field?.fingerprint_live}.`} />
        </Box>
      )}

      {/* ── selector row ─────────────────────────────────────────────────── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <ToggleButtonGroup size="small" exclusive value={panel}
          onChange={(_, v) => v && setPanel(v)}>
          <ToggleButton value="geometry" sx={{ fontSize: 11, px: 1.5, py: 0.25 }}>Geometry</ToggleButton>
          <ToggleButton value="mesh" sx={{ fontSize: 11, px: 1.5, py: 0.25 }}>Mesh</ToggleButton>
          <ToggleButton value="fields" sx={{ fontSize: 11, px: 1.5, py: 0.25 }}>Fields</ToggleButton>
        </ToggleButtonGroup>

        {panel !== 'geometry' && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Typography sx={lbl}>mesh</Typography>
            <Select size="small" value={fidelity} onChange={(e) => setFidelity(String(e.target.value))}
              sx={{ fontSize: 11, height: 26 }}>
              {FIDELITIES.map((f) => (
                <MenuItem key={f} value={f} sx={{ fontSize: 11 }}>{f}</MenuItem>
              ))}
            </Select>
            <HelpTip title={machine ? Object.entries(machine.fidelities).map(([k, v]) => `${k}: h_gap ${v.h_gap} mm, h_solid ${v.h_solid} mm, P${v.order}, ${v.n_stack}+${v.n_cap} axial layers — ${v.note}`).join(' · ') : 'mesh size and element order'} />
          </Box>
        )}

        {panel === 'fields' && (
          <>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Typography sx={lbl}>show</Typography>
              <Select size="small" value={quantity} onChange={(e) => setQuantity(e.target.value as 'Bmag' | 'demag')}
                sx={{ fontSize: 11, height: 26 }}>
                <MenuItem value="Bmag" sx={{ fontSize: 11 }}>|B|</MenuItem>
                <MenuItem value="demag" sx={{ fontSize: 11 }}>demag H·M̂</MenuItem>
              </Select>
            </Box>
            <FormControlLabel sx={{ m: 0 }} control={
              <Switch size="small" checked={nonlinear} onChange={(e) => setNonlinear(e.target.checked)} />
            } label={<Typography sx={lbl}>nonlinear iron</Typography>} />
            {!nonlinear && (
              <Chip size="small" color="warning" sx={{ height: 18, fontSize: 10 }}
                label="linear iron short-circuits this rotor" />
            )}
            <HelpTip title="Leave this ON for a spoke-PM rotor. The pole pieces are joined by thin bridges whose whole job is to SATURATE; with linear iron they never do, they carry the magnet flux straight from pole to pole, and the gap field collapses — measured on this machine: the air-gap fundamental drops from 1.51 T to 0.056 T, unchanged by mesh size or box size. Linear iron is kept because it is cheap and its failure is instructive, not because it is a picture of the motor." />
            <FormControlLabel sx={{ m: 0 }} control={
              <Switch size="small" checked={showVectors} onChange={(e) => setShowVectors(e.target.checked)} />
            } label={<Typography sx={lbl}>B vectors</Typography>} />
          </>
        )}

        <Box sx={{ flex: 1 }} />

        <FormControlLabel sx={{ m: 0 }} control={
          <Switch size="small" checked={fullRing} onChange={(e) => setFullRing(e.target.checked)} />
        } label={<Typography sx={lbl}>repeat ×{nSectors}</Typography>} />
        <FormControlLabel sx={{ m: 0 }} control={
          <Switch size="small" checked={mirrorZ} onChange={(e) => setMirrorZ(e.target.checked)} />
        } label={<Typography sx={lbl}>mirror z</Typography>} />
        <HelpTip title="Viewing transforms of the ONE solved sector — the copies carry no information the solve did not have. |B| is identical in every copy by anti-periodicity and by the z = 0 mirror plane, which is why a magnitude may be mirrored; no signed component is drawn." />
      </Box>

      {/* ── cut planes and view switches ─────────────────────────────────── */}
      {panel !== 'geometry' ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 210 }}>
            <Typography sx={lbl}>cut z</Typography>
            <Slider size="small" min={0} max={Math.round(zBox)} step={0.25}
              value={cutZ ?? Math.round(zBox)}
              onChange={(_, v) => setCutZ(Number(v) >= Math.round(zBox) ? null : Number(v))}
              sx={{ width: 110 }} />
            <Typography sx={val}>{cutZ === null ? 'off' : `${cutZ} mm`}</Typography>
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 210 }}>
            <Typography sx={lbl}>cut θ</Typography>
            <Slider size="small" min={0} max={sectorDeg} step={5}
              value={cutTheta ?? sectorDeg}
              onChange={(_, v) => setCutTheta(Number(v) >= sectorDeg ? null : Number(v))}
              sx={{ width: 110 }} />
            <Typography sx={val}>{cutTheta === null ? 'off' : `${cutTheta}°`}</Typography>
          </Box>
          <HelpTip title="A cut drops whole tets whose centroid is on the far side, so the exposed face is jagged by exactly one element — nothing is re-meshed or interpolated to make it flat." />
          {panel === 'mesh' && (
            <>
              <FormControlLabel sx={{ m: 0 }} control={
                <Switch size="small" checked={wireframe} onChange={(e) => setWireframe(e.target.checked)} />
              } label={<Typography sx={lbl}>edges</Typography>} />
              <FormControlLabel sx={{ m: 0 }} control={
                <Switch size="small" checked={showAir} onChange={(e) => setShowAir(e.target.checked)} />
              } label={<Typography sx={lbl}>air</Typography>} />
              <HelpTip title="Air is off by default: the air box is 40–160 mm of nothing and drawn whole it hides the machine. Switched on, only the air within 1.6 × the stator radius is drawn." />
            </>
          )}
        </Box>
      ) : (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <FormControlLabel sx={{ m: 0 }} control={
            <Switch size="small" checked={showCoils} onChange={(e) => setShowCoils(e.target.checked)} />
          } label={<Typography sx={lbl}>conductors</Typography>} />
          <FormControlLabel sx={{ m: 0 }} control={
            <Switch size="small" checked={showEndTurns} onChange={(e) => setShowEndTurns(e.target.checked)} />
          } label={<Typography sx={lbl}>end-turn band{geom ? ` (${geom.coils.end_turn_band_mm.toFixed(1)} mm)` : ''}</Typography>} />
          <HelpTip title="The winding is modelled as a source field T, not as a swept solid: what is real is the copper cross-section and the axial BAND the end turns occupy (h_ew = tooth_width/2 + wire_width/2 — the same length the 2D copper-loss model has always charged for). A bent-wire bundle would be an illustration, not the model." />
          <FormControlLabel sx={{ m: 0 }} control={
            <Switch size="small" checked={showArrows} onChange={(e) => setShowArrows(e.target.checked)} />
          } label={<Typography sx={lbl}>magnetisation</Typography>} />
          <HelpTip title="Spoke PM: M is TANGENTIAL and alternates pole to pole. Each arrow is that magnet's own M vector as fem_solver_2d.build_materials set it." />
          <FormControlLabel sx={{ m: 0 }} control={
            <Switch size="small" checked={modelledHalfOnly} onChange={(e) => setModelledHalfOnly(e.target.checked)} />
          } label={<Typography sx={lbl}>modelled half only</Typography>} />
          <FormControlLabel sx={{ m: 0 }} control={
            <Switch size="small" checked={showGrid} onChange={(e) => setShowGrid(e.target.checked)} />
          } label={<Typography sx={lbl}>grid</Typography>} />
        </Box>
      )}

      {err && (
        <Alert severity="error" sx={{ fontSize: 11, py: 0 }}>{err}</Alert>
      )}

      {/* ── the offer to solve ───────────────────────────────────────────── */}
      {panel === 'fields' && offer && !prog?.running && (
        <Alert severity="info" sx={{ fontSize: 11.5, py: 0.25, alignItems: 'center' }}
          action={
            <Button size="small" variant="contained" onClick={runSolve} sx={{ fontSize: 11 }}>
              Solve ({quote(offer.quote_s)})
            </Button>
          }>
          No solve cached for this machine at {offer.fidelity} / {nonlinear ? 'nonlinear' : 'linear'} iron
          <HelpTip title={`${offer.quote_basis ?? ''} — ${offer.quote_note}. Magnet-only (I = 0), total scalar potential, one anti-periodic sector.`} />
        </Alert>
      )}

      {prog?.running && (
        <Box sx={{ px: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography sx={{ fontSize: 11, color: 'var(--text-2)' }}>
              solving — {prog.phase} · {Math.round(prog.elapsed_s)}s of ~{quote(prog.quote_s)}
            </Typography>
            <Button size="small" onClick={() => { cancelSolve(); }} sx={{ fontSize: 10 }}>cancel</Button>
          </Box>
          <LinearProgress variant="determinate" value={Math.round((prog.progress || 0) * 100)} />
        </Box>
      )}
      {prog && !prog.running && prog.phase === 'error' && (
        <Alert severity="error" sx={{ fontSize: 11, py: 0 }}>solve failed: {prog.error}</Alert>
      )}

      {/* ── the canvas ───────────────────────────────────────────────────── */}
      <Box sx={{ position: 'relative', flex: 1, minHeight: 320, border: '1px solid var(--line)', borderRadius: 1, overflow: 'hidden' }}>
        <Static3DScene
          geom={panel === 'geometry' ? geom : null}
          surface={shown}
          scale={scale}
          vectors={panel === 'fields' && showVectors && field?.vectors ? field.vectors : null}
          wireframe={panel === 'mesh' && wireframe}
          showCoils={showCoils}
          showEndTurns={showEndTurns}
          showMagnetArrows={showArrows}
          fullRing={fullRing}
          mirrorZ={mirrorZ}
          modelledHalfOnly={modelledHalfOnly}
          sectorDeg={sectorDeg}
          nSectors={nSectors}
          antiperiodic={sector?.antiperiodic ?? true}
          radiusMm={radius}
          showGrid={showGrid}
        />
        <Viewcube />
        {busy && (
          <Box sx={{
            position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
            justifyContent: 'center', gap: 1, bgcolor: 'rgba(6,13,23,0.65)', zIndex: 5,
          }}>
            <CircularProgress size={26} />
            <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)' }}>
              {panel === 'mesh' ? 'building the tet mesh (gmsh) — first time only' : 'loading'}
            </Typography>
          </Box>
        )}
      </Box>

      {/* ── the status line: what is on screen, in numbers ───────────────── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', px: 0.5 }}>
        {sector && (
          <Stat k="machine" v={`${sector.num_slots}s/${sector.num_poles}p · OD ${sector.stator_od_mm.toFixed(1)} · stack ${sector.stack_mm} mm`}
            tip={`gap ${sector.air_gap_mm.toFixed(2)} mm, Br ${sector.Br_T} T, magnet ${sector.materials?.magnet ?? '?'} — ${machine?.materials_note ?? ''}`} />
        )}
        {panel === 'geometry' && geom && (
          <Stat k="bodies" v={`${geom.regions.length} regions · ${geom.coils.n_sides_sector} of ${geom.coils.n_sides_full_ring} conductors`}
            tip={`the CAD cross-section clipped to the sector — the same polygons the 2D solver meshes. ${geom.coils.note}`} />
        )}
        {counts && (
          <>
            <Stat k="volume mesh" v={`${counts.tets.toLocaleString()} tets · ${counts.nodes.toLocaleString()} nodes`}
              tip={`${counts.tets_solid.toLocaleString()} solid, ${counts.tets_air.toLocaleString()} air. Cross-section ${counts.n_tri_2d} triangles × ${counts.axial_layers} axial layers. This is the MODEL; the picture is its surface.`} />
            <Stat k="drawn" v={`${shown!.faces_shown.toLocaleString()} of ${shown!.faces_total.toLocaleString()} surface faces`}
              tip="surface = material interfaces and the cut planes. Interior faces between two tets of the same region are never shipped." />
            {shown!.decimated && (
              <Chip size="small" color="warning" sx={{ height: 18, fontSize: 10 }}
                label={`decimated to ${shown!.max_tris.toLocaleString()}`} />
            )}
          </>
        )}
        {panel === 'fields' && field && (
          <>
            <Stat k="solve" v={`${field.solve.iron.split('—')[0].trim()} · P${field.solve.element_order} · ${field.solve.ndofs.toLocaleString()} dofs`}
              tip={`${field.solve.excitation}, ${field.solve.formulation}. ${field.solve.iron}. Picard ${field.solve.picard_converged ? 'converged' : 'DID NOT converge'} in ${field.solve.picard_iterations ?? '—'} of ${field.solve.picard_max_iter ?? '—'} sweeps at residual ${field.solve.picard_residual ?? '—'} (tol ${field.solve.picard_tol ?? '—'}). ${field.solve.tets.toLocaleString()} tets, boundary flux ${field.solve.boundary_flux_Wb?.toExponential(2)} Wb (must be ~0). Wall ${quote(field.solve.wall_s)}, solved ${field.solved_utc}.`} />
            {field.solve.picard_converged === false && (
              <Chip size="small" color="error" sx={{ height: 18, fontSize: 10 }} label="Picard did not converge" />
            )}
            <ColorBar vmin={field.scale.vmin} vmax={field.scale.vmax}
              unit={field.scale.unit} note={`${field.scale.note} Range is the ${field.scale.clip}, so the tail is clipped rather than allowed to flatten the picture.`} />
            {field.spill && (
              <Stat k="gap spill" v={`k_flux,self ${field.spill.k_flux_self.toFixed(4)}`}
                tip={`B1 at the mid-plane ${field.spill.B1_mid_T.toFixed(4)} T; the axial mean over the stack divided by it. The passport's value for this machine is ${machine?.passport?.k_flux_self ?? '—'}.`} />
            )}
            {field.demag_slices && (
              <Stat k="demag end/mid" v={`${(field.demag_slices.end_worst_H / 1e3).toFixed(0)} / ${(field.demag_slices.mid_worst_H / 1e3).toFixed(0)} kA/m`}
                tip={field.demag_slices.note} />
            )}
            {field.vectors && (
              <Stat k="vectors" v={`${field.vectors.shown} of ${field.vectors.total}`}
                tip="a decimated sketch of direction — the arrow count means nothing" />
            )}
          </>
        )}
        {panel === 'fields' && machine && (
          <>
            <Divider orientation="vertical" flexItem />
            <Stat k="passport" v={`k_flux ${machine.passport.k_flux?.toFixed(4)} · k_T ${machine.passport.k_T?.toFixed(4) ?? '—'} · k_L ${machine.passport.k_L?.toFixed(3) ?? '—'}`}
              tip={`config/end_effect_3d.json ${machine.passport.version}, generated ${machine.passport.generated_utc}. These are the MEASURED numbers this tab exists to let you look at; the picture above is a cheaper solve unless you chose the passport mesh.`} />
          </>
        )}
      </Box>
    </Box>
  );
};

export default Static3DPanel;

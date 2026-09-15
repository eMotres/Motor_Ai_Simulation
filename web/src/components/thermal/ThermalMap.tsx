/**
 * The Thermal tab's result view — the temperature map and the heat flux.
 *
 * A THIN HOST, exactly like `mechanical/StressMap`: it owns the toolbar state
 * that belongs to a thermal result (equalised vs linear colours, the flux
 * arrows), turns the payload into `FieldOutput`s through `thermalOutputs`, and
 * hands them to the same `common/FieldViewer` the Electromagnetic and Mechanical tabs
 * draw through — one camera, one banded shader, one colour bar, one part tree.
 * User 2026-09-06: "интерфейс должен быть единым для всех графиков —
 * электромагнитных, механических и термо".
 *
 * Nothing here computes physics.  The temperature, the flux and the component
 * maxima are the solver's; this file decides only how they are COLOURED and what
 * the one header line says.
 */
import React, { useMemo } from 'react';
import { Box, Button, Typography } from '@mui/material';

import FieldViewer from '../common/FieldViewer';
import { outputStub } from '../common/fieldOutput';
import type { FieldOutput } from '../common/fieldOutput';
import { solvedIn } from '../mechanical/SolveTimer';
import { THERMAL_MENU, geometryOutput, thermalOutputs } from './fieldAdapters';
import { fmt, fmtSecs, outerCooling } from './api';
import type { ThermView, ThermalField, ThermalMeshPayload } from './api';

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;

/** A toolbar button in the viewer's own ink — the same one the Loss view's
 *  log/lin and shared/per-material toggles use, so the two toolbars read as one
 *  control set rather than two designs. */
const ToggleBtn: React.FC<{
  on: boolean; title: string; onClick: () => void; children: React.ReactNode;
}> = ({ on, title, onClick, children }) => (
  <Button size="small" onClick={onClick} title={title}
    sx={{ color: on ? 'var(--text-0)' : 'var(--text-3)', fontSize: 10,
      textTransform: 'none', minWidth: 0, px: 1, border: '1px solid var(--line-soft)' }}>
    {children}
  </Button>
);

interface Props {
  res: ThermalField;
  /** which quantity is drawn.  Lifted into the store with the rest of the tab's
   *  state: leaving the tab unmounts the panel, and a view choice that resets on
   *  every return is the same complaint as a result that disappears. */
  view: ThermView;
  onView: (v: ThermView) => void;
  eqTemp: boolean;
  onEqTemp: (v: boolean) => void;
  showFlux: boolean;
  onShowFlux: (v: boolean) => void;
  /** "result is for a previous geometry" — printed on the viewer's own header
   *  too, not only on the panel's, because the picture is the thing being
   *  misread. */
  staleNote?: string | null;
  height?: number;
}

const ThermalMap: React.FC<Props> = ({
  res, view, onView, eqTemp, onEqTemp, showFlux, onShowFlux, staleNote,
  height = 460,
}) => {
  const outputs = useMemo<FieldOutput[]>(() => {
    const live = thermalOutputs(res, view, { eqTemp, showFlux });
    // The headline number of the SELECTED quantity, on the header line — one
    // line, tooltip for the rest (the project's no-walls-of-text rule).
    let statText = live.statText;
    let tip = live.tip;
    if (view === 'temp') {
      const w = res.components?.winding;
      statText = `hot-spot ${fmt(res.T_max, 0)} °C${statText ? ` · ${statText}` : ''}`;
      tip = `${tip}\n\nWinding peak ${fmt(w?.max, 0)} °C (mean ${fmt(w?.avg, 0)} °C); the whole model spans ${fmt(res.T_min, 0)}…${fmt(res.T_max, 0)} °C above a ${fmt(res.t_sink_c ?? res.ambient_temp, 0)} °C sink.`;
    } else {
      statText = `${fmt(res.P_loss_total_W, 0)} W leaving the machine${statText ? ` · ${statText}` : ''}`;
      tip = `${tip}\n\nEverything drawn here is carrying the ${fmt(res.P_loss_total_W, 0)} W of loss out to the housing: copper ${fmt(res.P_cu_W, 0)} W, iron ${fmt(res.P_fe_W, 0)} W, magnet eddy ${fmt(res.P_mag_eddy_W, 1)} W.`;
    }
    if (staleNote) statText = `${staleNote}${statText ? ` · ${statText}` : ''}`;
    const sel: FieldOutput = { ...live, statText, tip };
    return THERMAL_MENU.map(m => (m.id === view ? sel
      : outputStub(m.id, m.menuLabel, m.label, 'Thermal', { unit: m.unit, tip: m.tip })));
  }, [res, view, eqTemp, showFlux, staleNote]);

  const controls = (
    <>
      {view === 'temp' && (
        <ToggleBtn on={eqTemp} onClick={() => onEqTemp(!eqTemp)}
          title="Colour mapping. Equalised: the band edges are the temperature quantiles, so the whole palette is spent on the distribution that exists — the right default for a machine under steady cooling, which is one tight hot plateau. Linear: a true °C axis, where a colour step is a fixed number of degrees.">
          {eqTemp ? 'equalised' : 'linear'}
        </ToggleBtn>
      )}
      <ToggleBtn on={showFlux} onClick={() => onShowFlux(!showFlux)}
        title="Heat-flux arrows: q = −k∇T per element, drawn from the element centroid with the length scaled by |q|. They show which way the heat is actually LEAVING — a hot winding beside a dark slot wall means it is getting out somewhere else.">
        flux
      </ToggleBtn>
    </>
  );

  // The OUTER surface through the one reader, so a result cached before the
  // two-surface split (2026-09-07) still names its own boundary here.
  const outer = outerCooling(res.cooling);
  const inner = res.cooling?.inner;
  const gap = res.cooling?.gap;
  const flow = typeof outer.flow_lpm === 'number' ? outer.flow_lpm : null;
  const hOuter = outer.h_conv ?? res.h_conv;

  return (
    <FieldViewer
      outputs={outputs}
      selected={view}
      onSelect={(id) => onView(id as ThermView)}
      height={height}
      controls={controls}
      /* …and how long it took (user 2026-09-06: "нужно добавить ещё индикатор
         времени расчёта").  Backend-measured, so it is the solve and not this
         browser's network or the JSON of the field payload. */
      contextLabel={
        `${(res.n_triangles ?? res.triangles.length).toLocaleString()} tri`
        + ` · sink ${fmt(outer.t_sink_c ?? res.t_sink_c ?? res.ambient_temp, 0)} °C`
        + ` · h ${fmt(hOuter, 0)} W/m²K`
        + `${inner && inner.mode && inner.mode !== 'none'
            ? ` · bore h ${fmt(inner.h_conv, 0)}` : ''}`
        + `${solvedIn(res) ? ` · ${solvedIn(res)}` : ''}`}
      contextTip={`Steady-state conduction over the solids alone: the outer air and the gap are not in this mesh, and the gap is represented by an effective conductivity (${fmt(gap?.k_eff ?? res.gap_k, 3)} W/m·K, computed from the gap width and the rotor speed) exactly as the slot is (${fmt(res.slot_k, 2)} W/m·K for the impregnated bundle). Driven by ${fmt(res.P_loss_total_W, 0)} W of loss — copper ${fmt(res.P_cu_W, 0)} W, iron ${fmt(res.P_fe_W, 0)} W, magnet eddy ${fmt(res.P_mag_eddy_W, 1)} W — against a ${fmt(hOuter, 0)} W/m²K film on the outer surface pulling toward ${fmt(outer.t_sink_c ?? res.t_sink_c ?? res.ambient_temp, 0)} °C${outer.mode ? ` (${String(outer.mode)}${outer.fluid ? `, ${String(outer.fluid)}` : ''})` : ''}${flow !== null ? `, fed ${flow >= 1 ? `${flow.toFixed(2)} L/min` : `${(flow * 1000).toFixed(0)} mL/min`} in at ${fmt(outer.t_in_c, 1)} °C and returning at ${fmt(outer.t_out_c, 1)} °C` : ''}${inner && inner.mode && inner.mode !== 'none' ? `, plus a ${fmt(inner.h_conv, 0)} W/m²K film in the rotor bore (${String(inner.mode)}) taking ${fmt(inner.heat_removed_W, 0)} W straight off the rotor` : ''}.`}
      placeholder={
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          no field in this result — press Solve
        </Typography>}
      fitKey={res.geometry_fingerprint ?? undefined}
    />
  );
};

/**
 * The same viewer with nothing solved in it — just the cross-section.
 *
 * Deliberately the SAME `FieldViewer` and not a lighter preview widget: the
 * camera, the Fit, the Mesh toggle and the part tree are the ones the user keeps
 * using after pressing Solve, so the picture must not jump when the field
 * arrives — it fills in.
 */
export const GeometryMap: React.FC<{
  mesh: ThermalMeshPayload | null;
  busy?: boolean;
  error?: string | null;
  height?: number;
  actions?: React.ReactNode;
}> = ({ mesh, busy, error, height = 460, actions }) => {
  const out = useMemo(() => geometryOutput(mesh), [mesh]);
  return (
    <FieldViewer
      outputs={[out]} selected={out.id} onSelect={() => { /* single output */ }}
      height={height} busy={busy} error={error} actions={actions}
      contextLabel={mesh
        ? `${mesh.n_triangles.toLocaleString()} tri · mesh ${mesh.mesh_size_mm} mm`
          + `${mesh.mesh_s ? ` · built in ${fmtSecs(mesh.mesh_s)}` : ''}`
          + ' · nothing solved yet'
        : 'building the cross-section…'}
      contextTip="The solids the conduction solve runs on — stator, coils, rotor, magnets, shaft and sleeve — meshed by the same mesher the solve uses, with the air dropped (there is nothing to conduct through in it). Press Solve to fill this with a temperature."
      placeholder={
        <Box sx={{ textAlign: 'center' }}>
          {/* Say what actually failed: "check the geometry" sends the user to
              the wrong place when the truth is an API that has not been
              restarted since the route was added. */}
          <Typography sx={{ ...lbl }}>
            {error
              ? `cross-section not loaded — ${error}`
              : busy ? 'building the cross-section…'
              : 'cross-section not loaded — the API did not return the thermal mesh (restart the API if the route is new)'}
          </Typography>
        </Box>} />
  );
};

export default ThermalMap;

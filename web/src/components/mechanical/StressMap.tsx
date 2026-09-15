/** The Mechanical tab's result view — displacement, von Mises, the principal
 *  and hoop / radial stresses, and the safety factor.
 *
 * 2026-09-05 this mounted THREE static canvases side by side ("рисунок со всеми
 * деформациями и напряжениями и safety factor, то есть три рисунка, чтобы понять
 * всё"), each with its own legend and no zoom at all.
 *
 * 2026-09-06 the user asked for the opposite of three pictures: "сделай наш
 * интерфейс для просмотра, чтобы можно было приближать и удалять; нужно сделать
 * одну картинку и меню для переключения выводов графиков; интерфейс должен быть
 * единым для всех графиков — электромагнитных, механических и термо".  So this
 * file is now a THIN HOST: it owns the toolbar state that belongs to a
 * mechanical result (case, deformation factor, safety-factor bands, contact
 * overlay), turns the payload into `FieldOutput`s through `mechOutputs`, and
 * hands them to the same `common/FieldViewer` the Electromagnetic tab uses.
 *
 * The safety factor is still NOT computed here.  `strength / stress` is only a
 * safety factor once you know which strength and which stress, and that is per
 * part — the backend does the division and sends `sf_per_tri`.
 */
import React, { useMemo } from 'react';
import {
  Box, Checkbox, FormControlLabel, MenuItem, Select, TextField, Tooltip, Typography,
} from '@mui/material';

import FieldViewer from '../common/FieldViewer';
import { outputStub } from '../common/fieldOutput';
import type { FieldOutput } from '../common/fieldOutput';
import { solvedIn } from './SolveTimer';
import { MECH_MENU, geometryOutput, mechOutputs } from './fieldAdapters';
import type { MechView } from './fieldAdapters';
import { caseKeys, fmt, fmtSecs } from './api';
import type { CaseName, MechMeshPayload, RotorStress } from './api';

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;

interface Props {
  res: RotorStress;
  caseName: CaseName;
  onCase: (c: CaseName) => void;
  /** which quantity is drawn.  Lifted out of this component 2026-09-06 with the
   *  rest of the tab's state: leaving the tab unmounts the panel, and a view
   *  choice that resets to Von Mises on every return is the same complaint as a
   *  result that disappears. */
  view: MechView;
  onView: (v: MechView) => void;
  /** free text so "auto" stays a legal value (0/NaN = auto-size the factor) */
  exagg: string;
  onExagg: (v: string) => void;
  contacts: boolean;
  onContacts: (v: boolean) => void;
  sfLow: string;
  onSfLow: (v: string) => void;
  sfHigh: string;
  onSfHigh: (v: string) => void;
  /** "result is for a previous geometry" — printed on the viewer's own header
   *  too, not only on the panel's, because the picture is the thing being
   *  misread (user 2026-09-06: "нужно подсвечивать неактуальность текущего
   *  расчёта") */
  staleNote?: string | null;
  height?: number;
}

const StressMap: React.FC<Props> = ({
  res, caseName, onCase, view, onView, exagg, onExagg, contacts, onContacts,
  sfLow, onSfLow, sfHigh, onSfHigh, staleNote, height = 460,
}) => {
  const c = res.cases[caseName];
  const low = Number(sfLow) || 0;
  const high = Number(sfHigh) || 0;
  /** The cases this RESULT has — one, named by its speed, in single-speed mode
   *  (user 2026-09-06: "проще будет считать только одну величину"), the three
   *  otherwise.  Read off the answer, never assumed. */
  const cases = caseKeys(res);
  const single = res.case_mode === 'single';

  const outputs = useMemo<FieldOutput[]>(() => {
    const live = mechOutputs(res.field ?? null, caseName, view, {
      exaggeration: Number(exagg), sfLow: low, sfHigh: high, contacts,
    });
    // The headline number of the SELECTED quantity, on the header line where
    // the three MapHead captions used to be — one line, tooltip for the rest.
    let statText = live.statText;
    let tip = live.tip;
    if (view === 'disp') {
      statText = `case max ${fmt(c.max_displacement_um, 2)} µm${statText ? ` · ${statText}` : ''}`;
      tip = `${tip}\n\nRotor OD growth is ${fmt(c.rotor_od_growth_um, 2)} µm — that number comes straight off the air gap.`;
    } else if (view === 'vm') {
      const peak = Object.entries(c.parts)
        .reduce((a, [n, p]) => (p.von_mises_max_mpa > a[1] ? [n, p.von_mises_max_mpa] as [string, number] : a),
          ['', 0] as [string, number]);
      const p995 = Math.max(...Object.values(c.parts).map((p) => p.von_mises_p995_mpa));
      statText = `case max ${fmt(peak[1], 1)} MPa${statText ? ` · ${statText}` : ''}`;
      tip = `${tip}\n\nPeak von Mises is in the ${peak[0] || '—'}; p99.5 over all parts is ${fmt(p995, 1)} MPa.`;
    } else if (view === 'sf') {
      const crit = c.sf_min_p05_part ? res.materials[c.sf_min_p05_part]?.sf_criterion : '';
      statText = `min ${fmt(c.sf_min_p05, 3)}${c.sf_min_p05_part ? ` (${c.sf_min_p05_part})` : ''}`;
      tip = `${tip}\n\n5th percentile of the per-element safety factor — the field with the singular corner elements removed. Raw minimum ${fmt(c.sf_min, 3)} in the ${c.sf_min_part ?? '—'}. Criterion for that part: ${crit || '—'}.`;
    }
    if (staleNote) statText = `${staleNote}${statText ? ` · ${statText}` : ''}`;
    const sel: FieldOutput = { ...live, statText, tip };
    return MECH_MENU.map(m => (m.id === view ? sel
      : outputStub(m.id, m.menuLabel, m.label, 'Mechanical', { unit: m.unit, tip: m.tip })));
  }, [res, caseName, view, exagg, low, high, contacts, c, staleNote]);

  const controls = (
    <>
      {/* One solved speed = nothing to choose: the selector only appears when
          a result carries several cases (user 2026-09-08: "убери эту надпись,
          она не нужна" — a one-item "23,000 rpm" dropdown). */}
      {!single && (
        <Tooltip title={'Which load case to read. Every quantity below is that case\'s field.'}>
          <Select size="small" value={caseName} onChange={(e) => onCase(e.target.value as CaseName)}
            sx={{ fontSize: 12, height: 30 }}>
            {cases.map((cn) => (
              <MenuItem key={cn} value={cn} sx={{ fontSize: 12 }}>
                {`${cn} · ${Math.round(res.cases[cn].rpm).toLocaleString()} rpm`}
              </MenuItem>
            ))}
          </Select>
        </Tooltip>
      )}
      {view === 'disp' && (
        <Tooltip title="Deformation is microns on a 100 mm part — at true scale the deformed shape is pixel-identical to the original, so it is drawn exaggerated. 'auto' sizes the factor so the largest displacement reads as 5 % of the rotor radius; type a number to fix it. The dashed outline is always the undeformed metal.">
          <TextField label="deform ×" size="small" value={exagg}
            onChange={(e) => onExagg(e.target.value)} sx={{ width: 90 }}
            inputProps={{ style: { fontSize: 12 } }} InputLabelProps={{ style: { fontSize: 12 } }} />
        </Tooltip>
      )}
      <Tooltip title="Overlay the contact state on the map: green where the surfaces are still pressed together, red where they have opened, amber where the facet is partly open.">
        <FormControlLabel
          control={<Checkbox size="small" checked={contacts} sx={{ p: 0.5 }}
            onChange={(e) => onContacts(e.target.checked)} />}
          label={<Typography sx={lbl}>contacts</Typography>}
          sx={{ ml: 0, mr: 0 }} />
      </Tooltip>
      {view === 'sf' && (
        <Tooltip title="Safety-factor bands, the way Fusion draws them: below the first value red ('below target'), between the two green ('in range'), above the second blue ('above target'). LEAVE THEM EMPTY and the map picks them itself — the fixed 2 / 4 acceptance bands while any element is within reach of the limit, and bands read off this rotor's own distribution (5th percentile and median) once nothing is anywhere near it, because a fixed 2 / 4 would paint such a rotor one flat blue. The colour bar's own caption says which of the two is in force.">
          <Box sx={{ display: 'flex', gap: 0.75, alignItems: 'center' }}>
            <Typography sx={lbl}>SF bands</Typography>
            <TextField label="low" size="small" value={sfLow} placeholder="auto"
              onChange={(e) => onSfLow(e.target.value)} sx={{ width: 72 }}
              inputProps={{ style: { fontSize: 12 } }} InputLabelProps={{ style: { fontSize: 12 } }} />
            <TextField label="high" size="small" value={sfHigh} placeholder="auto"
              onChange={(e) => onSfHigh(e.target.value)} sx={{ width: 72 }}
              inputProps={{ style: { fontSize: 12 } }} InputLabelProps={{ style: { fontSize: 12 } }} />
          </Box>
        </Tooltip>
      )}
    </>
  );

  return (
    <FieldViewer
      outputs={outputs}
      selected={view}
      onSelect={(id) => onView(id as MechView)}
      height={height}
      controls={controls}
      /* …and how long it took: user 2026-09-06, "нужно добавить ещё индикатор
         времени расчёта".  Backend-measured (`elapsed_s`), so it is the solve
         and not this browser's network. */
      contextLabel={
        (single ? `single speed · ${caseName}`
          : `${caseName} · ${Math.round(c.rpm).toLocaleString()} rpm`)
        + ` · ${res.mesh.n_triangles.toLocaleString()} tri · P${res.mesh.element_order}`
        + ` · mesh ${fmt(res.mesh.mesh_size_mm, 2)} mm`
        + `${solvedIn(res) ? ` · ${solvedIn(res)}` : ''}`}
      contextTip={`Contact solve: ${c.contact.converged ? 'converged' : 'NOT converged'} in ${c.contact.iterations} iterations, ${c.contact.n_bodies} bodies, max penetration ${fmt(c.contact.max_penetration_um, 2)} µm. Rigid-body residual ${res.mesh.rigid_residual.toExponential(1)}. Mesh ${fmt(res.mesh.mesh_size_mm, 2)} mm, ${res.mesh.n_contact_facets} contact facets over ${res.mesh.n_contact_pairs} pairs${res.mesh.mesh_s ? `, ${fmt(res.mesh.mesh_s, 1)} s to build${res.mesh.mesh_reused ? ' (reused, so this solve did not pay it)' : ''}` : ''}.`}
      placeholder={
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          no field in this result — press Solve
        </Typography>}
      fitKey={res.geo_fingerprint ?? undefined}
    />
  );
};

/**
 * The same viewer with nothing solved in it — just the rotor.
 *
 * User 2026-09-06: "если нет расчётов — рисуется просто геометрия".  Deliberately
 * the SAME `FieldViewer` and not a lighter preview widget: the camera, the Fit,
 * the Mesh toggle and the Part menu are the ones the user will keep using after
 * pressing Solve, so the picture must not jump when the field arrives.
 */
export const GeometryMap: React.FC<{
  mesh: MechMeshPayload | null;
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
      contextTip={'The rotor solids meshed by the same mesher the stress solve uses, with no fields on them. Press Solve to compute the stresses, the deformation and the safety factor of this cross-section.'
        + (mesh ? ` Edges ${fmt(mesh.min_edge_mm, 2)}…${fmt(mesh.max_edge_mm, 2)} mm at a ${fmt(mesh.mesh_size_mm, 2)} mm target — gmsh refines below the target on curvature, never above it.` : '')}
      placeholder={
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          {/* Say what actually failed: the section comes from the API's mesh
              route, and the one time this showed it was a server that had not
              been restarted after the route was added — "check the geometry"
              sent the user to the wrong place (2026-09-06: "почему нет?
              рисуй пустую геометрию"). */}
          {error
            ? `cross-section not loaded — ${error}`
            : busy ? 'building the cross-section…'
            : 'cross-section not loaded — the API did not return the rotor mesh (restart the API if the route is new)'}
        </Typography>} />
  );
};

export default StressMap;

/**
 * StoredFieldViewer - the READ-ONLY map of one computed point (owner 2026-10-26).
 *
 * The same viewer the Simulation / Thermal / Mechanical tabs draw through (`common/FieldViewer`, one camera, one banded
 * shader, one colour bar, one part tree) and the same adapters (`emOutputs`, `thermalOutputs`, `mechOutputs`), fed with the
 * map the server STORED for the duty instead of a fresh solve.  There is no settings panel and no run button here:
 * a view menu (|B| / loss / demag / temperature / stress ...), the maps, and one line with the simulated parameters
 * (operating point, mesh, solver, date).
 */
import React, { useMemo, useState } from 'react';
import { Box, Button, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import FieldViewer from '../common/FieldViewer';
import { outputStub } from '../common/fieldOutput';
import type { FieldOutput } from '../common/fieldOutput';
import { EM_MENU, classOf, emOutputs } from '../simulation/fieldView';
import { THERMAL_MENU, thermalOutputs } from '../thermal/fieldAdapters';
import { MECH_MENU, mechOutputs } from '../mechanical/fieldAdapters';
import type { MechView } from '../mechanical/fieldAdapters';
import { drawnExtent, isCropped, mapExtent, outlineEntries, outlineLoops } from '../../modules/development/storedFieldPayload';
import type { StoredField } from '../../modules/development/types';

interface Props {
  field: StoredField;
  /** the simulated parameters of this map (operating point, mesh, solver, date) - one line */
  contextLabel: string;
  height?: number;
}

type Anyish = Record<string, any>;   // eslint-disable-line @typescript-eslint/no-explicit-any

const StoredFieldViewer: React.FC<Props> = ({ field, contextLabel, height = 420 }) => {
  const { t } = useTranslation('development');
  const views = field.payload.views;
  const first = field.kind === 'rotor_stress' && views.includes('vm') ? 'vm' : views[0];
  const [chosen, setChosen] = useState<string>(first);
  const selected = views.includes(chosen) ? chosen : first;

  // the map is framed to the solved (coloured) part; one switch shows the whole motor (zoom and Fit work as everywhere)
  const [whole, setWhole] = useState(false);

  const built = useMemo<FieldOutput[]>(() => {
    const p = field.payload as Anyish;
    if (field.kind === 'em') {
      // air / gap / band borders are not outlines (same rule as fieldView.outlinesFromMesh), welded so a seam is not a border
      const outlines = p.outlines?.length ? p.outlines : outlineEntries(p.vertices, p.triangles, p.domain_per_tri,
        { groupOf: classOf, draw: (a, b) => a > 0 || (b !== undefined && b > 0) });
      const payload = { ...p, outlines };
      return EM_MENU.filter((m) => views.includes(m.id)).map((m) => (m.id === selected
        ? emOutputs(payload as never, m.id, {})
        : outputStub(m.id, m.menuLabel, m.label, 'Electromagnetic', { unit: m.unit, tip: m.tip })));
    }
    if (field.kind === 'thermal') {
      // the outlines stored with the field (the live tab's own) when the record has them, else the welded domain boundaries
      const payload = { ...p, outlines: p.outlines?.length ? p.outlines : outlineEntries(p.vertices, p.triangles, p.domain_per_tri) };
      return THERMAL_MENU.filter((m) => views.includes(m.id)).map((m) => (m.id === selected
        ? thermalOutputs(payload as never, m.id, { eqTemp: true, showFlux: false })
        : outputStub(m.id, m.menuLabel, m.label, 'Thermal', { unit: m.unit, tip: m.tip })));
    }
    const payload = { ...p, outlines: p.outlines?.length ? p.outlines : outlineLoops(p.vertices, p.triangles, p.domain_per_tri, 1) };
    return MECH_MENU.filter((m) => views.includes(m.id)).map((m) => (m.id === selected
      ? mechOutputs(payload as never, p.case as never, m.id as MechView, { contacts: false })
      : outputStub(m.id, m.menuLabel, m.label, 'Mechanical', { unit: m.unit, tip: m.tip })));
  }, [field, selected, views]);

  const crop = useMemo(() => {
    const out = built.find((o) => o.id === selected);
    const positions = (out?.geometry?.getAttribute('position') as { array?: ArrayLike<number> } | undefined)?.array;
    return drawnExtent(positions, out?.vertexValues);
  }, [built, selected]);
  const cropped = useMemo(() => isCropped(built.find((o) => o.id === selected)?.extent, crop), [built, selected, crop]);
  const outputs = useMemo<FieldOutput[]>(
    () => built.map((o) => (o.id === selected && o.geometry ? { ...o, extent: mapExtent(o.extent, crop, whole || !cropped) ?? o.extent } : o)),
    [built, selected, crop, whole, cropped]);

  return (
    <Box data-testid={`stored-field-${field.kind}`}>
      <FieldViewer
        outputs={outputs}
        selected={selected}
        onSelect={setChosen}
        height={height}
        contextLabel={contextLabel}
        fitKey={`${field.kind}:${field.duty}:${field.provenance.geometry_fingerprint ?? ''}:${whole && cropped ? 'whole' : 'part'}`}
        actions={cropped ? (
          <Tooltip title={t('points.fields.wholeTip')}>
            <Button size="small" aria-pressed={whole} onClick={() => setWhole((v) => !v)} data-testid="stored-field-whole"
              sx={{ color: whole ? 'var(--text-0)' : '#93c5fd', fontSize: 10, textTransform: 'none', minWidth: 0, px: 1,
                border: `1px solid ${whole ? '#93c5fd' : 'var(--line-soft)'}` }}>
              {t('points.fields.whole')}
            </Button>
          </Tooltip>) : undefined}
      />
    </Box>
  );
};

export default StoredFieldViewer;

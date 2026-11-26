/**
 * PointFieldPane - the standard stored FIELD view of one project-motor point (owner 2026-11-26: «справа от таблицы наш стандартный
 * вывод полей»).  It is the SAME read-only viewer the Points-limited Configure uses (compare/StoredFieldViewer) with the same kind
 * switch and the same availability rule (`storedFieldKinds`: a map solved at another speed than the point's is never offered, and the
 * tooltip of the disabled kind says why), fed by the project's own GET routes (`/motors/view` for the kinds, `/motors/field` for the
 * map).  Nothing is solved or written.
 */
import React, { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { Box, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { developmentApi } from '../../modules/development/api';
import { FIELD_KIND_ORDER, isNotComputed, otherPointFields, provenanceLine, storedFieldKinds } from '../../modules/development/pointsLimited';
import type { FieldKind, MotorView, StoredField } from '../../modules/development/types';
import { watchChunkImport } from '../../lib/chunkReload';
import { PANEL, LABEL } from '../common/ResultTiles';

const StoredFieldViewer = lazy(watchChunkImport(() => import('../compare/StoredFieldViewer')));

interface Props { projectId: string; die: string; config: string; duty: string; showAll?: boolean }

const PointFieldPane: React.FC<Props> = ({ projectId, die, config, duty, showAll = false }) => {
  const { t } = useTranslation('development');
  const [view, setView] = useState<MotorView | null>(null);
  const [kind, setKind] = useState<FieldKind | null>(null);
  const [field, setField] = useState<{ key: string; value: StoredField | null; error: string; missing?: boolean } | null>(null);
  const cache = useRef(new Map<string, StoredField>());

  useEffect(() => {
    let cancelled = false;
    cache.current.clear(); setView(null); setField(null); setKind(null);
    void developmentApi.motorView(projectId, die, config, showAll)
      .then((v) => { if (!cancelled) setView(v); })
      .catch(() => { if (!cancelled) setView(null); });
    return () => { cancelled = true; };
  }, [projectId, die, config, showAll]);

  const current = view?.duties?.find((d) => d.duty === duty) ?? null;
  const kinds = storedFieldKinds(current);
  const otherPoint = otherPointFields(current);
  const activeKind: FieldKind | null = kind && kinds.includes(kind) ? kind : (kinds[0] ?? null);

  useEffect(() => {
    if (!current || !activeKind) { setField(null); return; }
    const key = `${die}\u0000${config}\u0000${duty}\u0000${activeKind}`;
    const hit = cache.current.get(key);
    if (hit) { setField({ key, value: hit, error: '' }); return; }
    let cancelled = false;
    setField({ key, value: null, error: '' });
    developmentApi.motorField(projectId, die, config, duty, activeKind, showAll)
      .then((value) => { if (!cancelled) { cache.current.set(key, value); setField({ key, value, error: '' }); } })
      .catch((e) => { if (!cancelled) setField({ key, value: null, error: isNotComputed(e) ? '' : (e instanceof Error ? e.message : String(e)), missing: isNotComputed(e) }); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, die, config, duty, activeKind, showAll, current?.duty]);

  const label = field?.value ? provenanceLine(field.value.provenance) : '';
  const missingNote = view && kinds.length === 0
    ? (FIELD_KIND_ORDER.map((k) => otherPoint[k]).find(Boolean) ? t('points.fields.otherPoint', FIELD_KIND_ORDER.map((k) => otherPoint[k]).find(Boolean)!) : t('points.notComputed'))
    : '';

  return (
    <Box sx={{ ...PANEL, p: 1, display: 'flex', flexDirection: 'column', gap: 0.75, minWidth: 0 }} data-testid="point-field-pane">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={LABEL}>{t('points.fields.title')}</Typography>
        <ToggleButtonGroup exclusive size="small" value={activeKind} aria-label={t('points.fields.title')}
          onChange={(_, value) => { if (value) setKind(value as FieldKind); }}>
          {FIELD_KIND_ORDER.map((k) => (
            <ToggleButton key={k} value={k} disabled={!kinds.includes(k)} sx={{ textTransform: 'none', px: 1.2, py: 0.3, fontSize: 12, '&.Mui-disabled': { pointerEvents: 'auto' } }}>
              <Tooltip title={kinds.includes(k) ? t(`points.fields.tip.${k}`) : otherPoint[k] ? t('points.fields.otherPoint', otherPoint[k]) : t('points.notComputed')}><span>{t(`points.fields.kind.${k}`)}</span></Tooltip>
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Box>
      {missingNote && <Typography role="status" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{missingNote}</Typography>}
      {field?.error && <Typography role="alert" sx={{ fontSize: 12, color: 'error.main' }}>{t('points.fields.loadFailed')}: {field.error}</Typography>}
      {field && !field.value && !field.error && !field.missing && <Typography role="status" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.loading')}</Typography>}
      {field?.value && (
        <Suspense fallback={<Typography role="status" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('points.loading')}</Typography>}>
          <StoredFieldViewer key={field.key} field={field.value} contextLabel={label} />
        </Suspense>
      )}
    </Box>
  );
};

export default PointFieldPane;

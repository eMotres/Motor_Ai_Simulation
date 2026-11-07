/**
 * The loaded point's OWN stored map, or an honest empty state - the Thermal and Mechanical tabs' picture while they hold no
 * fresh solve of the loaded point (owner 2026-11-04).
 *
 *   ready        -> the point's stored map in the read-only viewer, labelled as stored (a map: the tables and tiles come with
 *                   the next Solve);
 *   anything else -> one line saying why there is no stored map (nothing computed / an older build / unreadable) and the
 *                   tab's own cross-section (`children`) underneath, exactly as an unsolved tab always drew it.
 *
 * Never another motor's result: the stored file is looked up by the loaded point's identity and checked against the loaded
 * machine's fingerprint before it gets here (`lib/storedPointResult`).  Nothing is solved and nothing is written.
 */
import React, { Suspense, lazy, useMemo } from 'react';
import { Box, Typography } from '@mui/material';

import { watchChunkImport } from '../../lib/chunkReload';
import { storedPointLine } from '../../lib/storedPointResult';
import type { StoredPoint } from '../../lib/storedPointResult';
import { provenanceLine } from '../../modules/development/pointsLimited';
import type { StoredField } from '../../modules/development/types';
import { tileFullRing } from '../thermal/types';
import type { ThermalPayload } from '../thermal/types';

// the viewer is three.js: fetched when a stored map is opened, not with the tab
const StoredFieldViewer = lazy(watchChunkImport(() => import('../compare/StoredFieldViewer')));

const lbl = { fontSize: 11, color: 'var(--text-3)' } as const;

/** A stored thermal answer is the SECTOR the solver meshed (the live tab tiles it on arrival); tile it the same way. */
function tiled(field: StoredField): StoredField {
  if (field.kind !== 'thermal') return field;
  const n = Number((field.payload as { n_sectors?: unknown }).n_sectors ?? 1);
  if (!(n > 1)) return field;
  const full = tileFullRing(field.payload as unknown as ThermalPayload);
  return { ...field, payload: { ...(full as unknown as Record<string, unknown>), views: field.payload.views } };
}

interface Props {
  stored: StoredPoint;
  what: 'thermal' | 'mechanical';
  /** what the tab draws when there is no stored map: its own cross-section */
  children: React.ReactNode;
}

const StoredPointMap: React.FC<Props> = ({ stored, what, children }) => {
  const field = stored.status === 'ready' ? stored.field : null;
  const shown = useMemo(() => (field ? tiled(field) : null), [field]);
  if (shown && field) {
    const when = provenanceLine(field.provenance);
    return (
      <Box data-testid={`stored-point-${what}`}>
        <Typography sx={{ ...lbl, mb: 0.75, display: 'block' }}>
          Stored result of this point (the map only) - press Solve for the {what === 'thermal' ? 'component temperatures and the heat budget' : 'stresses by part and the safety factors'}
        </Typography>
        <Suspense fallback={<Typography sx={lbl}>loading the stored map…</Typography>}>
          <StoredFieldViewer key={`${stored.key}\u0000${field.kind}`} field={shown} contextLabel={`stored · ${when}`} />
        </Suspense>
      </Box>
    );
  }
  const line = storedPointLine(stored, what);
  return (
    <Box data-testid={`stored-point-${what}-empty`}>
      {line && <Typography sx={{ ...lbl, mb: 0.75, display: 'block' }}>{line}</Typography>}
      {children}
    </Box>
  );
};

export default StoredPointMap;

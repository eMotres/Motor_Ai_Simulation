import { useEffect, useState } from 'react';
import { Box, Checkbox, FormControlLabel, Stack, Typography } from '@mui/material';
import { developmentApi } from '../../api';
import { acceptedOf, defaultAccepted, preferredBounds, sizeKey, sizeText, toggleSize, type AcceptedSize, type ExistingSize } from '../../odSizes';
import { specFieldLabelSx } from '../../specGrid';

/** 4_1 with "Can be chosen from existing sizes": the public existing sizes, closest to the preferred diameter first, and a tick for
 *  every size the customer accepts.  The first time the list arrives with nothing stored, all sizes within the maximum diameter are
 *  ticked (the customer unticks what he does not accept).  The one-line explanation is the label's tooltip. */
export default function OdSizesPicker({ projectId, preferred, maxOd, value, onChange, disabled, tr }: {
  projectId?: string; preferred: unknown; maxOd?: number; value: unknown; onChange: (next: AcceptedSize[]) => void; disabled: boolean;
  tr: (key: string, args?: Record<string, unknown>) => string;
}) {
  const [sizes, setSizes] = useState<ExistingSize[] | null>(null);
  const bounds = preferredBounds(preferred);
  const accepted = acceptedOf(value);
  useEffect(() => {
    if (!projectId) return undefined;
    let live = true;
    developmentApi.existingSizes(projectId, { preferredMin: bounds.min, preferredMax: bounds.max, max: maxOd })
      .then((a) => { if (live) setSizes(a.sizes ?? []); }).catch(() => { if (live) setSizes([]); });
    return () => { live = false; };
  }, [projectId, bounds.min, bounds.max, maxOd]);
  useEffect(() => {          // nothing stored yet: every listed size is accepted until the customer unticks one
    if (sizes && sizes.length && accepted.length === 0 && !disabled && !Array.isArray(value)) onChange(defaultAccepted(sizes, maxOd));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sizes]);
  const ticked = new Set(accepted.map(sizeKey));
  return <Stack spacing={0.3} data-testid="od-sizes">
    {sizes === null ? <Typography sx={specFieldLabelSx}>{tr('odSizes.loading')}</Typography>
      : sizes.length === 0 ? <Typography sx={specFieldLabelSx}>{tr('odSizes.none')}</Typography>
      : <Box>{sizes.map((s) => <FormControlLabel key={sizeKey(s)} sx={{ display: 'flex', m: 0 }}
          control={<Checkbox size="small" disabled={disabled} checked={ticked.has(sizeKey(s))} onChange={(e) => onChange(toggleSize(accepted, s, e.target.checked))} />}
          label={<Typography component="span" sx={{ fontSize: 13 }}>{sizeText(s, tr)}</Typography>} />)}</Box>}
  </Stack>;
}

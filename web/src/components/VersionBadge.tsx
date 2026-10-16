import React, { useEffect, useState } from 'react';
import { Chip, Tooltip } from '@mui/material';
import { APP_VERSION, APP_GIT_SHA, checkBackendVersion, type VersionCheck } from '../lib/version';

/**
 * Small version chip in the header. Shows the frontend build version and, after
 * checking GET /api/version, warns (amber ⚠) if the backend is on a different
 * MAJOR.MINOR — the "half-deployed release / version skew" failure mode.
 */
export const VersionBadge: React.FC = () => {
  const [chk, setChk] = useState<VersionCheck | null>(null);

  useEffect(() => {
    let alive = true;
    checkBackendVersion().then((c) => { if (alive) setChk(c); });
    return () => { alive = false; };
  }, []);

  const skew = chk?.skew ?? false;
  const tip = skew
    ? `Version mismatch — frontend v${chk?.frontend}, backend v${chk?.backend}. `
      + `Hard-reload (Ctrl+Shift+R) to get the matching frontend.`
    : `AeroStator Core v${APP_VERSION} (${APP_GIT_SHA})`
      + (chk?.backend ? ` · backend v${chk.backend}` : '');

  return (
    <Tooltip title={tip} arrow>
      <Chip
        label={skew ? `v${APP_VERSION} ⚠` : `v${APP_VERSION}`}
        size="small"
        variant="outlined"
        color={skew ? 'warning' : 'default'}
        sx={{ height: 20, fontSize: 11, opacity: skew ? 1 : 0.65, cursor: 'default' }}
      />
    </Tooltip>
  );
};

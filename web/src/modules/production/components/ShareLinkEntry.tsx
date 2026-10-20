/**
 * The door behind a shared documents link (/production/<die>/<config>/documents?...), for a SIGNED-IN visitor.
 *
 * Opening a link grants nothing: the server answers who the caller is for that production (`/access`).
 *   - member (or admin)  -> the workspace opens on the Production tab, on that page (children = <App />);
 *   - anybody else       -> one short line and "Request access"; no document, title or member name is ever shown here.
 * A signed-out visitor never gets here: `Root` shows the landing and `AuthContext` opens the sign-in dialog on the same URL.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Button, CircularProgress, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { productionApi } from '../api';
import type { ProductionLink } from '../../../lib/productionLink';
import type { ProductionAccess } from '../types';

type State = { kind: 'loading' } | { kind: 'error' } | { kind: 'member' } | { kind: 'none'; requested: boolean };

const ShareLinkEntry: React.FC<{ link: ProductionLink; children: React.ReactNode }> = ({ link, children }) => {
  const { t } = useTranslation('production');
  const [state, setState] = useState<State>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);

  const check = useCallback(() => {
    setState({ kind: 'loading' });
    productionApi.access(link.die, link.config)
      .then((a: ProductionAccess) => setState(a.access === 'member' ? { kind: 'member' } : { kind: 'none', requested: a.requested }))
      .catch(() => setState({ kind: 'error' }));
  }, [link.die, link.config]);
  useEffect(() => { check(); }, [check]);

  if (state.kind === 'member') return <>{children}</>;

  const request = () => {
    setBusy(true);
    productionApi.requestAccess(link.die, link.config)
      .then(() => setState({ kind: 'none', requested: true }))
      .catch(() => setState({ kind: 'error' }))
      .finally(() => setBusy(false));
  };

  return (
    <Box role="status" aria-live="polite" sx={{
      minHeight: '100vh', display: 'grid', placeItems: 'center', px: 2, color: '#44443f', background: '#f7f6f3',
      fontFamily: 'Inter, system-ui, sans-serif',
    }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, flexWrap: 'wrap', justifyContent: 'center', maxWidth: 560 }}>
        {state.kind === 'loading' && <><CircularProgress size={18} /><Typography sx={{ fontSize: 14 }}>{t('loading')}</Typography></>}
        {state.kind === 'error' && (
          <>
            <Typography sx={{ fontSize: 14 }}>{t('access.error')}</Typography>
            <Button size="small" variant="outlined" sx={{ textTransform: 'none' }} onClick={check}>{t('access.retry')}</Button>
          </>
        )}
        {state.kind === 'none' && (
          <>
            <Typography sx={{ fontSize: 14 }}>{state.requested ? t('access.requested') : t('access.none')}</Typography>
            {!state.requested && (
              <Button size="small" variant="contained" disabled={busy} sx={{ textTransform: 'none' }} onClick={request}>
                {t('access.request')}
              </Button>
            )}
            <HelpTip title={t('access.tip')} />
            <Button size="small" sx={{ textTransform: 'none' }} href="/">{t('access.home')}</Button>
          </>
        )}
      </Box>
    </Box>
  );
};

export default ShareLinkEntry;

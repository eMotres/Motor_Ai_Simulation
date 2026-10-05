/**
 * RefusedNotice - ONE line, top of the page, when the server's role gate refuses
 * a call (lib/apiAuth dispatches `api-refused`).  Title-row sized, no small
 * print, EN + full ZH (common:refused.*).  Replaces the silent failure a
 * standard account saw when something tried a route that is admin-only.
 */
import React, { useEffect, useState } from 'react';
import { Alert, Snackbar } from '@mui/material';
import { useTranslation } from 'react-i18next';

const RefusedNotice: React.FC = () => {
  const { t } = useTranslation('common');
  const [key, setKey] = useState<string | null>(null);
  useEffect(() => {
    const on = (e: Event) => {
      const k = (e as CustomEvent<{ key?: string }>).detail?.key;
      if (!k) return;
      setKey(k);
    };
    window.addEventListener('api-refused', on);
    return () => window.removeEventListener('api-refused', on);
  }, []);
  return (
    <Snackbar open={key !== null} autoHideDuration={9000} onClose={() => setKey(null)}
      anchorOrigin={{ vertical: 'top', horizontal: 'center' }}>
      <Alert severity="warning" variant="filled" onClose={() => setKey(null)}
        sx={{ fontSize: 14, fontWeight: 600, alignItems: 'center' }}>
        {key ? t(key) : ''}
      </Alert>
    </Snackbar>
  );
};

export default RefusedNotice;

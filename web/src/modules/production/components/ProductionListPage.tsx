/** The productions the caller may see (members; admins see all). Click a row to open it. */
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Button, Chip, CircularProgress, Paper, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { RevisionLabel } from '../../../components/common/MotorRevisionText';
import { useAuth } from '../../../contexts/AuthContext';
import BilingualText from './BilingualText';
import { productionApi } from '../api';
import { callerRole } from '../helpers';
import { productionUnreadKey, unreadLabel } from '../../../lib/unreadMessages';
import { useUnreadCounts } from '../../../lib/useUnreadMessages';
import type { Production } from '../types';
import { NARROW } from '../../../lib/narrowLayout';

const ProductionListPage: React.FC<{ onOpen: (p: Production) => void }> = ({ onOpen }) => {
  const { t } = useTranslation('production');
  const { user, isAdmin } = useAuth();
  const [items, setItems] = useState<Production[] | null>(null);
  const unreadCounts = useUnreadCounts();
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try { setItems(await productionApi.list()); }
    catch (e) { setErr(t('errors.load', { reason: e instanceof Error ? e.message : String(e) })); }
    finally { setLoading(false); }
  }, [t]);
  useEffect(() => { void load(); }, [load]);

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5 }}>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>{t('title')}</Typography>
        <HelpTip title={t('titleTip')} />
        <Box sx={{ flex: 1 }} />
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} disabled={loading} onClick={() => void load()}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>{t('refresh')}</Button>
      </Box>
      {loading && <Box sx={{ display: 'flex', gap: 1.5, color: 'var(--text-3)', py: 3 }}><CircularProgress size={18} /><Typography sx={{ fontSize: 13 }}>{t('loading')}</Typography></Box>}
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      {!loading && !err && items && items.length === 0 && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('none')}</Typography>
          <HelpTip title={t('noneTip')} />
        </Box>
      )}
      {!loading && !err && items && items.length > 0 && (
        <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'auto' }}>
          <Table size="small" sx={{
            /* Phone: keep the columns readable and scroll the table sideways (the Paper scrolls). */
            [NARROW]: { minWidth: 680 },
            '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
            '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
          }}>
            <TableHead>
              <TableRow>
                <TableCell>{t('col.motor')}</TableCell><TableCell>{t('col.title')}</TableCell>
                <TableCell align="right">{t('col.orderQty')}</TableCell><TableCell align="center">{t('col.revision')}</TableCell>
                <TableCell align="center">{t('col.status')}</TableCell><TableCell align="center">{t('col.role')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {items.map((p) => {
                const role = callerRole(p.members, user?.email ?? '', isAdmin);
                const unread = unreadCounts ? (unreadCounts.productions[productionUnreadKey(p.die, p.config)] ?? 0) : (p.unread_messages ?? 0);
                return (
                  <TableRow key={`${p.die}/${p.config}`} hover sx={{ cursor: 'pointer' }} onClick={() => onOpen(p)}>
                    <TableCell sx={{ color: 'var(--text-0)', fontWeight: 600 }}>{p.die} / {p.config}<RevisionLabel rev={p.product_revision?.rev} />{unread > 0 && <Tooltip title={t('unread.tip', { count: unread })}><Chip size="small" color="primary" label={unreadLabel(unread)} aria-label={t('unread.tip', { count: unread })} sx={{ ml: 1, height: 18, fontSize: 10.5, fontWeight: 700 }} /></Tooltip>}</TableCell>
                    <TableCell><BilingualText value={p.title} size={12.5} /></TableCell>
                    <TableCell align="right">{p.order_quantity}</TableCell>
                    <TableCell align="center">{p.current_revision}</TableCell>
                    <TableCell align="center"><Chip size="small" label={t(`status.${p.status}`)} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: 'var(--text-2)' }} /></TableCell>
                    <TableCell align="center" sx={{ color: 'var(--text-2)' }}>{role ? t(`role.${role}`) : ''}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Paper>
      )}
    </Box>
  );
};

export default ProductionListPage;

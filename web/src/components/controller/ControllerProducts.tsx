/**
 * ControllerProducts - the catalogue of COMPLETE commercial controllers (DTI HV-850, ...), read-only.
 *
 * Owner 2026-10-26: every controller of a project comes from this catalogue (the Development specification picks from
 * it).  It sits under the power-device catalogue in the Controller tab: one row per product and revision, the values the
 * manufacturer's document states - each with its page in a tooltip - and what the document does not state stays empty
 * ("-"), never estimated.  One value per cell (the worst / the rating point); the other bounds are in the tooltip.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Paper, Tooltip, Typography } from '@mui/material';
import HelpTip from '../common/HelpTip';
import { listControllerCatalog, type ControllerProduct } from './controllerApi';
import { nsT } from '../../i18n/nsT';
import { useTranslation } from 'react-i18next';
import { controllerCells, controllerDetails, type ProductCell } from './controllerProductsModel';

const tx = nsT('controller');
const EMPTY = '-';

const CellText: React.FC<{ cell: ProductCell }> = ({ cell }) => {
  const text = cell.text || EMPTY;
  return cell.tip ? <Tooltip title={cell.tip}><span>{text}</span></Tooltip> : <span>{text}</span>;
};

const ControllerProducts: React.FC = () => {
  useTranslation('controller'); // re-render on language change; lazy-loads the namespace
  const [rows, setRows] = useState<ControllerProduct[] | null>(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState('');
  useEffect(() => {
    let live = true;
    listControllerCatalog().then(r => { if (live) setRows(r.controllers); }).catch(e => { if (live) setError(String(e?.message ?? e)); });
    return () => { live = false; };
  }, []);
  const cells = useMemo(() => (rows ?? []).map(r => ({ row: r, cells: controllerCells(r, tx) })), [rows]);
  const columns = ['brand', 'model', 'revision', 'supplyMax', 'continuous', 'peak', 'peakTime', 'switching', 'devices', 'sensors', 'mass'];

  return (
    <Paper sx={{ p: 1.5, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)' }} data-testid="controller-products">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-0)' }}>{tx('controllers.title')}</Typography>
        <HelpTip title={tx('controllers.tip')} />
      </Box>
      {error && <Typography role="alert" sx={{ color: 'error.main', fontSize: 12 }}>{error}</Typography>}
      {rows && rows.length === 0 && <Typography sx={{ color: 'var(--text-3)', fontSize: 12 }}>{tx('controllers.empty')}</Typography>}
      {rows && rows.length > 0 && (
        <Box sx={{ overflowX: 'auto' }}>
          <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
            <thead><tr>{columns.map(c => (
              <Box component="th" key={c} sx={{ textAlign: 'left', px: 1, py: 0.6, fontSize: 10.5, color: 'var(--text-3)', borderBottom: '1px solid var(--line-soft)', whiteSpace: 'nowrap' }}>
                {tx(`controllers.col.${c}`)}
              </Box>))}
            </tr></thead>
            <tbody>
              {cells.map(({ row, cells: c }) => (
                <React.Fragment key={row.id}>
                  <Box component="tr" onClick={() => setOpen(open === row.id ? '' : row.id)} aria-expanded={open === row.id} data-testid={`controller-row-${row.id}`}
                    sx={{ cursor: 'pointer', '&:hover': { bgcolor: 'var(--panel)' } }}>
                    {columns.map(col => (
                      <Box component="td" key={col} sx={{ px: 1, py: 0.6, borderBottom: '1px solid var(--app-bg)', color: 'var(--text-1)', whiteSpace: 'nowrap' }}>
                        <CellText cell={c[col] ?? { text: '' }} />
                      </Box>))}
                  </Box>
                  {open === row.id && (
                    <tr><Box component="td" colSpan={columns.length} sx={{ px: 1.5, py: 1, borderBottom: '1px solid var(--line-soft)' }}>
                      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(260px,1fr))', gap: '6px 16px' }}>
                        {controllerDetails(row, tx).map(d => (
                          <Box key={d.label} sx={{ minWidth: 0 }}>
                            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.label}</Typography>
                            <Typography sx={{ fontSize: 12, color: 'var(--text-1)', overflowWrap: 'anywhere' }}>{d.value || EMPTY}</Typography>
                          </Box>))}
                      </Box>
                    </Box></tr>
                  )}
                </React.Fragment>))}
            </tbody>
          </Box>
        </Box>
      )}
    </Paper>
  );
};

export default ControllerProducts;

/**
 * "Flat wire in stock" — a compact reference table of the enamelled flat
 * copper wire physically on the shelf (`GET /api/wires/stock`, backed by
 * `config/wire_stock.yaml`).
 *
 * WHY (the owner, 2026-09-20): *"давай сделаем справочную таблицу по
 * доступным на складе проводам; мы потом будем брать данные отсюда, чтобы
 * пользователи могли менять толщину провода из тех, что есть реально"* — a
 * table the winding editors will later restrict the wire-size choice to.
 * This component only DISPLAYS the table; nothing here restricts a selector
 * yet (the owner decides the hard restriction later) — see
 * `lib/wireStock.ts::stockHint` for the passive hint that ships instead.
 *
 * Project rule: one short line + HelpTip, no text walls (`ui-no-text-walls`).
 */
import React, { useMemo, useState } from 'react';
import {
  Box, Typography, CircularProgress, Table, TableBody, TableCell, TableHead,
  TableRow, Chip, Tooltip, TableSortLabel,
} from '@mui/material';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import HelpTip from '../common/HelpTip';
import { useWireStock } from './useWireStock';
import {
  formatWireRow, widthsInStock, sortWiresByColumn,
  type WireSortKey, type SortDir,
} from '../../lib/wireStock';

const cellSx = { fontSize: '0.68rem', color: 'var(--text-2)', py: '4px', px: 1 };
const headSx = { ...cellSx, fontSize: '0.62rem', fontWeight: 700, color: 'var(--text-4)',
  letterSpacing: '0.04em', textTransform: 'uppercase' as const, borderBottom: '1px solid var(--line)' };

/** Column header that sorts on click — thickness/width/stock (owner,
 *  2026-09-20: "each sortable, click the header to sort asc/desc"). */
const SortHeadCell: React.FC<{
  label: string; col: WireSortKey; active: WireSortKey; dir: SortDir;
  onSort: (col: WireSortKey) => void; align?: 'left' | 'right';
}> = ({ label, col, active, dir, onSort, align = 'left' }) => (
  <TableCell sx={headSx} align={align}>
    <TableSortLabel
      active={active === col}
      direction={active === col ? dir : 'asc'}
      onClick={() => onSort(col)}
      sx={{ '&.MuiTableSortLabel-root': { color: 'inherit' },
        '&.Mui-active': { color: 'var(--text-2)' },
        '& .MuiTableSortLabel-icon': { fontSize: '0.9rem' } }}
    >
      {label}
    </TableSortLabel>
  </TableCell>
);

const WireStockTable: React.FC = () => {
  const { data, loading, error } = useWireStock();
  const [widthFilter, setWidthFilter] = useState<number | null>(null);
  // Default: thickness then width (owner, 2026-09-20) — same order sortWires gave.
  const [sortKey, setSortKey] = useState<WireSortKey>('thickness_mm');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const onSort = (col: WireSortKey) => {
    if (col === sortKey) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(col); setSortDir('asc'); }
  };

  const rows = useMemo(() => {
    const wires = data?.wires ?? [];
    const filtered = widthFilter == null ? wires : wires.filter(w => w.width_mm === widthFilter);
    return sortWiresByColumn(filtered, sortKey, sortDir);
  }, [data, widthFilter, sortKey, sortDir]);

  const widths = useMemo(() => widthsInStock(data?.wires ?? []), [data]);

  if (loading && !data) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
        <CircularProgress size={18} />
      </Box>
    );
  }

  if (error || !data) {
    return (
      <Box sx={{ p: 1.5 }}>
        <Typography variant="caption" color="error">
          {error ?? 'wire stock unavailable'}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ borderTop: '1px solid var(--line)', mt: 1, pt: 1 }}>
      {/* Header: one short line + tooltip, per the project's UI rule */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, px: 2, mb: 0.5 }}>
        <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-4)',
          letterSpacing: '0.1em', textTransform: 'uppercase' }}>
          Flat wire in stock
        </Typography>
        <HelpTip title={`What the warehouse holds today (${data.updated ?? '—'}); the winding editors will offer these sizes.`} />
      </Box>

      {/* Filter by width */}
      {widths.length > 1 && (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, px: 2, mb: 0.75 }}>
          <Chip
            label="all widths" size="small" clickable
            onClick={() => setWidthFilter(null)}
            variant={widthFilter == null ? 'filled' : 'outlined'}
            sx={{ height: 18, fontSize: '0.6rem' }}
          />
          {widths.map(w => (
            <Chip
              key={w}
              label={`${w} mm`} size="small" clickable
              onClick={() => setWidthFilter(w)}
              variant={widthFilter === w ? 'filled' : 'outlined'}
              sx={{ height: 18, fontSize: '0.6rem' }}
            />
          ))}
        </Box>
      )}

      <Box sx={{ px: 1, maxHeight: 260, overflowY: 'auto' }}>
        <Table size="small" sx={{ '& td, & th': { border: 0 } }}>
          <TableHead>
            <TableRow>
              <SortHeadCell label="Thickness (mm)" col="thickness_mm" active={sortKey} dir={sortDir} onSort={onSort} />
              <SortHeadCell label="Width (mm)" col="width_mm" active={sortKey} dir={sortDir} onSort={onSort} />
              <TableCell sx={headSx}>Insulation</TableCell>
              <SortHeadCell label="Stock" col="stock_kg" active={sortKey} dir={sortDir} onSort={onSort} align="right" />
              <TableCell sx={headSx}>Warehouse</TableCell>
              <TableCell sx={headSx}>Code</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map(raw => {
              const r = formatWireRow(raw);
              return (
                <TableRow key={`${r.code}|${raw.spec}`} hover>
                  <TableCell sx={{ ...cellSx, color: 'var(--text-1)', whiteSpace: 'nowrap' }}>
                    {r.thicknessLabel}
                    {r.check && (
                      <Tooltip title="label ambiguous — confirm">
                        <WarningAmberIcon sx={{ fontSize: 12, color: '#f59e0b', ml: 0.5, verticalAlign: 'middle' }} />
                      </Tooltip>
                    )}
                  </TableCell>
                  <TableCell sx={{ ...cellSx, color: 'var(--text-1)', whiteSpace: 'nowrap' }}>{r.widthLabel}</TableCell>
                  <TableCell sx={cellSx}>{r.insulationLabel}</TableCell>
                  <TableCell sx={{ ...cellSx, textAlign: 'right' }}>{r.stockLabel}</TableCell>
                  <TableCell sx={cellSx}>{r.warehouse || '—'}</TableCell>
                  <TableCell sx={{ ...cellSx, fontFamily: 'monospace', fontSize: '0.62rem' }}>{r.code}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
};

export default WireStockTable;

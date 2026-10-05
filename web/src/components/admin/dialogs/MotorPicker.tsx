// Admin · shared motor-grant picker — used by the Invite dialog and by the
// per-user Motors dialog, so a motor list that reads one way in one of them
// cannot read another way in the other.
import React, { useEffect, useMemo, useState } from 'react';
import { Box, Typography, CircularProgress, FormControlLabel, Switch, Checkbox } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { CardBadge, CardSummary } from '../../common/CardBadge';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const LABEL = { fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' } as const;

export interface MotorGrants { all: boolean; dies: string[]; default?: { die: string; config: string } | null }
export interface CatalogDie {
  name: string; stator_diameter: number | null;
  slots?: number | null; poles?: number | null;
  configs: number; duties: number;
  /** configuration names of the die */
  config_names?: string[];
  /** {configuration: date} of those that have a FULL passport card (v1 record) */
  cards?: Record<string, string>;
}

/** Load the catalog once per dialog opening. */
export const useCatalog = (open: boolean) => {
  const [dies, setDies] = useState<CatalogDie[] | null>(null);
  useEffect(() => {
    if (!open) return;
    setDies(null);
    fetch(`${API}/api/admin/motors`, { cache: 'no-store' })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((j) => setDies(j.dies ?? []))
      .catch(() => setDies([]));
  }, [open]);
  return dies;
};

export const MotorPicker: React.FC<{
  dies: CatalogDie[] | null; all: boolean; picked: Set<string>;
  onAll: (v: boolean) => void; onToggle: (name: string) => void;
}> = ({ dies, all, picked, onAll, onToggle }) => {
  const { t } = useTranslation('admin');
  // Grouped by stator diameter — the same hierarchy the Motors tab shows.
  const groups = useMemo(() => {
    const m = new Map<string, CatalogDie[]>();
    for (const d of dies ?? []) {
      const k = d.stator_diameter == null ? '—' : String(d.stator_diameter);
      m.set(k, [...(m.get(k) ?? []), d]);
    }
    return [...m.entries()].sort((a, b) => Number(a[0]) - Number(b[0]));
  }, [dies]);

  return (
    <>
      <FormControlLabel
        control={<Switch size="small" checked={all} onChange={(e) => onAll(e.target.checked)} />}
        label={<Typography sx={{ fontSize: 13 }}>{t('motors.allMotors')} <span style={{ color: 'var(--text-4)' }}>{t('motors.allMotorsNote')}</span></Typography>}
      />
      {dies === null && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 2, color: 'var(--text-3)' }}>
          <CircularProgress size={16} /> <Typography sx={{ fontSize: 12 }}>{t('motors.loading')}</Typography>
        </Box>
      )}
      {dies !== null && groups.map(([dia, list]) => (
        <Box key={dia} sx={{ mt: 1.5, opacity: all ? 0.45 : 1 }}>
          <Typography sx={{ ...LABEL, color: '#60a5fa' }}>Ø {dia} mm</Typography>
          {list.map((d) => (
            <FormControlLabel key={d.name} sx={{ display: 'flex', ml: 0 }}
              control={<Checkbox size="small" disabled={all} checked={all || picked.has(d.name)}
                onChange={() => onToggle(d.name)} sx={{ py: 0.25 }} />}
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
                  <Typography sx={{ fontSize: 12.5, color: 'var(--text-1)' }}>
                    {d.name}
                    <span style={{ color: 'var(--text-4)' }}>
                      {'  '}· {t('motors.configs', { count: d.configs })}
                    </span>
                  </Typography>
                  <CardSummary cards={Object.keys(d.cards ?? {}).length} total={d.configs} />
                  {(d.config_names ?? []).map((c) => (
                    <Box key={c} sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.4 }}>
                      <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{c}</Typography>
                      <CardBadge date={d.cards?.[c]} />
                    </Box>
                  ))}
                </Box>
              } />
          ))}
        </Box>
      ))}
      {dies !== null && dies.length === 0 && (
        <Typography sx={{ fontSize: 12, color: 'var(--text-4)', py: 2 }}>
          {t('motors.empty')}
        </Typography>
      )}
    </>
  );
};

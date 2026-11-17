/** The standard designation of a stored motor with its "?" decoder (owner 2026-11-16).  The name is computed by the server from the
 *  parameters at every read (`GET /api/designation/motor/{die}/{config}`), never typed and never cached here, so an edit shows at
 *  once.  A segment the parameters cannot decide is "?"; a stored name that contradicts the parameters is listed in the popover. */
import React, { useEffect, useState } from 'react';
import { Box, IconButton, Popover, Table, TableBody, TableCell, TableRow, Tooltip, Typography } from '@mui/material';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import { useTranslation } from 'react-i18next';
import { connectionParts, meaningKey, SEGMENTS, UNKNOWN, type Segment } from '../../lib/motorDesignation';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface SegmentInfo { value: string; source: string; [k: string]: unknown }
/** Stored results of this motor computed for ANOTHER designation (each result keeps the name it was computed for). */
export interface OtherName { computed_for: string; now: string; results: Array<{ duty: string; kind: string; computed_at?: string | null }> }
/** For a customer or the factory the server sends no `checks` and no incomplete designation (`designation: null`). */
export interface DesignationAnswer { designation: string | null; complete: boolean; unknown?: string[]; segments?: Record<string, SegmentInfo>;
  checks?: Array<{ code: string; name: string; parameters?: string }>; other_names?: OtherName[] }

export const fetchDesignation = async (die: string, config: string): Promise<DesignationAnswer | null> => {
  try {
    const r = await fetch(`${API}/api/designation/motor/${encodeURIComponent(die)}/${encodeURIComponent(config)}`, { cache: 'no-store' });
    return r.ok ? await r.json() as DesignationAnswer : null;
  } catch { return null; }
};

const MARK = '#f59e0b';

/** "N results computed for <old>; the motor is now <new>": one line per earlier name. */
export const otherNameText = (t: (k: string, o?: Record<string, unknown>) => string, o: OtherName) =>
  t('designation.otherName', { count: o.results.length, old: o.computed_for, now: o.now });

export const DesignationDecoder: React.FC<{ segments: Record<string, SegmentInfo>; checks?: DesignationAnswer['checks']; otherNames?: OtherName[] }> = ({ segments, checks, otherNames }) => {
  const { t } = useTranslation('common');
  const words = (seg: Segment, v: string) => {
    if (seg === 'connection') {
      const c = connectionParts(v);
      return c ? t('designation.meaning.connection.words', { series: c.series, parallel: c.parallel }) : t('designation.meaning.unknown');
    }
    if (seg === 'double' && !v && Number(segments.double?.phases) === 6) return t('designation.meaning.double.inTurns');
    return t(meaningKey(seg, v), { n: v });
  };
  const fact = (seg: string, s: SegmentInfo) => {
    if (seg === 'winding' && s.slots) return t('designation.fact.slots', { n: s.slots });
    if (seg === 'double' && s.phases) return t('designation.fact.phases', { n: s.phases });
    if (seg === 'turns' && Number(s.sets) > 1 && s.per_set) return t('designation.fact.turnsSets', { sets: s.sets, n: s.per_set });
    if (seg === 'connection' && Number(s.sets) > 1) return t('designation.fact.perSet');
    if ((seg === 'steel' || seg === 'ferrite' || seg === 'aluminium') && s.material) return String(s.material);
    if (seg === 'wire' && s.width_mm && s.height_mm) return `${s.width_mm} × ${s.height_mm} mm`;
    if (seg === 'connection' && s.stored) return String(s.stored);
    return '';
  };
  return (
    <Box sx={{ p: 1.5, maxWidth: 460 }}>
      <Table size="small" sx={{ '& td': { fontSize: 12, borderColor: 'var(--panel)', py: 0.4 } }}>
        <TableBody>
          {SEGMENTS.filter((seg) => segments[seg]).map((seg) => {
            const s = segments[seg];
            return (
              <TableRow key={seg}>
                <TableCell sx={{ fontFamily: 'monospace', fontWeight: 800, color: s.value === UNKNOWN ? '#f87171' : 'var(--text-0)', whiteSpace: 'nowrap' }}>{s.value || '·'}</TableCell>
                <TableCell>
                  {t(`designation.segment.${seg}`)}: {words(seg, s.value)}
                  {s.source === 'die_name' && <Tooltip title={t('designation.fromDieNameTip')}><Box component="span" sx={{ ml: 0.5, color: 'var(--text-3)' }}>ⓘ</Box></Tooltip>}
                </TableCell>
                <TableCell sx={{ color: 'var(--text-3)', whiteSpace: 'nowrap' }}>{fact(seg, s)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {otherNames?.map((o) => (
        <Typography key={o.computed_for} data-testid="designation-other-name" sx={{ mt: 0.75, fontSize: 12, color: MARK }}>
          {otherNameText(t, o)}: {o.results.map((r) => r.duty).filter((d, i, a) => a.indexOf(d) === i).join(', ')}
        </Typography>
      ))}
      {checks?.map((c, i) => (
        <Typography key={i} sx={{ mt: 0.75, fontSize: 12, color: '#f59e0b' }}>{t(`designation.check.${c.code}`, { name: c.name, value: c.parameters ?? '' })}</Typography>
      ))}
    </Box>
  );
};

/** The name in monospace, "?" opens the decoder, "≠" marks results computed for another name (never a silent relabel).
 *  Pure on its answer, so it renders server-side in the tests with the real locale files. */
export const DesignationName: React.FC<{ answer: DesignationAnswer; size?: number }> = ({ answer, size = 12 }) => {
  const { t } = useTranslation('common');
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  if (!answer.designation || !answer.segments) return null;
  const segments = answer.segments, checks = answer.checks ?? [], others = answer.other_names ?? [];
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  const othersText = others.map((o) => otherNameText(t, o)).join('\n');
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', ml: 1, verticalAlign: 'middle' }} onClick={stop} data-testid="motor-designation">
      <Typography component="span" sx={{ fontFamily: 'monospace', fontSize: size, fontWeight: 700, color: answer.complete ? 'var(--text-1)' : 'var(--text-3)' }}>
        {answer.designation}
      </Typography>
      <Tooltip title={t('designation.decode')}>
        <IconButton size="small" aria-label={t('designation.decode')} onClick={(e) => setAnchor(e.currentTarget)} sx={{ p: 0.25, color: 'var(--text-3)' }}>
          <HelpOutlineIcon sx={{ fontSize: size + 2 }} />
        </IconButton>
      </Tooltip>
      {others.length > 0 && (
        <Tooltip title={<Box component="span" sx={{ whiteSpace: 'pre-line' }}>{othersText}</Box>}>
          <Box component="span" role="img" aria-label={othersText} data-testid="designation-other-mark"
            sx={{ color: MARK, fontWeight: 800, fontSize: size, ml: 0.25 }}>≠</Box>
        </Tooltip>
      )}
      {checks.length > 0 && <Tooltip title={t('designation.checksTip', { n: checks.length })}><Box component="span" sx={{ color: MARK, fontWeight: 800, fontSize: size }}>!</Box></Tooltip>}
      <Popover open={!!anchor} anchorEl={anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }} onClick={stop}>
        <DesignationDecoder segments={segments} checks={checks} otherNames={others} />
      </Popover>
    </Box>
  );
};

/** `<MotorDesignation die config />`: fetches the answer; nothing while it loads or for a motor the caller may not see. */
const MotorDesignation: React.FC<{ die: string; config: string; size?: number }> = ({ die, config, size = 12 }) => {
  const [answer, setAnswer] = useState<DesignationAnswer | null>(null);
  useEffect(() => {
    let alive = true;
    setAnswer(null);
    void fetchDesignation(die, config).then((a) => { if (alive) setAnswer(a); });
    return () => { alive = false; };
  }, [die, config]);
  return answer ? <DesignationName answer={answer} size={size} /> : null;
};

export default MotorDesignation;

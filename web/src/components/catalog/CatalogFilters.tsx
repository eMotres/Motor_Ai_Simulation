/** The Motors catalog's filters, in the Motors title line beside the application filter (owner 2026-11-17: "filters of what
 *  is relevant for me now, a project or projects that are now in simulation or in production, that is, filter by stage";
 *  "my current" = saved filter sets).  The state lives in stores/catalogFilterStore; CatalogByDie applies it and reports
 *  the choices and counts back.  Admins: Active / Parked / Archived, stages, projects, "running".  Everybody: the project
 *  picked in the tab's project selector as the first row of Projects, the saved sets, the count. */
import React, { useState } from 'react';
import { Box, Button, Checkbox, Chip, ListItemText, Menu, MenuItem, Select, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { DisplayStateFilter } from '../common/DisplayStateControl';
import { TextPromptDialog, type TextPromptState } from '../common/PromptDialogs';
import { isFiltered, stageKey, type StateFilter } from '../../lib/catalogByDie';
import { useCatalogFilterStore } from '../../stores/catalogFilterStore';

/** the Projects filter's first row: the project chosen in the tab's selector (not a project id) */
const HIGHLIGHTED = '__highlighted__';
const ctl = { fontSize: 11, height: 24, '& .MuiSelect-select': { py: 0.25, pl: 1, fontSize: 11 } } as const;

const CatalogFilters: React.FC<{ isAdmin: boolean; highlightTitle: string | null }> = ({ isAdmin, highlightTitle }) => {
  const { t } = useTranslation(['motors', 'common', 'development']);
  const { filter, options, sets, activeSet, set, clear, saveSet, applySet, deleteSet } = useCatalogFilterStore();
  const [setsAnchor, setSetsAnchor] = useState<HTMLElement | null>(null);
  const [ask, setAsk] = useState<TextPromptState | null>(null);
  const stageLabel = (s: string) => t(stageKey(s), { defaultValue: s });
  const projectTitle = (id: string) => (id === HIGHLIGHTED ? highlightTitle ?? ''
    : options.projects.find((p) => p.id === id)?.title ?? id);
  const picked = [...(filter.onlyHighlighted && highlightTitle ? [HIGHLIGHTED] : []), ...(isAdmin ? filter.projects : [])];
  return (
    <Box data-testid="catalog-filters" sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
      {isAdmin && (
        <DisplayStateFilter value={filter.state} onChange={(f) => set({ state: f as StateFilter })}
          counts={{ counts: options.counts, woke: { active: 0, parked: 0, archived: 0, all: 0 } }} tip={t('motors:byDie.stateTip')} />
      )}
      {/* owner 2026-11-25: Active shows no old motors by default; the switch brings them back (remembered with the filter) */}
      {isAdmin && filter.state === 'active' && (options.old > 0 || filter.showOld) && (
        <Tooltip title={t('motors:byDie.filter.showOldTip')}>
          <Chip size="small" clickable data-testid="filter-show-old" label={t('motors:byDie.filter.showOld', { n: options.old })}
            variant={filter.showOld ? 'filled' : 'outlined'} color={filter.showOld ? 'primary' : 'default'}
            onClick={() => set({ showOld: !filter.showOld })} sx={{ height: 22, fontSize: 11 }} />
        </Tooltip>
      )}
      {isAdmin && options.stages.length > 0 && (
        <Tooltip title={t('motors:byDie.filter.stageTip')}>
          <Select multiple size="small" displayEmpty value={filter.stages} inputProps={{ 'data-testid': 'filter-stage' }} sx={ctl}
            onChange={(e) => set({ stages: typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value as string[] })}
            renderValue={(v) => ((v as string[]).length ? (v as string[]).map(stageLabel).join(', ') : t('motors:byDie.filter.stage'))}>
            {options.stages.map((s) => (
              <MenuItem key={s} value={s} dense><Checkbox size="small" checked={filter.stages.includes(s)} sx={{ p: 0.25 }} />
                <ListItemText primary={stageLabel(s)} primaryTypographyProps={{ fontSize: 12 }} /></MenuItem>
            ))}
          </Select>
        </Tooltip>
      )}
      {/* ONE project control (owner 2026-11-25: "two filters on projects"): the project chosen in the tab's selector is the
          first row of the Projects filter (everybody); the projects of the catalog follow (admins) */}
      {((isAdmin && options.projects.length > 0) || !!highlightTitle) && (
        <Tooltip title={t('motors:byDie.filter.projectTip')}>
          <Select multiple size="small" displayEmpty value={picked} inputProps={{ 'data-testid': 'filter-project' }} sx={{ ...ctl, maxWidth: 220 }}
            onChange={(e) => {
              const v = typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value as string[];
              set({ onlyHighlighted: v.includes(HIGHLIGHTED), projects: isAdmin ? v.filter((x) => x !== HIGHLIGHTED) : filter.projects });
            }}
            renderValue={(v) => ((v as string[]).length ? (v as string[]).map(projectTitle).join(', ') : t('motors:byDie.filter.project'))}>
            {highlightTitle && (
              <MenuItem value={HIGHLIGHTED} dense data-testid="filter-highlighted"><Checkbox size="small" checked={filter.onlyHighlighted} sx={{ p: 0.25 }} />
                <ListItemText primary={t('motors:byDie.filter.highlightedRow', { project: highlightTitle })} primaryTypographyProps={{ fontSize: 12 }} /></MenuItem>
            )}
            {isAdmin && options.projects.filter((p) => p.title !== highlightTitle).map((p) => (
              <MenuItem key={p.id} value={p.id} dense><Checkbox size="small" checked={filter.projects.includes(p.id)} sx={{ p: 0.25 }} />
                <ListItemText primary={p.title} primaryTypographyProps={{ fontSize: 12 }} /></MenuItem>
            ))}
          </Select>
        </Tooltip>
      )}
      {isAdmin && (
        <Tooltip title={t('motors:byDie.filter.runningTip')}>
          <Chip size="small" clickable data-testid="filter-running" label={t('motors:byDie.filter.running')}
            variant={filter.running ? 'filled' : 'outlined'} color={filter.running ? 'success' : 'default'}
            onClick={() => set({ running: !filter.running })} sx={{ height: 22, fontSize: 11 }} />
        </Tooltip>
      )}
      <Tooltip title={t('motors:byDie.filter.setsTip')}>
        <Button size="small" variant="outlined" data-testid="filter-sets" onClick={(e) => setSetsAnchor(e.currentTarget)}
          sx={{ textTransform: 'none', fontSize: 11, py: 0, minHeight: 24 }}>
          {activeSet ?? t('motors:byDie.filter.sets')}
        </Button>
      </Tooltip>
      <Menu open={!!setsAnchor} anchorEl={setsAnchor} onClose={() => setSetsAnchor(null)}>
        {sets.map((s) => (
          <MenuItem key={s.name} dense selected={s.name === activeSet} onClick={() => { applySet(s.name); setSetsAnchor(null); }}>
            <ListItemText primary={s.name} primaryTypographyProps={{ fontSize: 12 }} />
            <Box component="span" role="button" aria-label={t('motors:byDie.filter.deleteSet', { name: s.name })}
              onClick={(e) => { e.stopPropagation(); deleteSet(s.name); }} sx={{ ml: 2, color: 'var(--text-4)', fontSize: 13 }}>×</Box>
          </MenuItem>
        ))}
        <MenuItem dense data-testid="filter-save-set" onClick={() => {
          setSetsAnchor(null);
          setAsk({ title: t('motors:byDie.filter.saveTitle'), label: t('motors:byDie.filter.saveLabel'), initial: activeSet ?? '',
            okLabel: t('motors:byDie.filter.saveOk'), onSubmit: (name) => saveSet(name) });
        }}>{t('motors:byDie.filter.saveCurrent')}</MenuItem>
      </Menu>
      <TextPromptDialog state={ask} onClose={() => setAsk(null)} />
      {isFiltered(filter) && (
        <>
          <Typography component="span" data-testid="filter-count" sx={{ fontSize: 11, color: 'var(--text-2)' }}>
            {t('motors:byDie.filter.count', { shown: options.shown, total: options.total })}
          </Typography>
          <Button size="small" data-testid="filter-clear" onClick={clear} sx={{ textTransform: 'none', fontSize: 11, minWidth: 0, py: 0 }}>
            {t('motors:byDie.filter.clear')}
          </Button>
        </>
      )}
    </Box>
  );
};

export default CatalogFilters;

/** The Motors catalog's filters, in the Motors title line beside the application filter (owner 2026-11-17: "filters of what
 *  is relevant for me now, a project or projects that are now in simulation or in production, that is, filter by stage";
 *  "my current" = saved filter sets).  The state lives in stores/catalogFilterStore; CatalogByDie applies it and reports
 *  the choices and counts back.  Admins: Active / Parked / Archived, stages, projects, "running".  Everybody: "only the
 *  highlighted project's motors" (their own project, picked in the tab's project selector), the saved sets, the count. */
import React, { useState } from 'react';
import { Box, Button, Checkbox, Chip, ListItemText, Menu, MenuItem, Select, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { DisplayStateFilter } from '../common/DisplayStateControl';
import { TextPromptDialog, type TextPromptState } from '../common/PromptDialogs';
import { isFiltered, stageKey, type StateFilter } from '../../lib/catalogByDie';
import { useCatalogFilterStore } from '../../stores/catalogFilterStore';

const ctl = { fontSize: 11, height: 24, '& .MuiSelect-select': { py: 0.25, pl: 1, fontSize: 11 } } as const;

const CatalogFilters: React.FC<{ isAdmin: boolean; highlightTitle: string | null }> = ({ isAdmin, highlightTitle }) => {
  const { t } = useTranslation(['motors', 'common', 'development']);
  const { filter, options, sets, activeSet, set, clear, saveSet, applySet, deleteSet } = useCatalogFilterStore();
  const [setsAnchor, setSetsAnchor] = useState<HTMLElement | null>(null);
  const [ask, setAsk] = useState<TextPromptState | null>(null);
  const stageLabel = (s: string) => t(stageKey(s), { defaultValue: s });
  const projectTitle = (id: string) => options.projects.find((p) => p.id === id)?.title ?? id;
  return (
    <Box data-testid="catalog-filters" sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
      {isAdmin && (
        <DisplayStateFilter value={filter.state} onChange={(f) => set({ state: f as StateFilter })}
          counts={{ counts: options.counts, woke: { active: 0, parked: 0, archived: 0, all: 0 } }} tip={t('motors:byDie.stateTip')} />
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
      {isAdmin && options.projects.length > 0 && (
        <Tooltip title={t('motors:byDie.filter.projectTip')}>
          <Select multiple size="small" displayEmpty value={filter.projects} inputProps={{ 'data-testid': 'filter-project' }} sx={{ ...ctl, maxWidth: 220 }}
            onChange={(e) => set({ projects: typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value as string[] })}
            renderValue={(v) => ((v as string[]).length ? (v as string[]).map(projectTitle).join(', ') : t('motors:byDie.filter.project'))}>
            {options.projects.map((p) => (
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
      {highlightTitle && (
        <Tooltip title={t('motors:byDie.filter.onlyHighlightedTip')}>
          <Chip size="small" clickable data-testid="filter-highlighted" label={t('motors:byDie.filter.onlyHighlighted', { project: highlightTitle })}
            variant={filter.onlyHighlighted ? 'filled' : 'outlined'} color={filter.onlyHighlighted ? 'warning' : 'default'}
            onClick={() => set({ onlyHighlighted: !filter.onlyHighlighted })} sx={{ height: 22, fontSize: 11, maxWidth: 220 }} />
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

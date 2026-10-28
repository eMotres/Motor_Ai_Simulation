/** The List / Cards switch of the Development and Production overviews, and the hook that remembers the choice. */
import React, { useCallback, useState } from 'react';
import { ToggleButton, ToggleButtonGroup, Tooltip } from '@mui/material';
import ViewListIcon from '@mui/icons-material/ViewList';
import ViewModuleIcon from '@mui/icons-material/ViewModule';
import { useTranslation } from 'react-i18next';
import { parseViewMode, readViewMode, writeViewMode, type ViewMode } from '../../lib/viewMode';

/** The page's viewer and its setter; the choice is read once and written to localStorage on every change. */
export function useViewMode(key: string, fallback: ViewMode): [ViewMode, (mode: ViewMode) => void] {
  const [mode, setMode] = useState<ViewMode>(() => readViewMode(key, fallback));
  const choose = useCallback((next: ViewMode) => { setMode(next); writeViewMode(key, next); }, [key]);
  return [mode, choose];
}

const ViewModeToggle: React.FC<{ mode: ViewMode; onChange: (mode: ViewMode) => void }> = ({ mode, onChange }) => {
  const { t } = useTranslation('common');
  return (
    <ToggleButtonGroup size="small" exclusive value={mode} aria-label={t('viewMode.label')}
      onChange={(_, next) => { const m = parseViewMode(next); if (m) onChange(m); }}   // clicking the active button keeps it
      sx={{ '& .MuiToggleButton-root': { py: 0.25, px: 0.75, color: 'var(--text-3)', borderColor: 'var(--line-soft)' } }}>
      <ToggleButton value="list" aria-label={t('viewMode.list')}>
        <Tooltip title={t('viewMode.list')}><ViewListIcon sx={{ fontSize: 18 }} /></Tooltip>
      </ToggleButton>
      <ToggleButton value="cards" aria-label={t('viewMode.cards')}>
        <Tooltip title={t('viewMode.cards')}><ViewModuleIcon sx={{ fontSize: 18 }} /></Tooltip>
      </ToggleButton>
    </ToggleButtonGroup>
  );
};

export default ViewModeToggle;

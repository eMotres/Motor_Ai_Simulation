import { useEffect, useState } from 'react';
import { Box, Button, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { useUIStore } from '../../stores/motorStore';
import { developmentApi } from '../../modules/development/api';

const tx = nsT('motors');   // EN source, ZH mirror (docs/I18N.md)

/** What a new account with no motors sees instead of "no motors granted yet - ask the vendor" (owner 2026-11-06): one line and the
 *  way on - Projects, with the New project dialog open. */
export const NEW_PROJECT_EVENT = 'development:new-project';
export const NEW_PROJECT_FLAG = 'development.newProject';

export default function StartProject() {
  useTranslation('motors');
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  // a customer coming back already has a project: the way on is that project, not another new one
  const [hasProject, setHasProject] = useState(false);
  useEffect(() => {
    let live = true;
    void developmentApi.list().then((list) => { if (live) setHasProject(list.length > 0); }).catch(() => { /* the first-visit text stays */ });
    return () => { live = false; };
  }, []);
  const start = () => {
    if (!hasProject) {
      try { sessionStorage.setItem(NEW_PROJECT_FLAG, '1'); } catch { /* the event below still opens the dialog */ }
    }
    setActiveTab('development' as never);
    if (!hasProject) window.dispatchEvent(new Event(NEW_PROJECT_EVENT));
  };
  return <Box data-testid="start-project" sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
    <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }}>{tx(hasProject ? 'startProject.textBack' : 'startProject.text')}</Typography>
    <Button size="small" variant="contained" onClick={start} sx={{ textTransform: 'none' }}>{tx(hasProject ? 'startProject.buttonBack' : 'startProject.button')}</Button>
  </Box>;
}

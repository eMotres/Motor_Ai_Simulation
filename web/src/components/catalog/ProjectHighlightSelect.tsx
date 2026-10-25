/**
 * ProjectHighlightSelect - the Motors tab's project selector (owner 2026-10-26).
 *
 * The geometries (die / configuration) that belong to the selected project are highlighted in the list below; it opens on
 * the LAST OPENED project (the one a motor was last opened from) and another project highlights its motors instead.  A
 * view aid only: the list still shows exactly the motors the caller may use (grants and project membership unchanged),
 * and the project's own motor list is read through the project routes' gates - a project the caller cannot see is not
 * offered.  Renders nothing for an account with no projects.
 */
import React, { useEffect, useState } from 'react';
import { MenuItem, Select, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { developmentApi } from '../../modules/development/api';
import { pickFocusProject } from '../../modules/development/projectFocus';
import { useProjectFocusStore } from '../../stores/projectFocusStore';
import { useProjectHighlightStore } from '../../stores/projectHighlightStore';
import { nsT } from '../../i18n/nsT';

const tx = nsT('motors');

const ProjectHighlightSelect: React.FC = () => {
  useTranslation('motors');
  const focus = useProjectFocusStore((s) => s.focus);
  const setFocus = useProjectFocusStore((s) => s.set);
  const setMotors = useProjectHighlightStore((s) => s.setMotors);
  const [projects, setProjects] = useState<Array<{ id: string; title: string }>>([]);

  useEffect(() => {
    let cancelled = false;
    developmentApi.list()
      .then((list) => { if (!cancelled) setProjects(list.map((p) => ({ id: p.id, title: p.title }))); })
      .catch(() => { if (!cancelled) setProjects([]); });
    return () => { cancelled = true; };
  }, []);

  const chosen = pickFocusProject(projects, focus);
  useEffect(() => {
    if (!chosen) { setMotors([]); return; }
    let cancelled = false;
    developmentApi.motors(chosen.id, true)
      .then((motors) => { if (!cancelled) setMotors(motors.filter((m) => m.kind !== 'history')); })
      .catch(() => { if (!cancelled) setMotors([]); });
    return () => { cancelled = true; };
  }, [chosen?.id, setMotors]);

  if (projects.length === 0) return null;
  return (
    <Tooltip title={tx('projectFocus.tip')}>
      <Select size="small" displayEmpty value={chosen?.id ?? ''} aria-label={tx('projectFocus.label')}
        onChange={(e) => {
          const id = String(e.target.value);
          const found = projects.find((p) => p.id === id);
          setFocus(found ? { id: found.id, title: found.title } : null);
        }}
        renderValue={(v) => (v ? `${tx('projectFocus.label')}: ${projects.find((p) => p.id === v)?.title ?? v}` : tx('projectFocus.none'))}
        sx={{ fontSize: 11, maxWidth: 260, '& .MuiSelect-select': { py: 0.15, px: 1 } }}>
        <MenuItem value="" sx={{ fontSize: 12 }}>{tx('projectFocus.none')}</MenuItem>
        {projects.map((p) => <MenuItem key={p.id} value={p.id} sx={{ fontSize: 12 }}>{p.title}</MenuItem>)}
      </Select>
    </Tooltip>
  );
};

export default ProjectHighlightSelect;

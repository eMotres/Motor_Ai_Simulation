import { useEffect, useState } from 'react';
import { Button, Dialog, DialogContent, DialogTitle, IconButton, Tooltip } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { testsApi } from '../tests/api';
import TestsPanel from '../tests/components/TestsPanel';
import type { ProjectMotor } from './types';

/** Which linked motors of a project have tests the caller may see: one request per project, shared by every variant row
 *  for a short while (the project motor list renders one header per variant). */
const INDEX_TTL_MS = 30_000;
const indexCache = new Map<string, { at: number; keys: Promise<Set<string>> }>();
const key = (die: string, config: string) => `${die.toLowerCase()}/${config.toLowerCase()}`;

function testsIndex(projectId: string, showAll: boolean): Promise<Set<string>> {
  const ck = `${projectId}|${showAll ? 'all' : 'latest'}`;
  const hit = indexCache.get(ck);
  if (hit && Date.now() - hit.at < INDEX_TTL_MS) return hit.keys;
  const keys = testsApi.projectIndex(projectId, showAll).then((rows) => new Set(rows.map((r) => key(r.die, r.config)))).catch(() => new Set<string>());
  indexCache.set(ck, { at: Date.now(), keys });
  return keys;
}

/** "Tests" of one project motor (owner 2026-10-26): read-only bench measurements against the simulation, shown only when the
 *  motor has tests the caller may see (engineers and the owner always; customers behind the configuration's flag). */
export function ProjectMotorTests({ projectId, motor, showAll, t }: {
  projectId: string; motor: ProjectMotor; showAll?: boolean; t: (k: string) => string;
}) {
  const die = String(motor.canonical_die ?? motor.die);
  const [has, setHas] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let live = true;
    void testsIndex(projectId, !!showAll).then((s) => { if (live) setHas(s.has(key(die, motor.config))); });
    return () => { live = false; };
  }, [projectId, die, motor.config, showAll]);
  if (!has) return null;
  return <>
    <Tooltip title={t('motorTests.tip')}><Button size="small" onClick={() => setOpen(true)}>{t('motorTests.button')}</Button></Tooltip>
    <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="lg">
      <DialogTitle sx={{ fontSize: 15, display: 'flex', alignItems: 'center' }}>
        <span style={{ flex: 1 }}>{t('motorTests.button')} · {die} / {motor.config}</span>
        <IconButton size="small" onClick={() => setOpen(false)}><CloseIcon sx={{ fontSize: 18 }} /></IconButton>
      </DialogTitle>
      <DialogContent>
        {open && <TestsPanel die={motor.die} config={motor.config} source={{ kind: 'project', projectId, showAll }} />}
      </DialogContent>
    </Dialog>
  </>;
}

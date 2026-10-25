import { useEffect } from 'react';
import { useTabContextStore } from '../../stores/tabContextStore';
import { projectStripContext } from './projectStrip';
import type { ProjectMotor, ProjectSummary } from './types';

/** Publish the open project (and its motors) for the strip under the tabs; clears it when no project is open or the
 *  page unmounts. */
export function useProjectStripContext(project: ProjectSummary | null, motors: ProjectMotor[]): void {
  useEffect(() => {
    useTabContextStore.getState().setProject(projectStripContext(project, motors));
  }, [project, motors]);
  useEffect(() => () => { useTabContextStore.getState().setProject(null); }, []);
}

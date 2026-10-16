// Configure publishes what is on its screen for the help assistant (owner
// 2026-10-05): knob values, drive, propeller, battery, the key result tiles and
// the red lines.  Published while the panel is mounted with a model, cleared when
// it unmounts or has no model.  Read by lib/supportContext.buildSupportContext.
import { useEffect } from 'react';
import {
  setConfigureSnapshot, buildConfigureSnapshot, type ConfigureSnapshotInput,
} from '../../lib/supportContext';

export function useConfigureSnapshot(input: ConfigureSnapshotInput | null): void {
  const key = input ? JSON.stringify(input) : '';
  useEffect(() => {
    setConfigureSnapshot(input ? buildConfigureSnapshot(input) : null);
    return () => setConfigureSnapshot(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

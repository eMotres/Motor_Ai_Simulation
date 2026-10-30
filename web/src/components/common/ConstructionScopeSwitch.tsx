// The Active | Full switch - the SAME component and the SAME state in the Geometry and the Materials tab (owner 2026-10-15).
// One store (lib/constructionScope.ts): switching it in one tab switches the other; the choice is remembered per user.
import React, { useEffect, useSyncExternalStore } from 'react';
import { ToggleButton, ToggleButtonGroup, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../contexts/AuthContext';
import { nsT } from '../../i18n/nsT';
import { scopeStore, SCOPES, type ConstructionScope } from '../../lib/constructionScope';
import SubTabs from './SubTabs';

const tx = nsT('geometry');

/** The shared scope and its setter; binds the remembered choice to the signed-in user. */
export function useConstructionScope(): [ConstructionScope, (s: ConstructionScope) => void] {
  const { user } = useAuth();
  const email = user?.email ?? null;
  useEffect(() => { scopeStore.bindUser(email); }, [email]);
  const scope = useSyncExternalStore(scopeStore.subscribe, scopeStore.get, scopeStore.get);
  return [scope, scopeStore.set];
}

const ConstructionScopeSwitch: React.FC<{ testIdPrefix?: string }> = ({ testIdPrefix = 'scope' }) => {
  useTranslation('geometry');
  const [scope, setScope] = useConstructionScope();
  return (
    <ToggleButtonGroup
      exclusive size="small" value={scope} aria-label={tx('scopeMenu')}
      onChange={(_e, next: ConstructionScope | null) => { if (next) setScope(next); }}
    >
      {SCOPES.map((m) => (
        <Tooltip key={m.id} title={tx(m.tipKey)}>
          <ToggleButton value={m.id} data-testid={`${testIdPrefix}-${m.id}`}
            sx={{ textTransform: 'none', fontSize: 12, py: 0.25, px: 1.5 }}>
            {tx(m.labelKey)}
          </ToggleButton>
        </Tooltip>
      ))}
    </ToggleButtonGroup>
  );
};

/** The same Active | Full choice and state as sub-tabs, for the Geometry panel's header row. */
export const ConstructionScopeTabs: React.FC<{ testIdPrefix?: string }> = ({ testIdPrefix = 'scope' }) => {
  useTranslation('geometry');
  const [scope, setScope] = useConstructionScope();
  return (
    <SubTabs value={scope} onChange={setScope} ariaLabel={tx('scopeMenu')}
      items={SCOPES.map((m) => ({ id: m.id, label: tx(m.labelKey), tip: tx(m.tipKey), testId: `${testIdPrefix}-${m.id}` }))} />
  );
};

export default ConstructionScopeSwitch;

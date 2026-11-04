/**
 * SolveGate - the ONE place the web closes a "start a calculation" control (owner 2026-11-04: nobody but MOTRES runs
 * simulations for now; server setting MOTRES_SOLVE_ADMIN_ONLY, default on).
 *
 *   <SolveGate><Button onClick={run}>Run</Button></SolveGate>
 *
 * An admin (or a server with the setting off: `/api/me` `canSolve`) gets the child untouched.  Anybody else gets it
 * disabled, wrapped in one tooltip with the sentence the server's refusal carries (common:solveClosed, EN + zh-CN).
 * `useCanSolve()` is the same answer for code that starts a calculation without a button (auto-runs, event handlers).
 * Nothing here is a security boundary - the server refuses the call anyway (auth.RoleGateMiddleware, jobs.require_solve_allowed).
 */
import React from 'react';
import { Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../contexts/AuthContext';

export const useCanSolve = (): boolean => useAuth().canSolve;

const SolveGate: React.FC<{ children: React.ReactElement; fullWidth?: boolean }> = ({ children, fullWidth }) => {
  const canSolve = useCanSolve();
  const { t } = useTranslation('common');
  if (canSolve) return children;
  return (
    <Tooltip title={t('solveClosed')}>
      <span style={{ display: fullWidth ? 'block' : 'inline-flex', cursor: 'not-allowed' }}>
        {React.cloneElement(children, { disabled: true, onClick: undefined } as Record<string, unknown>)}
      </span>
    </Tooltip>
  );
};

export default SolveGate;

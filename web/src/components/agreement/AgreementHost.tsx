// Mounted once next to the workspace: when a gated action answers 428 `agreement_required`, lib/apiAuth.ts asks this host
// (lib/platformAgreement.ts) to open a dialog; the answer (accepted or not) decides whether the action is repeated.  Two dialogs:
// the SIMULATION-START agreement of an account (the 428 carries `at: 'simulation'`; owner 2026-11-01) and the company click-through of a
// development (project, specification, design assistant).  Renders nothing until the first 428, and loads the dialog code only then.
import React, { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { setAgreementHost, setSimulationAgreementHost } from '../../lib/platformAgreement';

const AgreementDialog = lazy(() => import('./AgreementDialog'));
const SimulationAgreementDialog = lazy(() => import('./SimulationAgreementDialog'));

const AgreementHost: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [sim, setSim] = useState<{ version?: string } | null>(null);
  const resolver = useRef<((accepted: boolean) => void) | null>(null);
  const simResolver = useRef<((accepted: boolean) => void) | null>(null);

  useEffect(() => {
    setAgreementHost(() => new Promise<boolean>((resolve) => {
      resolver.current = resolve;
      setOpen(true);
    }));
    setSimulationAgreementHost((detail) => new Promise<boolean>((resolve) => {
      simResolver.current = resolve;
      setSim({ version: detail.version });
    }));
    return () => {
      setAgreementHost(null);
      setSimulationAgreementHost(null);
      resolver.current?.(false);
      resolver.current = null;
      simResolver.current?.(false);
      simResolver.current = null;
    };
  }, []);

  const done = (accepted: boolean) => {
    setOpen(false);
    resolver.current?.(accepted);
    resolver.current = null;
  };
  const simDone = (accepted: boolean) => {
    setSim(null);
    simResolver.current?.(accepted);
    simResolver.current = null;
  };

  if (sim) return <Suspense fallback={null}><SimulationAgreementDialog open version={sim.version} onClose={simDone} /></Suspense>;
  if (!open) return null;
  return <Suspense fallback={null}><AgreementDialog open onClose={done} /></Suspense>;
};

export default AgreementHost;

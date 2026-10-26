// Mounted once next to the workspace: when a gated action answers 428 `agreement_required`, lib/apiAuth.ts asks this host
// (lib/platformAgreement.ts requestAgreement) to open the Platform Agreement dialog; the answer (accepted or not) decides whether
// the action is repeated.  Renders nothing until the first 428, and loads the dialog code only then.
import React, { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { setAgreementHost } from '../../lib/platformAgreement';

const AgreementDialog = lazy(() => import('./AgreementDialog'));

const AgreementHost: React.FC = () => {
  const [open, setOpen] = useState(false);
  const resolver = useRef<((accepted: boolean) => void) | null>(null);

  useEffect(() => {
    setAgreementHost(() => new Promise<boolean>((resolve) => {
      resolver.current = resolve;
      setOpen(true);
    }));
    return () => {
      setAgreementHost(null);
      resolver.current?.(false);
      resolver.current = null;
    };
  }, []);

  const done = (accepted: boolean) => {
    setOpen(false);
    resolver.current?.(accepted);
    resolver.current = null;
  };

  if (!open) return null;
  return <Suspense fallback={null}><AgreementDialog open onClose={done} /></Suspense>;
};

export default AgreementHost;

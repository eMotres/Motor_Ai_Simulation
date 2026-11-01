// Mounted once next to the workspace (Root.tsx): after a sign-in asks the server whether this account has accepted the current texts
// and, if not, opens the "Updated terms" dialog once.  Nothing here blocks anything: a failed status call shows nothing, "Later" closes
// the dialog until the next sign-in (a new session id).  Loads the dialog code only when it is needed.
//
// The same dialog opens when a compute job is refused with HTTP 428 `terms_required` (the owner's deadline, env TERMS_REQUIRED_FROM, has
// passed): lib/platformAgreement.ts hands that to `setTermsHost`, and the refused call is repeated once if the account accepts.
import React, { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { fetchLegalStatus, laterSession, rememberLater } from '../../lib/legalApi';
import { shouldAskTerms, termsDeadline } from '../../lib/legalLogic';
import { setTermsHost } from '../../lib/platformAgreement';
import { getSessionSid } from '../../lib/localAuth';

const TermsUpdateDialog = lazy(() => import('./TermsUpdateDialog'));

const TermsUpdateHost: React.FC = () => {
  const { user, resolved } = useAuth();
  const email = user?.email ?? '';
  const [ask, setAsk] = useState<{ version: string; deadline: string | null } | null>(null);
  const gate = useRef<((accepted: boolean) => void) | null>(null);

  useEffect(() => {
    setAsk(null);
    if (!resolved || !email) return undefined;
    let live = true;
    void fetchLegalStatus().then((st) => {
      if (live && shouldAskTerms(st, getSessionSid(), laterSession())) setAsk({ version: st?.version ?? '', deadline: termsDeadline(st) });
    });
    return () => { live = false; };
  }, [resolved, email]);

  // A refused job start: open the dialog now, whatever "Later" said earlier, and answer the retry logic with the outcome.
  useEffect(() => {
    setTermsHost(async (detail) => {
      const st = await fetchLegalStatus();
      if (st && st.required !== true) return true;                       // accepted in the meantime (another tab)
      return new Promise<boolean>((resolve) => {
        gate.current = resolve;
        setAsk({ version: st?.version ?? detail.version ?? '', deadline: termsDeadline(st) ?? detail.from ?? null });
      });
    });
    return () => { setTermsHost(null); gate.current?.(false); gate.current = null; };
  }, []);

  const close = (accepted: boolean) => {
    setAsk(null);
    gate.current?.(accepted);
    gate.current = null;
  };

  if (!ask || !ask.version) return null;
  return (
    <Suspense fallback={null}>
      <TermsUpdateDialog open version={ask.version} deadline={ask.deadline} onAccepted={() => close(true)}
        onLater={() => { if (!gate.current) rememberLater(); close(false); }} />
    </Suspense>
  );
};

export default TermsUpdateHost;

// Mounted once next to the workspace (Root.tsx): after a sign-in asks the server whether this account has accepted the current texts
// and, if not, opens the "Updated terms" dialog once.  Nothing here blocks anything: a failed status call shows nothing, "Later" closes
// the dialog until the next sign-in (a new session id).  Loads the dialog code only when it is needed.
import React, { Suspense, lazy, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { fetchLegalStatus, laterSession, rememberLater } from '../../lib/legalApi';
import { shouldAskTerms } from '../../lib/legalLogic';
import { getSessionSid } from '../../lib/localAuth';

const TermsUpdateDialog = lazy(() => import('./TermsUpdateDialog'));

const TermsUpdateHost: React.FC = () => {
  const { user, resolved } = useAuth();
  const email = user?.email ?? '';
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    setVersion(null);
    if (!resolved || !email) return undefined;
    let live = true;
    void fetchLegalStatus().then((st) => {
      if (live && shouldAskTerms(st, getSessionSid(), laterSession())) setVersion(st?.version ?? null);
    });
    return () => { live = false; };
  }, [resolved, email]);

  if (!version) return null;
  return (
    <Suspense fallback={null}>
      <TermsUpdateDialog open version={version} onAccepted={() => setVersion(null)} onLater={() => { rememberLater(); setVersion(null); }} />
    </Suspense>
  );
};

export default TermsUpdateHost;

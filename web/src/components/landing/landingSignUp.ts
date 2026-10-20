/**
 * The landing page's ways in, in one place, so the hero buttons and the public motor cards start
 * the SAME flows.  Nothing here is new: each call opens the app's own LoginDialog
 * (contexts/AuthContext.signIn) exactly as the landing's buttons always did.
 *
 *   google  - the dialog on its Google tab (it signs in an existing account and creates a new one;
 *             the account is created by the first Google sign-in, instantly)
 *   email   - the dialog on "Create account" (e-mail + password; the confirmation mail only gates
 *             NDA / STEP / FEM / mailing, see the owner's sign-up rule)
 *   signIn  - the dialog in sign-in mode on its default tab
 *   primary - what the first button of the hero does: Google where the deployment has a client id,
 *             else the e-mail form
 */
import { useMemo } from 'react';
import { GOOGLE_CLIENT_ID } from '../../lib/localAuth';
import { useAuth } from '../../contexts/AuthContext';

export function useSignUp() {
  const { signIn } = useAuth();
  return useMemo(() => {
    const go = (opts: Parameters<typeof signIn>[0]) => { void signIn(opts).catch(() => {}); };
    const google = () => go({ tab: 'google' });
    const email = () => go({ tab: 'email', mode: 'register' });
    return { google, email, signIn: () => go({ mode: 'signin' }), primary: GOOGLE_CLIENT_ID ? google : email };
  }, [signIn]);
}

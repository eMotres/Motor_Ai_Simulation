// After sign-in: if the visitor pressed "Open in Configure" on a landing motor card, hand that
// choice to the app's OWN last-motor mechanism (lib/lastMotor.ts), the one that makes Configure open
// on the account's remembered machine.  No new route in Configure and no Configure file touched:
//   * the browser copy (what restoreLastMotor falls back on for an account with no server copy,
//     i.e. every brand-new account),
//   * the account's server copy (PUT /api/me/last_motor, best effort - the server checks that the
//     account may open that die and configuration; an account that may not simply keeps its own),
//   * ?tab=configure in the address bar and a one-shot flag for App's first-screen choice.
// Applies once: the remembered intent is removed by reading it.
import { rememberLastMotor, writeLocalLastMotor } from './lastMotor';
import { markOpenInConfigure, selectionOfIntent, takeLandingMotor } from './landingMotors';

export function applyLandingMotor(email: string | null | undefined): boolean {
  const intent = takeLandingMotor();
  const who = typeof email === 'string' ? email.trim().toLowerCase() : '';
  if (!intent || !who) return false;
  const selection = selectionOfIntent(intent);
  writeLocalLastMotor(who, selection);
  markOpenInConfigure();
  try {
    const u = new URL(window.location.href);
    u.searchParams.set('tab', 'configure');
    window.history.replaceState(null, '', u.toString());
  } catch { /* no address bar to update */ }
  void rememberLastMotor(who, selection).catch(() => { /* the browser copy still opens it */ });
  return true;
}

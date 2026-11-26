// A page tells the help assistant where the user is and what he is doing (owner 2026-11-26).  Registered while the page is mounted,
// cleared when it unmounts; read by lib/supportContext.buildSupportContext.  Same pattern as useConfigureSnapshot: the context is
// compared by its JSON, so a re-render with the same values does nothing.  Pass null to publish nothing.
//
// What may go in: ids, names the user already sees, modes, status chips, small counts, the last visible error.  Never a value of a
// geometry or of a result, a price or customer data (the server drops anything else and re-checks the role).
import { useEffect, useId } from 'react';
import { setHelpContext, type HelpContext } from '../../lib/supportContext';

export function useHelpContext(ctx: HelpContext | null): void {
  const id = useId();
  const key = ctx ? JSON.stringify(ctx) : '';
  useEffect(() => {
    setHelpContext(id, ctx);
    return () => setHelpContext(id, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, key]);
}

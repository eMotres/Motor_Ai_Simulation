/** What the address bar shows for a production page (pure, so a test can check it after a click).
 *  - Assembly tab with a step selected: the step link `/production/<die>/<config>/assembly?item=<step id>`, ALSO when the page was entered
 *    by a shared documents link (a click on a step chip must not lose the step).
 *  - Documents tab entered by a shared documents link: that link.
 *  - A tab entered by a link: that link.
 *  - otherwise: `null` = nothing to write; `'/'` = go back to the app root (the bar still holds a production link). */
import { appLinkPath } from '../../lib/appLink';
import { productionLinkPath } from '../../lib/productionLink';

export interface PageAddressInput {
  die: string; config: string; tab: string; stepSel: string | null; hasEntryLink: boolean;
  link?: { since?: string; ids?: string[]; invalidIds?: true } | null; initialTab?: string; itemId?: string;
  /** the 3D tab's selection as a link item (`step.<id>` / `part.<id>`, see helpers.modelItemOf), or null for the whole model */
  modelItem?: string | null;
}

export function pageAddress(i: PageAddressInput): string | null | 'ROOT' {
  // The 3D tab with a step or a part selected (owner 2026-11-25): `/production/<die>/<config>/3d?item=step.<id>` (or part.<id>), so a
  // reload and a copy keep the selection; the whole model alone has no address of its own, like the other tabs.
  if (i.tab === '3d' && i.modelItem) return appLinkPath({ kind: 'production', die: i.die, config: i.config, tab: '3d', item: i.modelItem });
  if (i.tab === 'assembly' && i.stepSel) return appLinkPath({ kind: 'production', die: i.die, config: i.config, tab: 'assembly', item: i.stepSel });
  // a link whose ids= list is invalid: the RAW address stays (a reload shows the same notice, never a silently plain list)
  if (i.hasEntryLink && i.tab === 'documents' && i.link?.invalidIds) return null;
  if (i.hasEntryLink && i.tab === 'documents') return productionLinkPath({ die: i.die, config: i.config, since: i.link?.since, ids: i.link?.ids });
  if (!i.hasEntryLink && i.initialTab && i.tab === i.initialTab) {
    return appLinkPath({ kind: 'production', die: i.die, config: i.config, tab: i.initialTab as never, ...(i.itemId && i.initialTab !== 'assembly' && i.initialTab !== '3d' ? { item: i.itemId } : {}) });
  }
  return 'ROOT';
}

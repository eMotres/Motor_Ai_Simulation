/**
 * Public links and fixed lists of the landing page (pure, no React), so a node
 * test can check them.  Documentation links point at the deploy branch on
 * GitHub; change BRANCH here when the default branch changes.
 */

export const REPO_URL = 'https://github.com/eMotres/Motor_Ai_Simulation';
export const BRANCH = 'pre-migration-freeze-2026-09-15';

/** A file in the repository, on the deploy branch. */
export function docUrl(path: string): string {
  return `${REPO_URL}/blob/${BRANCH}/${path.replace(/^\/+/, '')}`;
}

export interface DevLink { id: 'github' | 'sdk' | 'rules' | 'byo' | 'mcp'; href: string }

/** Links for developers and AI agents, in the order shown. */
export const DEV_LINKS: DevLink[] = [
  { id: 'github', href: REPO_URL },
  { id: 'sdk', href: docUrl('docs/COMPONENT_SDK.md') },
  { id: 'rules', href: docUrl('docs/MODULE_CONTRIBUTION_RULES.md') },
  { id: 'byo', href: docUrl('docs/BYO_COMPUTE.md') },
  { id: 'mcp', href: docUrl('docs/MCP_DISCOVERY.md') },
];

/** The strip under the headline: idea, the five steps, product. */
export const CHAIN_IDS = ['idea', 'design', 'simulation', 'configure', 'model3d', 'production',
  'product'] as const;

export const APP_IDS = ['aviation', 'robotics', 'marine', 'yours'] as const;
export const OPEN_IDS = ['source', 'agents', 'modules', 'compute'] as const;

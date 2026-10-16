/** Canonical URL handling for the Schemas tab. Does not require a router. */
export interface SchemasRouteResolution { href: string; activeTab: 'schemas' | null; redirected: boolean }

export function resolveSchemasRoute(input: string): SchemasRouteResolution {
  const url = new URL(input, 'https://schemas.invalid');
  const oldPath = /^\/system\/?$/i.test(url.pathname);
  if (oldPath) url.pathname = '/schemas';

  const tab = url.searchParams.get('tab');
  const oldTab = tab?.toLowerCase() === 'system';
  if (oldTab) url.searchParams.set('tab', 'schemas');

  const activeTab = /^\/schemas\/?$/i.test(url.pathname) || oldTab || tab?.toLowerCase() === 'schemas'
    ? 'schemas' : null;
  const href = `${url.pathname}${url.search}${url.hash}`;
  return { href, activeTab, redirected: oldPath || oldTab };
}

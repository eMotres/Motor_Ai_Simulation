/** Canonical URL handling for the Gears tab (direct link /gears or ?tab=gears). No router needed; pure for node --test. */
export function isGearsRoute(input: string): boolean {
  const url = new URL(input, 'https://gears.invalid');
  return /^\/gears\/?$/i.test(url.pathname) || url.searchParams.get('tab')?.toLowerCase() === 'gears';
}

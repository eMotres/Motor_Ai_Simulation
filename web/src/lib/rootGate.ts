/** Which front door does this visitor get?  Pure (no React), so a node test can run it.
 *  Used by `Root.tsx`: a signed-out visitor boots the small landing shell, everybody else
 *  the lazy workspace. */
export function showsLandingShell(hasUser: boolean, resolved: boolean, enforced: boolean): boolean {
  // Until /api/me has answered, an anonymous visitor sees the landing at once (the static
  // pre-rendered page is already there); an UNENFORCED backend (local dev) then hands over
  // to the workspace.
  return !hasUser && !(resolved && !enforced);
}

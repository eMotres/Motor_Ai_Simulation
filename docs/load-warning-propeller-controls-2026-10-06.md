# Motor load warning and propeller controls — 2026-10-06

User requested explanation for repeated admin-required toast on otherwise successful motor load, and the propeller picker below LOAD, visibly inactive in Manual.

Confirmed production log: PATCH /api/mesh/config returned 403. The fetch interceptor displays role-gate refusals globally; persistMeshBlock does not fail local duty application on an HTTP refusal. Thus loading can complete while a secondary mesh persistence request is refused.

Code supports an authentication timing race: enforced initially false, shared-context follower can run before /api/me resolves. This timing sequence was not reproduced in an authenticated browser. Fix gates follower on resolved role and shared-write eligibility; mesh persistence also rechecks permission at the write boundary. Backend permissions and global refusal notices remain intact.

Propeller row moved after LOAD. Manual disables and mutes propeller selection while ambient temperature remains editable. No calculation or physics changes.

Implementation: GPT-6 Luna, no escalation. Focused agent tests 19 passed; server access policy tests and Vite build checked separately. Authenticated UI verification pending. Isolated checkout used; owner's modified checkout and API on 8001 untouched.

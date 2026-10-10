# Claude API helper launch journal — 2026-10-09

- Haiku API minimal no-tools request: API_AGENT_OK, exit 0.
- Sonnet API implementation of helper and documentation: exit 0; same-model hardening: exit 0. No escalation.
- Haiku API initial wrapper: WRAPPER_API_OK, exit 0, USD 0.0013939.
- Haiku API final read-only wrapper: WRAPPER_FINAL_OK, exit 0, USD 0.001403275.
- Haiku API scoped write: exit 0, USD 0.00175751; root independently read back SCOPED_WRITE_OK.
- Synthetic transport verification: initial mock build failed; unsigned compiled mock refused by Windows Application Control. Not accepted, not bypassed. Fake test credential only; no real credential in artifacts.
- Root: parser and three invalid-path refusals passed; no application/API/deployment changes. Official setup record logged once: motor_ai_sim_claude_api_agent_setup_20261009_01.
## 2026-10-09 API helper hardening after Opus audit

Worker reported Codex GPT-5 (assignedrole Sonnet, runtimeidentitynotclaimedSonnet); root reviewed. Removed broad filesystemtools, -ReadPaths allowedfilesinline, existingOwnPathsfilesinline, childenvallowlist and pinneduserexe, timeoutkillproof and best-effort own-treefinalcleanup. Firstpatch omitted--tools whenempty: rootrejectedbeforeAPI; correctedexplicit--tools emptystring. Staleverification/defaultnullReadPaths fixed. Rootparser+threeforbiddenReadPathsnegative+argumentgate passed; oneactualapproveddocumentHaiku5.5callreturnedHARDENED_READONLY_OK/exit0. Originalautorefusal resolvedwithsameexactfileowner-approvedmanifest, no bypass. ReadWrite/cancellationtransport not repeated; unsignedmockAppControlrestriction retained. NoOSsandbox/signedexe guarantee. Rootcombinedtask motor_ai_sim_claude_api_helper_hardening_parent_20261009_01 officiallyloggedonce. NoAPIserver/coldcardchanges.

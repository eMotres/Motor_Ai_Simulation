"""Two regions (EU + US) — stage 1 (docs/regions/DESIGN.md).

Everything here is INACTIVE in single-region mode: with no ``MOTRES_PEER_URL`` and no peer secret configured the
server behaves exactly as before; the only visible additions are the admin region column / override and the region
card of Admin -> Servers (which then shows this server alone).

Modules:
  config            region of this server, the peer, the public base URLs, the feature switches (env, read per call)
  store             the home region of an account (users.json) and the global account directory
  classifier        proposes a home region for every account, with the reason (dry run by default)
  peer              HMAC-signed server-to-server requests (sign / verify / client)
  status            this server's health, version, load, queue depth and disk
  catalog_manifest  hashes of the open shared library that EU replicates read-only to US, plus the per-die extracts of the
                    partial files (duty results, end-effect passports, die access)
  catalog_mirror    the US half: plan / apply / merge of the mirror job (scripts/mirror_catalog_us.sh)
  directory_sync    pull the peer's account directory entries (5-minute timer)
  foreign           cross-region compute, both halves: dispatch + token map at home, foreign-job store + hand-back at the peer
  compute           the cross-region compute pool: where a job runs, the anonymised job payload, the opaque token map
  guard             the wrong-region answer (421) for a signed-in account on the other region's server, per account
                    after its migration; the write freeze (423) while its bundle is built
  routing           where an e-mail must be served, and the 421 of the sign-in routes (stage 3)
  handoff           the single-use sign-in ticket that carries a verified caller to the other region (stage 3)
  migrate           the per-account migration: export -> transfer -> import -> finish, delete-source (stage 3)
"""

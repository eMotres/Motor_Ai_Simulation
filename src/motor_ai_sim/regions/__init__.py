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
  catalog_manifest  hashes of the shared library that EU replicates read-only to US
  compute           the cross-region compute pool: where a job runs, the anonymised job payload, the opaque token map
  guard             the wrong-region answer (421) for a signed-in account on the other region's server
"""

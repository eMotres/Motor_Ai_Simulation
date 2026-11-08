"""Region configuration of THIS server (env, read per call so a test can flip it).

| variable                       | default                    | meaning                                                         |
|--------------------------------|----------------------------|-----------------------------------------------------------------|
| ``MOTRES_REGION``              | ``eu``                     | the region this server serves: ``eu`` or ``us``                 |
| ``MOTRES_PEER_URL``            | unset = single-region mode | base URL of the other region's API (over WireGuard in stage 2)  |
| ``MOTRES_PEER_REGION``         | the other region           | region of the peer (``eu`` / ``us``)                            |
| ``MOTRES_PEER_HMAC_SECRET``    | unset                      | shared secret of the server-to-server channel (never in git)    |
| ``MOTRES_PEER_HMAC_SECRET_FILE``| unset                     | ... or a file holding it (0600, e.g. under /etc/motres)         |
| ``MOTRES_REGION_BASE_URL_EU``  | ``https://aerostator.com`` | public base URL of the EU region (what a redirect points at)    |
| ``MOTRES_REGION_BASE_URL_US``  | ``https://us.aerostator.com`` | public base URL of the US region                             |
| ``MOTRES_REGION_GUARD``        | off                        | ``1`` turns the wrong-region answer on (needs a configured peer)|

Single-region mode = no peer URL or no secret: every peer route answers 404, the peer client never sends anything,
the guard passes every request.  That is the state of the current EU server after this change is deployed.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

REGIONS = ("eu", "us")
DEFAULT_REGION = "eu"

ENV_REGION = "MOTRES_REGION"
ENV_PEER_URL = "MOTRES_PEER_URL"
ENV_PEER_REGION = "MOTRES_PEER_REGION"
ENV_PEER_SECRET = "MOTRES_PEER_HMAC_SECRET"
ENV_PEER_SECRET_FILE = "MOTRES_PEER_HMAC_SECRET_FILE"
ENV_GUARD = "MOTRES_REGION_GUARD"
_ENV_BASE = {"eu": "MOTRES_REGION_BASE_URL_EU", "us": "MOTRES_REGION_BASE_URL_US"}
_DEFAULT_BASE = {"eu": "https://aerostator.com", "us": "https://us.aerostator.com"}

#: The shortest secret accepted: a short one is treated as "not configured" (fail closed, never weakly open).
MIN_SECRET_LEN = 32


def norm_region(value, default: Optional[str] = None) -> Optional[str]:
    """``"EU"`` / ``" us "`` -> ``"eu"`` / ``"us"``; anything else -> ``default``."""
    v = str(value or "").strip().lower()
    return v if v in REGIONS else default


def _on(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in ("1", "true", "yes", "on")


def local_region() -> str:
    """The region this server serves.  An unknown value falls back to ``eu`` (today's single server)."""
    return norm_region(os.environ.get(ENV_REGION), DEFAULT_REGION) or DEFAULT_REGION


def other_region(region: str) -> str:
    return "us" if norm_region(region, DEFAULT_REGION) == "eu" else "eu"


def peer_url() -> str:
    return str(os.environ.get(ENV_PEER_URL, "") or "").strip().rstrip("/")


def peer_region() -> str:
    return norm_region(os.environ.get(ENV_PEER_REGION), None) or other_region(local_region())


def peer_secret() -> str:
    """The shared HMAC secret, or ``""`` when it is absent or too short (then the channel stays closed)."""
    raw = str(os.environ.get(ENV_PEER_SECRET, "") or "").strip()
    if not raw:
        f = str(os.environ.get(ENV_PEER_SECRET_FILE, "") or "").strip()
        if f:
            try:
                raw = Path(f).read_text(encoding="utf-8").strip()
            except OSError:
                raw = ""
    return raw if len(raw) >= MIN_SECRET_LEN else ""


def peer_configured() -> bool:
    """Multi-region mode: a peer URL AND a usable secret.  Anything less is single-region mode."""
    return bool(peer_url()) and bool(peer_secret()) and peer_region() != local_region()


def guard_enabled() -> bool:
    """The wrong-region answer is on only when asked for AND a peer exists to send the caller to."""
    return _on(ENV_GUARD) and peer_configured()


def base_url(region: str) -> str:
    r = norm_region(region, DEFAULT_REGION) or DEFAULT_REGION
    return (str(os.environ.get(_ENV_BASE[r], "") or "").strip() or _DEFAULT_BASE[r]).rstrip("/")


def describe() -> Dict[str, object]:
    """What the admin and the public ``/api/region`` see: no URL of the peer's private address, no secret."""
    return {"region": local_region(), "mode": "multi" if peer_configured() else "single",
            "peer_region": peer_region() if peer_configured() else None,
            "guard": guard_enabled(),
            "base_urls": {r: base_url(r) for r in REGIONS}}

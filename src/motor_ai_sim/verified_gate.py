"""What an account with an UNCONFIRMED e-mail address may not do (owner decision 2026-10-15: "let them in right away, confirm later").

A password account is created and signed in at once; the confirmation link only unlocks a short list of actions.  Everything else
(catalogue, Configure, the 3-D view, the assistant, tickets, ...) works from the first minute.  The list is HERE, in one place, as
(method, path pattern, group) rows; ``auth.RoleGateMiddleware`` refuses a matching request from an unconfirmed caller with

    403  {"code": "email_unverified", "params": {"email": "<their address>", "gate": "<group>"}, "detail": "..."}

*before* the role check, so every gated route answers the same way whatever the caller's role.

Groups:

* ``nda``      online NDA signing
* ``step``     the STEP download (and with it the 3-D tools that come after the NDA)
* ``compute``  FEM / server compute: simulation runs, mesh builds, mechanical and thermal solves, coupled runs, the kernel,
               optimizer and scans, 3-D passport solves, the BYO-compute job queue
* ``mailing``  the e-mail topics (marketing consent; mail needs a proven address)
* ``tokens``   credentials for agents and machines: agent keys, the OAuth approval of an MCP connector, compute-node tokens
* role upgrades are refused in ``users.update_user`` (an admin cannot make an unconfirmed account an engineer or admin)

A caller that is not signed in, or whose address is confirmed (every account made before 2026-09-28 has no flag and counts as
confirmed, Google sign-ins are confirmed by Google), is never touched by this module.
"""
from __future__ import annotations

import re
from typing import Optional

#: (methods or None for any, path regex, group).  Paths are matched on ``request.url.path`` without a trailing slash.
GATES: tuple[tuple[Optional[frozenset], "re.Pattern[str]", str], ...] = tuple(
    (frozenset(m) if m else None, re.compile(p), g) for m, p, g in (
        (("POST",), r"^/api/nda/sign$", "nda"),
        (("GET",), r"^/api/model3d/[^/]+/[^/]+/model\.step$", "step"),
        # compute
        (None, r"^/api/simulation/physics/(fem_transient|fem_field2d|bench_ldq|harm_screening)$", "compute"),
        (None, r"^/api/simulation/mesh/build2d(_sliding_band)?$", "compute"),
        (("POST",), r"^/api/simulation/run$", "compute"),
        (None, r"^/api/mechanical/(rotor_stress|modes|critical_speeds|mesh|limit_speed)$", "compute"),
        (None, r"^/api/thermal/(field|coupled|mesh|duty_cycle)$", "compute"),
        (("POST",), r"^/api/coupled/(run|constants_20c|continuous_rating)$", "compute"),
        (("POST",), r"^/api/kernel/(run|study)$", "compute"),
        (("POST",), r"^/api/optimization/(?!saved)[^/]+.*$", "compute"),
        (("POST",), r"^/api/static3d/solve$", "compute"),
        (("POST",), r"^/api/pipeline/generate$", "compute"),
        (("POST",), r"^/api/nodes/jobs/submit(-shared)?$", "compute"),
        # the e-mail topics
        (("PUT",), r"^/api/me/comm-prefs$", "mailing"),
        # credentials for agents and machines
        (("POST",), r"^/api/agent_keys$", "tokens"),
        (("POST",), r"^/api/oauth/requests/[^/]+$", "tokens"),
        (("POST",), r"^/api/nodes$", "tokens"),
    ))


def gate_for(method: str, path: str) -> Optional[str]:
    """The group an unconfirmed account is refused for on this request, or None when the route is open to it."""
    p = (path or "/").rstrip("/") or "/"
    m = (method or "").upper()
    for methods, rx, group in GATES:
        if (methods is None or m in methods) and rx.match(p):
            return group
    return None


def unverified_email(user: Optional[dict]) -> Optional[str]:
    """The address of a signed-in caller whose e-mail is NOT confirmed, else None."""
    email = str((user or {}).get("email") or "").strip().lower()
    if not email:
        return None
    from motor_ai_sim import users as U
    rec = U.get_user(email)
    return email if rec is not None and not U.is_verified(rec) else None


def refusal_body(email: str, group: str) -> dict:
    return {
        "detail": f"Confirm your e-mail to use this - we sent a link to {email}. Not there? Check spam, or resend the link / change the address.",
        "code": "email_unverified",
        "params": {"email": email, "gate": group},
    }

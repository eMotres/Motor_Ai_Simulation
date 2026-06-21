"""users — capability `users`: identity, tiers/roles, quotas, the design library.

Cross-cutting service module. Wraps the existing auth/account/admin stack; here it
exposes the tier model so the portal can build role-gated UI from a manifest. The
heavy logic stays in routes/account + routes/admin + auth.

Agent brief: own identity/tenancy/quotas/library only. Never compute physics.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts import CONTRACTS_VERSION
from .base import ModuleManifest, UIContribution


class UsersModule:
    NAME, CAPABILITY, VERSION = "users", "users", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="ui",
            contracts_version=CONTRACTS_VERSION, depends_on=[],
            summary="Auth, tiers/roles (free/pro/team/admin), quotas, saved-design library",
            ui=UIContribution(panel_id="admin", title="Admin",
                              frontend_module="components/admin/AdminPanel", order=90))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"tiers": ["free", "pro", "team", "admin"],
                "note": "identity/quotas served by routes/account + routes/admin + auth"}

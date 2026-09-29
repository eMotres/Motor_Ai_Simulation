"""users — capability `users`: identity, roles, fair-use limits, the design library.

Cross-cutting service module. Wraps the existing auth/account/admin stack; here it
exposes the role model so the portal can build role-gated UI from a manifest. The
heavy logic stays in routes/account + routes/admin + auth.

Agent brief: own identity/tenancy/limits/library only. Never compute physics.
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
            inputs=[], outputs=["UserContext"],
            summary="Auth, roles (user/admin), fair-use limits, saved-design library",
            ui=UIContribution(panel_id="admin", title="Admin",
                              frontend_module="components/admin/AdminPanel", order=90))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"roles": ["user", "admin"],
                "note": "identity/fair-use limits served by routes/account + routes/admin + auth"}

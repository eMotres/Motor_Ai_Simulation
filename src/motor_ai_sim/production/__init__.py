"""Production module (stage 1): parts, BOM, process, documents, discussion, proposals and revisions of ONE motor configuration,
shared between the owner's engineers and an OEM producer.  Contract: docs/PRODUCTION_MODULE_DESIGN.md."""
from __future__ import annotations

VERSION = "1"
LANGS = ("en", "zh")
MEMBER_ROLES = ("owner", "engineer", "oem")
#: Members who may change design data (parts, BOM, process, revisions, proposal decisions).
EDIT_ROLES = ("owner", "engineer")
PRODUCTION_STATUSES = ("active", "paused", "archived")
PART_KINDS = ("made", "purchased", "consumable")
PROPOSAL_STATUSES = ("open", "accepted", "rejected", "implemented")
SECTION_KINDS = ("fits", "assembly", "winding", "tests")
TEXT_STATUSES = ("machine", "confirmed", "pending")

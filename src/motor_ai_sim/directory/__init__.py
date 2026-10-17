"""Companies and People Directory for AeroStator Admin.

Contract: BRIEF_companies_people_catalog.md
"""
from __future__ import annotations

from motor_ai_sim.directory.routes import router
from motor_ai_sim.directory.service import (
    backfill_nda,
    ingest_nda_record,
    on_user_confirmed,
    on_user_registered,
)

__all__ = [
    "router",
    "ingest_nda_record",
    "backfill_nda",
    "on_user_registered",
    "on_user_confirmed",
]

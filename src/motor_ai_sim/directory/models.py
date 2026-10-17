"""Data models and constants for the Companies & People Directory.

Contract: BRIEF_companies_people_catalog.md
- Company, Person, Affiliation, CommercialDocument
- Strict validation: Never store bank account numbers / SWIFT details.
"""
from __future__ import annotations

import re
import secrets
import time
from typing import Any, Optional

COMPANY_ROLES = ("oem_producer", "supplier", "customer", "partner")
AFFILIATION_STATUSES = ("proposed", "confirmed", "ended")
DOCUMENT_KINDS = ("proforma", "invoice", "PO")
DOCUMENT_STATUSES = ("open", "paid", "cancelled")
DIRECTORY_SOURCES = ("manual", "nda", "verification", "signup", "invitation", "domain_match")

# Forbidden bank field keys: Never store bank account numbers / SWIFT details.
FORBIDDEN_BANK_KEYS = frozenset({
    "bank",
    "bank_name",
    "bank_account",
    "bank_details",
    "bank_branch",
    "iban",
    "swift",
    "swift_bic",
    "bic",
    "routing_number",
    "account_number",
    "account_no",
    "sort_code",
    "wire_transfer",
    "wire_details",
})

# Patterns that indicate bank details in free-text fields
_IBAN_RE = re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}\b", re.I)
_SWIFT_RE = re.compile(r"\b[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?\b", re.I)


def check_no_bank_fields(data: Any, path: str = "") -> None:
    """Recursively verify that no bank account numbers or SWIFT details are included in the payload.

    Raises ValueError if any bank-related field key or bank detail is detected.
    """
    if isinstance(data, dict):
        for k, v in data.items():
            key_norm = str(k).strip().lower().replace("-", "_")
            if key_norm in FORBIDDEN_BANK_KEYS or any(bk in key_norm for bk in ("bank_acc", "bank_num", "iban", "swift", "bic_code", "routing_no")):
                raise ValueError("Bank details and SWIFT/IBAN fields are strictly prohibited in the directory catalog.")
            check_no_bank_fields(v, path=f"{path}.{k}" if path else str(k))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            check_no_bank_fields(item, path=f"{path}[{i}]")
    elif isinstance(data, str):
        # We also check for explicit SWIFT/IBAN keys in colon-separated text lines like "IBAN: ..."
        if re.search(r"\b(iban|swift|bic|account\s+no|routing\s+no)\s*[:=]", data, re.I):
            raise ValueError("Bank account or SWIFT details detected in text: bank details cannot be stored.")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    """Generate a clean URL-safe identifier e.g. c_20261017_abcd1234."""
    date_part = time.strftime("%Y%m%d", time.gmtime())
    token = secrets.token_hex(4)
    return f"{prefix}_{date_part}_{token}"

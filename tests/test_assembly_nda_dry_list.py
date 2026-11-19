"""The dry list of the factory members who would lose the Assembly tab behind the NDA gate."""
from __future__ import annotations

from tests.test_production_access import ADMIN, CONFIG, DIE, ENGINEER, OEM_NDA, prod_env  # noqa: F401  (fixture)


def test_the_dry_list_names_the_factory_members_who_would_lose_the_view(prod_env):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import assembly_nda_dry_list as D
    from tests.test_production_access import OEM_NO_NDA
    payload = {"title": {"en": "P", "zh": "P", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}}, "order_quantity": 10,
               "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}, {"email": OEM_NO_NDA, "role": "oem"}]}
    assert prod_env["client"].post("/api/admin/production/%s/%s" % (DIE, CONFIG), json=payload, headers=prod_env["admin"]).status_code in (200, 201)
    (row,) = D.members_without_view()
    assert row["oem_members"] == 2 and row["would_lose"] == [OEM_NO_NDA.lower()]

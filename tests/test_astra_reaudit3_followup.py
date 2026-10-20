# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Astra re-audit #3 follow-up probe B1 (cad_overrides), adapted to the fixed behaviour."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from tests.test_config_visibility import client, DIE, PUB, PLAIN
from tests.test_reaudit_r1_r2_r3 import env, gate, planted, FACTORY
from tests.test_reaudit2_r5_r6_r8 import layered, _export, _ws_cfg
from tests.test_astra_reaudit3_independent import evidence


@pytest.mark.parametrize("who,email", [("plain", PLAIN), ("factory", FACTORY)])
def test_cad_overrides_no_longer_survive_controller_copy_and_zip(layered, who, email):
    # cad_params.OVERRIDES_KEY is an actual protected producer: construction
    # _change saves this block, and GET cad_params exposes it only behind NDA.
    from motor_ai_sim import cad_params
    from motor_ai_sim.routes import family
    source = layered["cfg_path"]
    doc = yaml.safe_load(source.read_text("utf-8"))
    overrides = {"shaft_shoulder_len": 3.14159}
    assert cad_params.clean_overrides(overrides) == overrides
    doc[cad_params.OVERRIDES_KEY] = overrides
    source.write_text(yaml.safe_dump(doc), encoding="utf-8")
    before = source.read_bytes()
    family._TREE_CACHE.clear()
    denied = client.get(f"/api/family/config/{DIE}/{PUB}/cad_params", headers=layered[who])
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "nda_required"
    patched = client.patch(f"/api/family/config/{DIE}/{PUB}/controller",
                           json={}, headers=layered[who])
    assert patched.status_code == 200
    copied = yaml.safe_load(_ws_cfg(email).read_text("utf-8"))
    assert "thermal_path" not in copied["bearings"]
    assert "cad_overrides" not in copied            # B1, flipped
    with _export(layered[who]) as archive:
        member = f"workspace/dies/{DIE}/{PUB}.yaml"
        packed = yaml.safe_load(archive.read(member))
        assert "cad_overrides" not in packed
    assert source.read_bytes() == before




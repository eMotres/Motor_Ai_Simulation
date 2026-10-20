# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Astra re-audit #2 R5 repro, flipped: layered copy-on-write + account export no longer carry the CAD basis.

All catalog/workspace/user stores come from the existing tmp-backed privacy
fixtures. This exercises HTTP PATCH plus the real signed ZIP export; no server
process, network, or solver is started.
"""
from __future__ import annotations

import io
import sqlite3
import zipfile

import pytest
import yaml

from tests.test_config_visibility import DIE, PLAIN, PUB, client
from tests.test_reaudit_r1_r2_r3 import FACTORY, env, gate, planted


@pytest.mark.parametrize("who,email", [("plain", PLAIN), ("factory", FACTORY)])
def test_controller_copy_on_write_no_longer_exports_full_construction(
        planted, who, email, monkeypatch, tmp_path):
    from motor_ai_sim import account_lifecycle as AL
    from motor_ai_sim import admin_audit as AA
    from motor_ai_sim import agent_keys as AK
    from motor_ai_sim import comm_prefs as CP
    from motor_ai_sim import construction_privacy as privacy
    from motor_ai_sim import newsletter as NL
    from motor_ai_sim import oauth as OAuth
    from motor_ai_sim import sessions as Sessions
    from motor_ai_sim import support_store as Support
    from motor_ai_sim import usage_stats as Usage
    from motor_ai_sim.routes import construction

    if not planted["layered"]:
        pytest.skip("copy-on-write and per-account export apply to layered stores")

    source = planted["dies_dir"] / DIE / f"{PUB}.yaml"
    source_doc = yaml.safe_load(source.read_text("utf-8"))
    secret_fraction = source_doc["bearings"]["thermal_path"]["rotor_face_open_fraction"]
    assert secret_fraction == 0.4321
    source_before = source.read_bytes()

    # The export metadata is unrelated to the workspace archive regression.
    # Keep all reads in-memory or under tmp_path; leave the real workspace ZIP
    # traversal and both HTTP export endpoints intact.
    monkeypatch.setattr(NL, "_load", lambda: {"subscribers": {}})
    monkeypatch.setattr(Support, "list_access_requests", lambda: [])
    monkeypatch.setattr(Sessions, "list_for", lambda _email: [])
    monkeypatch.setattr(Sessions, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setattr(AK, "list_keys", lambda _email: [])
    monkeypatch.setattr(AK, "audit_path", lambda: tmp_path / "agent_audit.jsonl")
    monkeypatch.setattr(OAuth, "list_grants", lambda _email: [])
    monkeypatch.setattr(CP, "export_for", lambda _email: {})
    monkeypatch.setattr(AA, "for_subject", lambda _email, limit=10000: [])

    def memory_usage_db():
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE activity (day,event,key,n,user)")
        con.execute("CREATE TABLE storage (day,category,bytes,user)")
        con.execute("CREATE TABLE usage (run_id,ts_start,ts_end,kind,machine,wall_s,cpu_s,status,user)")
        return con

    monkeypatch.setattr(Usage, "_db", memory_usage_db)
    real_build = AL.build_export
    export_path = tmp_path / f"{who}-export.zip"

    def build_in_tmp(requested_email, dest=None):
        assert requested_email.lower() == email.lower()
        return real_build(requested_email, dest=export_path)

    monkeypatch.setattr(AL, "build_export", build_in_tmp)

    authorization = planted[who]["Authorization"]
    assert privacy.may_see_full_construction(authorization) is False
    denied = client.get(f"/api/family/config/{DIE}/{PUB}/cad_params",
                        headers=planted[who])
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "nda_required"

    patched = client.patch(
        f"/api/family/config/{DIE}/{PUB}/controller", json={},
        headers=planted[who])
    assert patched.status_code == 200, patched.text[:500]
    workspace_cfg = AL._ws_dirs(email)["workspace"] / "dies" / DIE / f"{PUB}.yaml"
    assert workspace_cfg.is_file()
    copied = yaml.safe_load(workspace_cfg.read_text("utf-8"))
    # R5, flipped: the copy holds the public bearings only
    assert "thermal_path" not in copied["bearings"] and copied["bearings"]["A"]["card"]
    assert source.read_bytes() == source_before  # copy-on-write leaves shared source intact

    link = client.post("/api/account/export", headers=planted[who])
    assert link.status_code == 200, link.text[:300]
    exported = client.get(link.json()["url"])
    assert exported.status_code == 200, exported.text[:300]
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        member = f"workspace/dies/{DIE}/{PUB}.yaml"
        assert member in archive.namelist()
        packed = archive.read(member).decode("utf-8")
    assert "thermal_path" not in packed and "0.4321" not in packed

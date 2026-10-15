# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""WITHOUT the NDA a user sees ONLY the SIMPLIFIED 3-D model; WITH it (the gate of the full STEP: ``model3d.can_download`` - NDA signed AND
applicant verification VERIFIED; the owner and admins always) the FULL model with the whole part tree (owner 2026-10-15).  This replaces
the 2026-10-13 "one model for everybody" viewing rule.

The server is the gate: the full GLB and the part trees answer 403 ``nda_required``; the simplified model is shown by ``simplified_view.glb``
(not stamped, not logged: viewing is not downloading); ``/api/model3d/models`` says per entry which model THIS caller gets and never falls back
to the full one; a hidden or ungranted configuration stays a 404."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import model3d as M
from motor_ai_sim.api import app
from tests.test_cad_simplified_access import CFG, DIE, GLB, MID, SGLB, _build_tree, _log, env  # noqa: F401  (the fixture)

client = TestClient(app)


def _view(env, who):
    return client.get("/api/model3d/models", headers=env[who]).json()["models"][0]


def test_a_plain_user_without_nda_gets_the_simplified_view_only(env):
    u = env["url"]
    r = client.get(u + "/model.glb", headers=env["user"])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "nda_required" and "NDA" in r.json()["detail"]["message"]
    p = client.get(u + "/parts", headers=env["user"])
    assert p.status_code == 403 and p.json()["detail"]["code"] == "nda_required"
    v = client.get(u + "/simplified_view.glb", headers=env["user"])
    assert v.status_code == 200 and v.content == SGLB and v.content != GLB
    assert v.headers["cache-control"].startswith("private") and "max-age" in v.headers["cache-control"] and "no-store" not in v.headers["cache-control"]
    assert v.headers["etag"] == '"%s"' % M.sha256_bytes(SGLB)
    assert _log(env) == []                                           # viewing is not downloading: nothing logged, nothing stamped
    m = _view(env, "user")
    assert m["view"] == "simplified" and m["view_reason"] == "" and m["simplified_available"] is True
    assert m["simplified_glb_sha256"] == M.sha256_bytes(SGLB)


def test_nda_signed_but_not_verified_is_the_same_as_without_nda(env, monkeypatch):
    from motor_ai_sim import verification as V
    monkeypatch.setattr(V, "access_ok", lambda *a, **k: False)       # signed the NDA online, verification not complete
    u = env["url"]
    assert client.get(u + "/model.glb", headers=env["nda"]).status_code == 403
    assert client.get(u + "/parts", headers=env["nda"]).json()["detail"]["code"] == "nda_required"
    assert client.get(u + "/simplified_view.glb", headers=env["nda"]).status_code == 200
    assert _view(env, "nda")["view"] == "simplified"
    assert client.get("/api/model3d/models", headers=env["nda"]).json()["can_download"] is False


def test_a_verified_account_gets_the_full_model(env, monkeypatch):
    from motor_ai_sim import verification as V
    monkeypatch.setattr(V, "access_ok", lambda *a, **k: True)        # NDA + verification VERIFIED
    u = env["url"]
    g = client.get(u + "/model.glb", headers=env["user"])
    assert g.status_code == 200 and g.content == GLB
    assert _view(env, "user")["view"] == "full"
    assert client.get(u + "/simplified_view.glb", headers=env["user"]).status_code == 200      # the simplified view stays available


def test_the_admin_and_the_flagged_owner_account_get_the_full_model(env):
    u = env["url"]
    assert client.get(u + "/model.glb", headers=env["admin"]).content == GLB
    assert _view(env, "admin")["view"] == "full"
    assert client.get(u + "/model.glb", headers=env["nda"]).status_code == 200                  # "NDA signed" flag set by the admin = the gate of the STEP
    assert _view(env, "nda")["view"] == "full"


def test_a_model_without_simplified_files_is_never_replaced_by_the_full_one(env):
    import shutil
    shutil.rmtree(env["cad"])
    _build_tree(env["cad"], with_simplified=False)
    m = _view(env, "user")
    assert m["view"] == "none" and "simplified model not built yet" in m["view_reason"]
    assert client.get(env["url"] + "/simplified_view.glb", headers=env["user"]).status_code == 404
    assert client.get(env["url"] + "/model.glb", headers=env["user"]).status_code == 403          # no fallback to full
    assert _view(env, "admin")["view"] == "full"                                                  # the owner still has the full one


def test_hidden_ungranted_and_anonymous_keep_their_answers(env, monkeypatch):
    u = env["url"]
    for path in ("/model.glb", "/parts", "/simplified_view.glb"):
        assert client.get(u + path).status_code == 401                                          # anonymous
        assert client.get(u + path, headers=env["other"]).status_code == 404                    # not granted: 404, not 403
        assert client.get(f"/api/model3d/{DIE}/NOSUCH" + path, headers=env["user"]).status_code == 404
    from motor_ai_sim import motor_access as MA
    monkeypatch.setattr(MA, "may_see_config", lambda access, die, cfg: cfg != CFG)               # a hidden configuration
    for who in ("user", "nda", "admin"):
        for path in ("/model.glb", "/parts", "/simplified_view.glb"):
            assert client.get(u + path, headers=env[who]).status_code == 404, (who, path)
    assert client.get("/api/model3d/models", headers=env["user"]).json()["models"] == []


def test_the_download_routes_are_unchanged(env):
    u = env["url"]
    assert client.get(u + "/model.step", headers=env["user"]).status_code == 403                  # the full STEP: as before
    assert client.get(u + "/simplified.step", headers=env["user"]).status_code == 200            # the simplified downloads: open and logged
    assert [r["kind"] for r in _log(env)] == ["simplified_step"]


def test_the_geometry_tab_part_tree_follows_the_same_gate(env, monkeypatch):
    """GET /api/family/die/<die>/part_tree (the Geometry / Materials Full tree) answers 403 nda_required without the gate."""
    from motor_ai_sim.routes import construction as C
    monkeypatch.setattr(C, "_resolve_cfg", lambda die, config, who: CFG)
    from motor_ai_sim.routes import family as fam
    monkeypatch.setattr(fam, "_require_die_access", lambda die, authorization: {"id": "x", "is_admin": False})
    r = client.get(f"/api/family/die/{DIE}/part_tree", params={"config": CFG}, headers=env["user"])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "nda_required"

# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Codex Astra RE-AUDIT 2026-10-20 — R1, R2, R3 (all on #340's bearing network).

R1 (BLOCKER) — the CAD-measured bearing basis (``bearings.thermal_path``: shaft
segments, holder sections, contact / film areas, the rotor face's open fraction)
is FULL-CONSTRUCTION data.  It leaves the server only for a caller who passes the
NDA + verification gate (``model3d.can_download``), on every output: the catalog
tree (and its memo, in either cache order), the editor context, the bearings
save, the thermal payloads (``/api/thermal/last`` here), any JSON route (the
middleware), the MCP and the support assistant's tree.  Flat and layered.

R2 (HIGH) — the friction made in the SOLVED bearing nodes reaches the lumped
transient: the bearing network's delta carries its Norton sources, and the
lumped network evaluated at the steady map's own temperatures is in balance on
every node (it was short by exactly the friction before).

R3 (MEDIUM) — saving the bearings keeps the CAD thermal_path while the cards are
unchanged and invalidates it, explicitly, when a card changes or the pair is
cleared.

Synthetic stores and closed-form networks only: no FEM, no CAD build.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest
import yaml

from tests.test_config_visibility import ADMIN, DIE, PUB, client, env  # noqa: F401  (env = the fixture)
from tests.test_construction_nda_gate import gate  # noqa: F401  (the fixture)

FACTORY = "factory@example.com"      # role oem, all motors, no NDA
SECRET = {"A": {"shaft_segments_mm": [[7.77777, 13.13131]],
                "holder": {"contact_core_mm2": 31.4159, "film_area_mm2": 2718.28,
                           "char_len_mm": 29.9792,
                           "arm_segments_mm": [[1.23456, 9.87654]]}},
          "rotor_face_open_fraction": 0.4321}
NEEDLES = ("7.77777", "13.13131", "31.4159", "2718.28", "29.9792", "1.23456",
           "9.87654", "0.4321", "thermal_path")
CARD_618_8 = {"name": "618/8-2Z", "d": 8, "D": 16, "B": 4, "d_m": 12,
              "n_balls": 10, "ball_d": 2.0}


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def planted(gate, monkeypatch):
    """The ``gate`` catalog with the CAD basis on the public configuration and an
    OEM (factory) account without an NDA."""
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family
    U.create_user(FACTORY, "password-factory", role="oem", name="Factory")
    U.set_motor_grants(FACTORY, all_motors=True)
    path = gate["dies_dir"] / DIE / f"{PUB}.yaml"
    cfg = yaml.safe_load(path.read_text("utf-8"))
    cfg["bearings"]["thermal_path"] = SECRET
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    family._TREE_CACHE.clear()
    # the full-mass overlay builds the 2-D geometry: not what is tested here
    monkeypatch.setattr(family, "_overlay_mass", lambda die, c: c)
    hdr = {"Authorization": "Bearer " + U.issue_token(FACTORY)}
    return dict(gate, factory=hdr, cfg_path=path)


NO_GATE = ("plain", "online", "factory")
WITH_GATE = ("verified", "flagged", "admin")


def _row(resp):
    die = next(d for d in resp.json()["dies"] if d["name"] == DIE)
    return next(c for c in die["configs"] if c["name"] == PUB)


def _clean(text: str) -> bool:
    return not any(n in text for n in NEEDLES)


# ── R1: the tree ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("who", NO_GATE)
def test_the_tree_withholds_the_cad_basis_without_the_gate(planted, who):
    from motor_ai_sim import auth, model3d, users
    ident = auth.caller_identity(planted[who]["Authorization"])
    assert not model3d.can_download(ident, users.get_user(ident["id"]))
    r = client.get("/api/family/tree", headers=planted[who])
    assert r.status_code == 200, r.text[:300]
    row = _row(r)
    # the public part stays: which bearings the machine is built with
    assert row["bearings"]["A"]["card"] == "618/8-2Z"
    assert "thermal_path" not in row["bearings"]
    assert _clean(r.text), (who, planted["layered"])


@pytest.mark.parametrize("who", WITH_GATE)
def test_the_tree_keeps_the_cad_basis_behind_the_gate(planted, who):
    r = client.get("/api/family/tree", headers=planted[who])
    assert r.status_code == 200
    assert _row(r)["bearings"]["thermal_path"] == SECRET


@pytest.mark.parametrize("order", [("admin", "plain", "admin", "factory"),
                                   ("plain", "admin", "online", "verified", "plain")])
def test_the_tree_memo_never_hands_one_callers_view_to_another(planted, order):
    """The memo holds the rows whole; every answer is filtered for its caller —
    admin -> ordinary and ordinary -> admin, no cache clearing in between."""
    for who in order:
        r = client.get("/api/family/tree", headers=planted[who])
        assert r.status_code == 200
        bearings = _row(r)["bearings"]
        if who in WITH_GATE:
            assert bearings["thermal_path"] == SECRET, (order, who)
        else:
            assert "thermal_path" not in bearings and _clean(r.text), (order, who)


def test_the_anonymous_exhibit_gets_the_row_and_not_the_basis(planted, monkeypatch):
    from motor_ai_sim.routes import family
    root = planted["tmp"] / "exhibit"
    root.mkdir()
    die_path = planted["dies_dir"] / DIE / "die.yaml"
    die_doc = yaml.safe_load(die_path.read_text("utf-8"))
    geo = die_doc["geometry"]
    geo["stator_outer_radius"] = geo["stator_diameter"] / 2
    die_path.write_text(yaml.safe_dump(die_doc), encoding="utf-8")
    (root / "motor_catalog.json").write_text(json.dumps({"motors": [{"passport": {"geo": {
        "numSlots": geo["num_slots"], "numPoles": geo["num_poles"],
        "statorOR_mm": geo["stator_outer_radius"], "magnetHeight_mm": geo["magnet_height"]}}}]}),
        encoding="utf-8")
    monkeypatch.setattr(family, "_ws_root_f", lambda: root)
    monkeypatch.setenv("PUBLIC_EXHIBIT", "1")
    family._TREE_CACHE.clear()
    client.get("/api/family/tree", headers=planted["admin"])        # warm the memo as admin first
    anon = client.get("/api/family/tree")
    assert anon.status_code == 200
    assert _row(anon)["bearings"]["A"]["card"] == "618/8-2Z"        # the exhibit row is there...
    assert _clean(anon.text)                                         # ...without the basis
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    assert client.get("/api/family/tree").status_code == 401


def test_in_process_trees_withhold_it_by_default(planted):
    """The support assistant's tree (``tree_for_access``) and the MCP's: no basis
    whoever is asking — the MCP is public-facing for admins too."""
    from fastapi import Response
    from motor_ai_sim import mcp_tools, motor_access, workspace
    from motor_ai_sim.auth import caller_identity
    from motor_ai_sim.routes import family
    acc = motor_access.catalog_access(planted["admin"]["Authorization"])
    t = family.tree_for_access(Response(), acc, with_mass=False)
    assert _clean(json.dumps(t))
    with workspace.use_caller(caller_identity(planted["admin"]["Authorization"])):
        assert "thermal_path" in json.dumps(family.tree(Response(), authorization=None))
    fake = {"dies": [{"name": DIE, "configs": [{"name": PUB, "card_date": "2026-10-20",
                                                "bearings": {"A": {"card": "618/8-2Z"},
                                                             "lubrication": "grease",
                                                             "thermal_path": SECRET}}]}]}
    import unittest.mock as um
    with um.patch.object(family, "tree", lambda *a, **k: fake):
        rows = mcp_tools._visible_tree()
    assert rows[0]["configs"][0]["bearings"] == {"A": {"card": "618/8-2Z"}, "lubrication": "grease"}
    with pytest.raises(AssertionError):
        mcp_tools.assert_no_geometry({"bearings": {"thermal_path": SECRET}})
    with pytest.raises(AssertionError):
        mcp_tools.assert_no_geometry({"x": {"segments_mm": [[1, 2]]}})


# ── R1: the editor context ──────────────────────────────────────────────────

def test_the_context_strip_withholds_it_too(planted, monkeypatch):
    from motor_ai_sim.routes import family
    ctx = planted["tmp"] / "family_context.json"
    ctx.write_text(json.dumps({"die": DIE, "config": PUB, "duty": "rated"}), encoding="utf-8")
    monkeypatch.setitem(family.__dict__, "_CTX_FILE", ctx)
    for who in NO_GATE + WITH_GATE:
        r = client.get("/api/family/context", headers=planted[who])
        assert r.status_code == 200, (who, r.text[:300])
        body = r.json()
        assert body.get("active") is True, (who, body)
        if who in WITH_GATE:
            assert body["bearings"]["thermal_path"] == SECRET
        else:
            assert body["bearings"]["A"]["card"] == "618/8-2Z"
            assert _clean(r.text), who


# ── R1: the thermal payloads (and the middleware under every JSON route) ────

def _bearing_node_block():
    """A real ``cooling.bearing_node`` block, built by the module from the CAD
    geometry above (``summarize`` on a solve with no nodes: temperatures None)."""
    from motor_ai_sim.simulation import bearing_holder as bh
    kw = dict(card=CARD_618_8, stack_m=0.012, shaft_area_stack_m2=49.64e-6,
              shaft_bore_d_m=5.9e-3, stator_od_m=0.040, stator_bore_m=0.024)
    ends = [{"end": "A", "card": CARD_618_8, "f_r_n": 0.18, "p_w": 0.05,
             "geom": bh.end_geometry(end="A", cad=SECRET["A"], **kw)}]
    net = bh.network(ends=ends, k_shaft=42.0, rpm=13000, t_ambient_c=40.0,
                     air_mode="wash", air_speed_mps=12.0, holder_wall_c={},
                     emissivity=0.9, symmetry_mult=1, shaft_tags=[6], stator_tags=[1])
    return {"mode": "solved", **bh.summarize({}, net, t_ambient_c=40.0),
            "cad": True, "notes": []}


_CAD_SRC = "CAD (bearings.thermal_path.rotor_face_open_fraction)"


def _thermal_result():
    return {"T_max": 100.0, "cooling": {
        "bearing_node": _bearing_node_block(),
        "rotor_channels": {
            "mode": "through-flow", "holder_open_fraction": 0.4321,
            "holder_open_fraction_source": _CAD_SRC, "face_speed_mps": 5.1852,
            "channels": [{"open": True, "v_mps": 3.3, "re": 400.0, "m_dot_g_s": 0.2,
                          "G_W_per_K": 0.01}],
            "notes": ["3 rotor air region(s) found on this section (machine: 6); 0 open "
                      "onto the gap; the air reaches the rotor face at 5.19 m/s (wash "
                      "12.00 m/s × holder open fraction 0.43, %s)" % _CAD_SRC]},
        "holder_open_fraction_source": _CAD_SRC,
        "end_faces": {"rotor": {"film_kind": "forced", "air_speed_mps": 5.1852,
                                "re": 999.0, "G_W_per_K": 0.02, "heat_removed_W": 0.5}}}}


@pytest.fixture()
def thermal_last(planted, monkeypatch):
    from motor_ai_sim.routes import thermal
    monkeypatch.setattr(thermal, "_load_last", lambda: None)
    monkeypatch.setattr(thermal, "_live_fingerprint", lambda *a, **k: "nofp")
    monkeypatch.setattr(thermal, "_LAST", {"field": {"result": _thermal_result(), "params": {},
                                                      "geometry_fingerprint": None}})
    return planted


@pytest.mark.parametrize("who", NO_GATE)
def test_the_thermal_payload_withholds_the_measured_basis(thermal_last, who):
    from motor_ai_sim.construction_privacy import WITHHELD
    r = client.get("/api/thermal/last", headers=thermal_last[who])
    assert r.status_code == 200, r.text[:300]
    cooling = r.json()["field"]["result"]["cooling"]
    # re-audit #3 B2 superseded the per-field redaction: the CAD-derived paths
    # come back with NO per-path detail, only the bearing temperature
    e = cooling["bearing_node"]["ends"][0]
    assert set(e) == {"end", "card", "bearing_max_c"}
    assert cooling["bearing_node"]["detail"] == "detailed cooling paths with NDA"
    rc = cooling["rotor_channels"]
    assert set(rc) <= {"mode", "detail", "notes", "note"}
    assert "holder open fraction 0.43" not in r.text
    assert set(cooling["end_faces"]["rotor"]) <= {"mode", "film_kind", "name", "note", "detail"}
    assert WITHHELD in cooling["holder_open_fraction_source"]
    assert "segments_mm" not in r.text and "0.4321" not in r.text and "5.185" not in r.text
    assert "7.778" not in r.text and "31.4 mm" not in r.text


@pytest.mark.parametrize("who", WITH_GATE)
def test_the_thermal_payload_is_whole_behind_the_gate(thermal_last, who):
    r = client.get("/api/thermal/last", headers=thermal_last[who])
    cooling = r.json()["field"]["result"]["cooling"]
    assert cooling["rotor_channels"]["holder_open_fraction"] == 0.4321
    rows = cooling["bearing_node"]["ends"][0]["resistances"]
    assert rows[0]["segments_mm"][1] == [7.778, 13.131]


def test_the_middleware_catches_a_bearings_block_on_any_json_route(planted):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim import construction_privacy as CP
    app = FastAPI()
    CP.install(app)

    @app.get("/api/anything")
    def anything():
        return {"configs": [{"bearings": {"A": {"card": "618/8-2Z", "seals": "2Z"},
                                          "lubrication": "grease", "preload_n": 5.0,
                                          "thermal_path": SECRET}}],
                "results": {"bearings": [{"end": "A", "P_W": 0.1}]}}

    c = TestClient(app)
    plain = c.get("/api/anything", headers=planted["plain"])
    assert plain.status_code == 200 and _clean(plain.text)
    body = plain.json()
    assert body["configs"][0]["bearings"] == {"A": {"card": "618/8-2Z", "seals": "2Z"},
                                              "lubrication": "grease", "preload_n": 5.0}
    assert body["results"] == {"bearings": [{"end": "A", "P_W": 0.1}]}   # not a config block
    assert int(plain.headers["content-length"]) == len(plain.content)
    assert "thermal_path" in c.get("/api/anything", headers=planted["admin"]).text
    assert _clean(c.get("/api/anything").text)                            # anonymous


# ── R3 (+R1): the bearings save ─────────────────────────────────────────────

BEARINGS = "/api/family/config/{die}/{cfg}/bearings"


def _saved(planted):
    return yaml.safe_load(planted["cfg_path"].read_text("utf-8"))


def test_a_compatible_save_keeps_the_cad_basis(planted):
    r = client.patch(BEARINGS.format(die=DIE, cfg=PUB), headers=planted["admin"],
                     json={"A": {"card": "618/8-2Z", "grease": None},
                           "B": {"card": "618/8-2Z"}, "preload_n": 4.0,
                           "temp_source": "thermal"})
    assert r.status_code == 200, r.text
    assert r.json()["thermal_path_status"]["status"] == "kept"
    assert r.json()["bearings"]["thermal_path"] == SECRET          # the admin's own answer
    saved = _saved(planted)["bearings"]
    assert saved["thermal_path"] == SECRET and saved["preload_n"] == 4.0


def test_a_card_change_invalidates_it_and_says_so(planted):
    from motor_ai_sim import bearings as B
    other = next(n for n in B.list_bearings() if n != "618/8-2Z")
    r = client.patch(BEARINGS.format(die=DIE, cfg=PUB), headers=planted["admin"],
                     json={"A": {"card": other}, "B": {"card": "618/8-2Z"}})
    assert r.status_code == 200, r.text
    tp = r.json()["thermal_path_status"]
    assert tp["status"] == "invalidated" and other in tp["reason"]
    assert "thermal_path" not in _saved(planted)["bearings"]


def test_clearing_the_pair_says_the_basis_went_with_it(planted):
    r = client.patch(BEARINGS.format(die=DIE, cfg=PUB), headers=planted["admin"], json={})
    assert r.status_code == 200
    assert r.json()["bearings"] is None and r.json()["thermal_path_status"]["status"] == "invalidated"


def test_a_save_without_the_basis_answers_as_before(planted):
    cfg = _saved(planted)
    cfg["bearings"].pop("thermal_path")
    planted["cfg_path"].write_text(yaml.safe_dump(cfg), encoding="utf-8")
    r = client.patch(BEARINGS.format(die=DIE, cfg=PUB), headers=planted["admin"],
                     json={"A": {"card": "618/8-2Z"}, "B": {"card": "618/8-2Z"}})
    assert r.status_code == 200 and "thermal_path_status" not in r.json()


def test_an_ordinary_writer_never_receives_or_copies_the_basis(planted):
    """Layered: a registered account writes its OWN workspace copy — the shared
    original keeps the basis, the copy and the answer do not get it.  Flat: only
    the admin writes at all."""
    r = client.patch(BEARINGS.format(die=DIE, cfg=PUB), headers=planted["plain"],
                     json={"A": {"card": "618/8-2Z"}, "B": {"card": "618/8-2Z"}})
    if not planted["layered"]:
        assert r.status_code == 403
        return
    assert r.status_code == 200, r.text
    assert not any(n in r.text for n in NEEDLES if n != "thermal_path")
    assert "thermal_path" not in r.json()["bearings"]
    assert r.json()["thermal_path_status"]["status"] == "withheld"
    assert _saved(planted)["bearings"]["thermal_path"] == SECRET    # the shared file untouched
    copies = list((planted["tmp"] / "srv" / "workspaces").rglob(f"{PUB}.yaml"))
    assert copies and all("thermal_path" not in (yaml.safe_load(p.read_text("utf-8"))
                                                 .get("bearings") or {}) for p in copies)


# ── R2: the friction's equivalent sources ───────────────────────────────────

def _net(p_w=0.8, cad=SECRET["A"], air_mode="wash"):
    from motor_ai_sim.simulation import bearing_holder as bh
    kw = dict(card=CARD_618_8, stack_m=0.012, shaft_area_stack_m2=49.64e-6,
              shaft_bore_d_m=5.9e-3, stator_od_m=0.040, stator_bore_m=0.024)
    ends = [{"end": e, "card": CARD_618_8, "f_r_n": 0.18, "p_w": p_w,
             "geom": bh.end_geometry(end=e, cad=(cad if e == "A" else None), **kw)}
            for e in "AB"]
    return bh.network(ends=ends, k_shaft=42.0, rpm=13000, t_ambient_c=40.0,
                      air_mode=air_mode, air_speed_mps=12.0, holder_wall_c={},
                      emissivity=0.9, symmetry_mult=1, shaft_tags=[6], stator_tags=[1])


def _solve_lumped(net, extra_nodes, extra_links, sources, t_amb=40.0):
    """Solve the bearing network + ``extra`` lumped machine nodes exactly.

    ``net``'s shaft sinks attach to node "R", its holder->stator sinks to "S";
    every ``G_W_per_K`` on a network node goes to the ambient.  Returns
    ``{node: T}``."""
    names = list(extra_nodes) + [n["name"] for n in net["nodes"]]
    ix = {n: i for i, n in enumerate(names)}
    Y = np.zeros((len(names), len(names)))
    q = np.zeros(len(names))

    def link(a, b, g):
        ia, ib = ix.get(a), ix.get(b)
        if ia is not None:
            Y[ia, ia] += g
        if ib is not None:
            Y[ib, ib] += g
        if ia is not None and ib is not None:
            Y[ia, ib] -= g
            Y[ib, ia] -= g
        # a missing end is the ambient (T = t_amb, folded below)
        if ia is None:
            q[ib] += g * t_amb
        if ib is None:
            q[ia] += g * t_amb

    for n in net["nodes"]:
        q[ix[n["name"]]] += float(n.get("q_W") or 0.0)
        if float(n.get("G_W_per_K") or 0.0) > 0.0:
            link(n["name"], None, float(n["G_W_per_K"]))
    for lk in net["links"]:
        link(lk["a"], lk["b"], float(lk["G_W_per_K"]))
    for s in net["sinks"]:
        link(s["node"], "R" if 6 in s["tags"] else "S", float(s["G_W_per_K"]))
    for a, b, g in extra_links:
        link(a, b, g)
    for n, p in sources.items():
        q[ix[n]] += p
    T = np.linalg.solve(Y, q)
    return {n: float(T[ix[n]]) for n in names}


@pytest.mark.parametrize("air_mode", ["wash", "still"])
def test_the_source_split_is_the_exact_reduction_of_the_network(air_mode):
    """With rotor, stator and air all at the ambient, the heat the solved network
    hands each of them IS the split — computed here independently, from the
    network's own nodes, links and sinks."""
    net = _net(air_mode=air_mode)
    for rep in net["ends"]:
        e = rep["end"]
        sub = {"nodes": [n for n in net["nodes"] if n["name"].startswith("brg%s_" % e)],
               "links": [l for l in net["links"] if l["name"].startswith("brg%s_" % e)],
               "sinks": [s for s in net["sinks"] if s["name"].startswith("bearing_%s_" % e)]}
        T = _solve_lumped(sub, ["R", "S"], [("R", None, 1e9), ("S", None, 1e9)], {})
        g_sh = next(s for s in sub["sinks"] if s["name"].endswith("_shaft"))["G_W_per_K"]
        g_hs = next(s for s in sub["sinks"] if s["name"].endswith("_holder_stator"))["G_W_per_K"]
        g_air = next(n for n in sub["nodes"] if n["name"].endswith("_holder"))["G_W_per_K"]
        to_r = g_sh * (T["brg%s_inner" % e] - T["R"])
        to_s = g_hs * (T["brg%s_holder" % e] - T["S"])
        to_a = g_air * (T["brg%s_holder" % e] - 40.0)
        d = rep["delta"]
        assert d["P_to_rotor_W"] == pytest.approx(to_r, rel=1e-6)
        assert d["P_to_stator_W"] == pytest.approx(to_s, rel=1e-6)
        assert d["P_to_air_W"] == pytest.approx(to_a, rel=1e-6)
        assert d["P_to_rotor_W"] + d["P_to_stator_W"] + d["P_to_air_W"] == pytest.approx(0.8)
        assert min(d["P_to_rotor_W"], d["P_to_stator_W"], d["P_to_air_W"]) > 0.0


def _synthetic_mech(p_nodes=2.0):
    return {"P_bearings_W": p_nodes, "P_bearings_at_bearing_nodes_W": p_nodes,
            "bearings_modelled": True, "shaft_ends_open": False, "P_windage_gap_W": 0.0}


def test_closed_ends_carry_the_friction_and_close_the_energy_balance():
    """Astra's repro (10 W copper + 2 W in the solved bearing nodes, closed shaft
    ends), now with the map's source split: every watt is somewhere."""
    from motor_ai_sim import coupled_time_to_limit as ttl
    from motor_ai_sim import thermal_duty_cycle as dc
    summary = {"P_stranded_W": 10.0, "P_core_W": 0.0, "P_solid_W": 0.0,
               "coil_temp_C": 100.0, "rpm": 1000.0}
    thermal = {"P_cu_exact_W": 10.0, "P_mag_eddy_W": 0.0, "cooling": {
        "bearing_node": {"mode": "solved", "friction_W": 2.0,
                         "delta": {"P_to_rotor_W": 1.1, "P_to_stator_W": 0.6,
                                   "P_to_air_W": 0.3}},
        "mech_losses": _synthetic_mech()}}
    loss = dc.losses_by_node(summary, thermal)
    assert loss["rotor"] == pytest.approx(1.1) and loss["stator"] == pytest.approx(0.6)
    assert loss["total_W"] == pytest.approx(11.7)
    assert loss["bearings_to_ambient_W"] == pytest.approx(0.3)
    assert loss["input_total_W"] == pytest.approx(12.0)
    assert ttl._segment(summary, thermal, "x").total_W == pytest.approx(11.7)
    assert not any("NOT in this network" in n for n in loss["notes"])
    # a map from before the split: the whole friction on the rotor, said
    old = json.loads(json.dumps(thermal))
    old["cooling"]["bearing_node"].pop("delta")
    loss = dc.losses_by_node(summary, old)
    assert loss["rotor"] == pytest.approx(2.0) and loss["input_total_W"] == pytest.approx(12.0)
    assert any("upper bound for the rotor" in n for n in loss["notes"])
    # open shaft ends: unchanged (all on the rotor at the seats)
    opened = json.loads(json.dumps(thermal))
    opened["cooling"]["mech_losses"].update(shaft_ends_open=True,
                                            P_bearings_at_bearing_nodes_W=0.0)
    assert dc.losses_by_node(summary, opened)["rotor"] == pytest.approx(2.0)


def test_the_lumped_network_balances_the_steady_map_it_was_fitted_to():
    """THE R2 property.  A ground-truth lumped machine (winding, stator, rotor,
    magnet + the real bearing network with friction, closed shaft ends) is solved
    exactly; its temperatures and flows are written as a steady map; the duty
    cycle's network is fitted to that map; at the map's own temperatures every
    node of it must be in balance.  Without the friction's sources the rotor
    and the stator were short by exactly their shares."""
    from motor_ai_sim import thermal_duty_cycle as dc
    net = _net(p_w=1.0)
    G_ws, G_rs, G_mr, G_house = 1.0, 0.05, 0.05, 0.8
    P = {"W": 10.0, "S": 1.0, "R": 0.2, "M": 0.1}
    T = _solve_lumped(net, ["W", "S", "R", "M"],
                      [("W", "S", G_ws), ("R", "S", G_rs), ("M", "R", G_mr),
                       ("S", None, G_house)], P)
    assert abs(T["R"] - T["S"]) > 1.0 and T["M"] - T["R"] > 1.0     # every pair resolvable
    shaft_to_brg = sum(s["G_W_per_K"] * (T["R"] - T[s["node"]])
                       for s in net["sinks"] if 6 in s["tags"])
    delta = {k: sum(float(e["delta"][k]) for e in net["ends"])
             for k in ("G_rotor_air_W_per_K", "G_rotor_stator_W_per_K",
                       "G_stator_air_W_per_K", "P_to_rotor_W", "P_to_stator_W",
                       "P_to_air_W")}
    housing_w = G_house * (T["S"] - 40.0)
    steady = {
        "components": {n: {"avg": T[k], "max": T[k]} for n, k in
                       (("winding", "W"), ("stator", "S"), ("rotor", "R"), ("magnet", "M"))},
        "cooling": {
            "outer": {"mode": "manual", "t_sink_c": 40.0, "area_m2": 0.004,
                      "heat_removed_W": housing_w},
            "heat_budget": {"losses_W": 13.3, "housing_W": housing_w, "bore_W": 0.0,
                            "gap_W": G_rs * (T["R"] - T["S"]), "shaft_ends_W": 0.0,
                            "bearings_W": shaft_to_brg, "coil_W": 10.0},
            "bearing_node": {"mode": "solved", "delta": delta,
                             "shaft_to_bearings_W": shaft_to_brg},
            "mech_losses": _synthetic_mech(2.0)},
        "P_cu_exact_W": 10.0, "P_mag_eddy_W": 0.1, "ambient_temp": 40.0}
    summary = {"P_stranded_W": 10.0, "P_solid_W": 0.1, "coil_temp_C": T["W"], "rpm": 13000,
               "P_core_terms": {"stator": {"hysteresis_W": 1.0}, "rotor": {"hysteresis_W": 0.2}}}
    network = dc.network_from_steady(steady, calibration_duty="synthetic")
    assert not network.merged
    loss = dc.losses_by_node(summary, steady)
    assert loss["input_total_W"] == pytest.approx(13.3)
    state = {network.rep(n): T[k] for n, k in
             (("winding", "W"), ("stator", "S"), ("rotor", "R"), ("magnet", "M"))}
    caps = {n: 1.0 for n in ("winding", "stator", "rotor", "magnet")}

    def residual(losses):
        seg = dc.Segment("synthetic", 1.0, losses=losses, coil_ref_c=T["W"],
                         copper_feedback=False)
        d, _p, _f = dc._derivatives(seg, state, network, caps)
        return d

    ok = residual(dc.node_watts(loss))
    # losses_by_node rounds every node to 0.1 mW: that is the floor here
    assert all(abs(v) < 2e-4 for v in ok.values()), ok
    # the defect the re-audit found, reproduced: drop the sources again
    lost = dict(dc.node_watts(loss), rotor=P["R"], stator=P["S"])
    bad = residual(lost)
    assert bad["rotor"] == pytest.approx(-delta["P_to_rotor_W"], abs=2e-4)
    assert bad["stator"] == pytest.approx(-delta["P_to_stator_W"], abs=2e-4)
    assert min(delta["P_to_rotor_W"], delta["P_to_stator_W"]) > 0.05     # a real defect
    assert math.isclose(sum(delta[k] for k in ("P_to_rotor_W", "P_to_stator_W",
                                               "P_to_air_W")), 2.0, rel_tol=1e-9)

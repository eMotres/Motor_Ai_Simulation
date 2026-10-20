# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Codex Astra RE-AUDIT #4 2026-10-20 — B2: no fitted network of a CAD-cooled
machine leaves the server for a caller without the full-construction gate.

(A) a FRESH network kept ``links.m_r.P_W`` / ``G_W_per_K.m_r`` / raw fits /
``calibration.P_mag_W``: P_mag − P_m→r is the withheld magnet-face watts, and
with the public mesh area the CAD open fraction (0.4319 vs 0.4321).
(B) a network in the OLD format (no ``construction_basis`` marker) kept
``surface_G_W_per_K.rotor_ends``, and redact / the ZIP / scrub missed it.

Now: the network is withheld WHOLE (links, G, raw fits, calibration, surface
G, basis text, the P_* splits) and its split reduced to totals, on any record
of a CAD-cooled machine — decided from the MACHINE's own documents in every
layer, or from the record's text naming CAD, and fail closed when the machine
cannot be determined.  Fresh, stored (the duty ledger), exported and scrubbed.
Astra's two probes are here and must fail to recover.  No FEM.
"""
from __future__ import annotations

import copy
import json
import math

import pytest
import yaml

from tests.test_config_visibility import DIE, PLAIN, PUB, client
from tests.test_reaudit_r1_r2_r3 import FACTORY, env, gate, planted, thermal_last  # noqa: F401
from tests.test_reaudit2_r5_r6_r8 import _export, _ws_cfg, layered  # noqa: F401
from tests.test_reaudit3_b1_b2 import _mesh_result

PLAIN_CFG = "L43"          # a machine with NO CAD basis
NDA = "detailed cooling network with NDA"


def _area_of(result, tag):
    total = 0.0
    for tri, domain in zip(result["triangles"], result["domain_per_tri"]):
        if domain != tag:
            continue
        a, b, c = [result["vertices"][i] for i in tri]
        total += abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
    return total * result["symmetry_mult"]


def _network_fixture():
    """Astra's round-4 fixture: a real ``network_from_steady`` on a map whose
    magnet face ran at 12 m/s × 0.4321, at route precision."""
    from motor_ai_sim import thermal_duty_cycle as TDC
    from motor_ai_sim.simulation.cooling_models import rotor_end_faces_open
    from motor_ai_sim.simulation.sb_domains import DOM_MAG_N, DOM_ROTOR
    result, _face, _a = _mesh_result()
    result["rpm"] = 1000.0
    result["ambient_temp"] = 40.0
    result["domain_per_tri"] = [DOM_MAG_N] * 4 + [DOM_ROTOR] * 4
    result["components"] = {n: {"avg": t, "max": t} for n, t in
                            (("winding", 120.), ("stator", 100.), ("rotor", 80.), ("magnet", 90.))}
    result["cooling"]["outer"] = {"mode": "air", "t_sink_c": 40., "area_m2": .004,
                                  "heat_removed_W": 20.}
    result["P_cu_exact_W"], result["P_mag_eddy_W"] = 20., 10.
    c = result["cooling"]
    c["rotor_channels"] = {"mode": "off", "notes": []}
    c["bearing_node"] = {"mode": "off"}
    c["heat_budget"]["rotor_channels_W"] = 0.
    c["heat_budget"]["rotor_channels_magnet_W"] = 0.
    for name, tag, wall in (("rotor", DOM_ROTOR, 80.), ("magnet", DOM_MAG_N, 90.)):
        a = _area_of(result, tag)
        face = rotor_end_faces_open(air_speed_mps=12. * .4321, rpm=1000., t_wall_c=wall,
                                    t_ambient_c=40., area_m2=a, radius_m=.015, n_faces=2,
                                    name=name)
        c["end_faces"][name] = dict(face, G_W_per_K=round(face["G_W_per_K"], 5),
                                    heat_removed_W=round(face["heat_removed_W"], 3),
                                    area_m2=round(2 * a, 8), area_per_face_m2=round(a, 8),
                                    t_mean_c=wall)
    net = TDC.network_from_steady(result, calibration_duty="probe").as_record()
    return result, net


def _recover(pub_net, mesh):
    """Astra's arithmetic, from the PUBLIC answer only.  ``None`` when there is
    nothing to start from."""
    from motor_ai_sim.simulation.cooling_models import air_properties
    from motor_ai_sim.simulation.sb_domains import DOM_MAG_N, DOM_ROTOR
    try:
        p_mag = pub_net["calibration"]["P_mag_W"]
        p_link = pub_net["links"]["m_r"]["P_W"]
        wall = pub_net["calibration"]["means_c"]["magnet"]
        sink = pub_net["t_ambient_c"]
    except (KeyError, TypeError):
        return None
    if None in (p_mag, p_link, wall, sink):
        return None
    area = _area_of(mesh, DOM_MAG_N)
    ids = {i for tri, tag in zip(mesh["triangles"], mesh["domain_per_tri"])
           if tag == DOM_ROTOR for i in tri}
    radius = max(math.hypot(*mesh["vertices"][i]) for i in ids)
    h = (p_mag - p_link) / (wall - sink) / area / 2
    pr = air_properties((wall + sink) / 2)
    d = radius * 2
    re_ = (h * d / (.664 * pr.k * pr.pr ** (1 / 3))) ** 2
    return re_ * pr.nu / d / 12.0


def _serve(monkeypatch, result, network, record_extra=None):
    from motor_ai_sim.routes import thermal
    record = {"kind": "duty_cycle", "network": network, "point": {"rpm": 1000.},
              "split": {"generated_total_W": 30.0, "closure_W": 0.1, "rotor_side_W": 3.0,
                        "stator_side_W": 26.9, "magnet_end_faces_W": 1.626,
                        "housing_W": 20.0},
              "cycle": {"t_s": [0.0, 1.0], "T_c": {"winding": [100.0, 101.0]},
                        "P_W": {"winding": [20.0, 20.0], "magnet": [10.0, 10.0]}},
              **(record_extra or {})}
    monkeypatch.setattr(thermal, "_LAST", {
        "field": {"result": result, "params": {"rpm": 1000.}, "geometry_fingerprint": None},
        "duty_cycle": {"result": record, "params": {"rpm": 1000.}, "geometry_fingerprint": None}})
    return record


# ── (A) + (B): Astra's probes must fail to recover ─────────────────────────

@pytest.mark.parametrize("variant", ["fresh", "legacy"])
@pytest.mark.parametrize("who", ["plain", "factory"])
def test_no_fitted_network_leaves_for_a_caller_without_the_gate(thermal_last, monkeypatch, who, variant):
    result, net = _network_fixture()
    assert abs(_recover(net, result) - .4321) < .001          # the probe works on the raw record...
    if variant == "legacy":
        net = copy.deepcopy(net)
        net["calibration"].pop("construction_basis")          # the exact pre-#358 format
    _serve(monkeypatch, result, net)
    r = client.get("/api/thermal/duty_cycle/last", headers=thermal_last[who])
    assert r.status_code == 200, r.text[:300]
    rec = r.json()["duty_cycle"]["result"]
    pub = rec["network"]
    mesh = client.get("/api/thermal/last", headers=thermal_last[who]).json()["field"]["result"]
    assert _recover(pub, mesh) is None                        # ...and finds nothing here
    assert pub == {"withheld": True, "detail": NDA} or (pub["withheld"] and pub["detail"] == NDA)
    for k in ("links", "G_W_per_K", "calibration", "areas_m2", "raw_fits_W_per_K", "h_sources"):
        assert k not in pub, k
    for needle in ("8.374", "0.8374", "surface_G_W_per_K", "raw_fits", "P_mag_W", "0.06506"):
        assert needle not in r.text, needle
    # the split and the per-node loss series: totals only
    assert set(rec["split"]) <= {"generated_total_W", "heat_to_air_W", "closure_pct", "basis", "detail"}
    assert rec["split"]["heat_to_air_W"] == pytest.approx(29.9)
    assert rec["cycle"]["P_W"] == {"total": [30.0, 30.0]}
    assert rec["cycle"]["T_c"] == {"winding": [100.0, 101.0]}     # the temperatures stay


@pytest.mark.parametrize("who", ["admin", "verified"])
def test_the_gated_caller_keeps_the_network(thermal_last, monkeypatch, who):
    result, net = _network_fixture()
    _serve(monkeypatch, result, net)
    pub = client.get("/api/thermal/duty_cycle/last", headers=thermal_last[who]).json()["duty_cycle"]["result"]["network"]
    assert pub["links"]["m_r"]["P_W"] == net["links"]["m_r"]["P_W"]


# ── the MACHINE decides ─────────────────────────────────────────────────────

@pytest.fixture()
def two_machines(planted):
    """PUB carries the CAD basis (planted); PLAIN_CFG is an ordinary machine."""
    src = yaml.safe_load(planted["cfg_path"].read_text("utf-8"))
    src["name"] = PLAIN_CFG
    src["bearings"].pop("thermal_path")
    (planted["dies_dir"] / DIE / f"{PLAIN_CFG}.yaml").write_text(yaml.safe_dump(src), encoding="utf-8")
    return planted


def test_the_machine_decides_and_an_unknown_machine_is_cad(two_machines):
    from motor_ai_sim import construction_privacy as CPV
    _r, net = _network_fixture()
    net = copy.deepcopy(net)
    net["calibration"].pop("construction_basis")
    rec = lambda cfg: {"die": DIE, "configuration": cfg, "network": net}   # noqa: E731
    assert CPV.machine_is_cad_cooled(DIE, PUB) is True
    assert CPV.machine_is_cad_cooled(DIE, PLAIN_CFG) is False
    assert CPV.machine_is_cad_cooled(DIE, "NOSUCH") is True                 # cannot tell
    assert CPV.redact(rec(PUB))["network"]["withheld"] is True
    assert CPV.redact(rec(PLAIN_CFG))["network"]["links"] == net["links"]   # ordinary: kept
    assert CPV.redact({"network": net})["network"]["withheld"] is True      # no machine: fail closed
    # the record's own text naming CAD wins over an ordinary machine
    cad_txt = dict(rec(PLAIN_CFG), note="holder from the CAD (bearings.thermal_path)")
    assert CPV.redact(cad_txt)["network"]["withheld"] is True


def test_a_scrubbed_workspace_copy_does_not_make_the_machine_ordinary(two_machines):
    """The caller's own copy has lost thermal_path; the shared original has not."""
    if not two_machines["layered"]:
        pytest.skip("per-account copies exist with layering only")
    from motor_ai_sim import construction_privacy as CPV
    from motor_ai_sim import workspace as W
    r = client.patch(f"/api/family/config/{DIE}/{PUB}/controller", json={},
                     headers=two_machines["plain"])
    assert r.status_code == 200
    assert "thermal_path" not in _ws_cfg(PLAIN).read_text("utf-8")
    with W.use_workspace(W.workspace_for_identity(PLAIN)):
        assert CPV.machine_is_cad_cooled(DIE, PUB) is True


def test_the_duty_ledger_is_judged_row_by_row(two_machines):
    from motor_ai_sim import construction_privacy as CPV
    _r, net = _network_fixture()
    ledger = {"results": {DIE: {PUB: {"rated": {"duty_cycle": {"network": net}}},
                                PLAIN_CFG: {"rated": {"duty_cycle": {"network": net}}}}}}
    out = CPV.redact(ledger)["results"][DIE]
    assert out[PUB]["rated"]["duty_cycle"]["network"]["withheld"] is True
    # an ordinary machine's row keeps it - unless the record itself names CAD
    kept = out[PLAIN_CFG]["rated"]["duty_cycle"]["network"]
    assert kept.get("withheld") is True or kept["links"] == net["links"]


def test_needs_redaction_sees_the_unmarked_format():
    from motor_ai_sim import construction_privacy as CPV
    _r, net = _network_fixture()
    net["calibration"].pop("construction_basis")
    assert CPV.needs_redaction(json.dumps({"network": net}).encode())
    assert CPV.needs_redaction(json.dumps({"split": {"generated_total_W": 1}}).encode())


# ── (B) the old format in the ZIP and the scrub ────────────────────────────

def test_old_network_files_are_cleaned_in_the_zip_and_by_the_scrub(layered, tmp_path):
    """Astra's legacy ZIP probe, flipped: the export withholds the network and
    the scrub reports and cleans the file."""
    from motor_ai_sim import construction_privacy as CPV
    _r, net = _network_fixture()
    legacy = copy.deepcopy(net)
    legacy["calibration"].pop("construction_basis")
    record = {"kind": "duty_cycle", "network": legacy, "point": {"rpm": 1000.}}
    path = _ws_cfg(PLAIN).parent / "old_cycle.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    before = path.read_bytes()
    with _export(layered["plain"]) as z:
        packed = json.loads(z.read(f"workspace/dies/{DIE}/old_cycle.json"))
    assert packed["network"]["withheld"] is True
    assert "surface_G_W_per_K" not in json.dumps(packed)
    assert path.read_bytes() == before                         # the export never edits
    iso = tmp_path / "legacy_only"
    iso.mkdir()
    (iso / "old_cycle.json").write_bytes(before)
    dry = CPV.scrub_tree(iso, dry_run=True)
    assert len(dry["scrubbed"]) == 1 and not dry["errors"]
    assert (iso / "old_cycle.json").read_bytes() == before
    app = CPV.scrub_tree(iso, dry_run=False)
    assert len(app["scrubbed"]) == 1 and not app["errors"]
    assert "surface_G_W_per_K" not in (iso / "old_cycle.json").read_text("utf-8")
    assert len(CPV.scrub_tree(iso, dry_run=False)["scrubbed"]) == 0   # idempotent
    # and the workspace command finds it too
    rep = CPV.scrub_workspaces(dry_run=False)
    assert rep["files_scrubbed"] >= 1 and rep["errors"] == 0
    assert json.loads(path.read_text("utf-8"))["network"]["withheld"] is True

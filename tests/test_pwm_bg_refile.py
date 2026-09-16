"""REFILING A SANDBOXED RUN — ``motor_ai_sim.duty_refile``.

Added 2026-09-16, out of a real failure.  The PWM campaign runs solver-direct
against a sandboxed ``config/`` so that it cannot disturb the machine the user
has open, and files its answer into the real catalogue at the end.  That save
wrote the duty's yaml entry, the gzip waveform sidecar and the ``coupled`` /
``thermal`` / ``em`` rows — and nothing else.  A NORMAL run (the ▶ button)
leaves more, because the solve routes file it as they go: the four field npz
under ``runs/<cfg>/<duty-stem>/fields/`` and the three mechanical rows.  In a
sandboxed run all of those landed in the temp directory and died with it, so the
client report drew its §6 table from the PWM run and its temperature maps from a
sine solve two days older — 175.2 °C in the table, 153.9 °C in the picture.

THE CONTRACT PINNED HERE, and it is one sentence: *a refiled duty carries the
same artefact set as a duty saved the normal way*.  So the suite builds both
sides for real —

  (a) a REFERENCE duty, filed through the very seams the solve routes call
      (``duty_fields.save_active``, ``duty_results.note_thermal`` /
      ``note_mechanical``), which is the normal path by construction;
  (b) a SANDBOX holding the same run's artefacts where a sandboxed campaign
      leaves them, refiled through ``duty_refile.refile``;

— and asserts the two sets are equal, kind for kind.  Then the things the report
actually reads: the thermal map's own ``T_max``, ``modes.f_switch_hz``,
``critical_speeds.campbell``, a fresh mtime, no other duty touched, the yaml
left alone, and the re-pack fallback for a sandbox that filed no npz.

The payloads are small synthetic meshes rather than solved fields: what is under
test is the FILING, and a four-minute FEM solve would only make the same
assertions slower.  ``test_duty_fields`` already pins the packers against real
solver output.
"""
from __future__ import annotations

import json
import pathlib
import pickle
import shutil
import tempfile
import time

import numpy as np
import pytest
import yaml

DIE = "REFILEDIE 200"
CFG = "L155 refile"
#: The duty that is REFILED, and the one filed the normal way to compare it
#: against.  A Cyrillic С in one of them on purpose — the 2026-09-01 trap: two
#: duty names that look identical must not share one folder.
DUTY = "rated 1x9 mm"
REF_DUTY = "rated 1x9 mm С"
OTHER_DUTY = "peak 1x9 mm"

MASS_KG = 27.559
T_MAX = 175.2
F_SWITCH = 24000.0


# ---------------------------------------------------------------------------
# Synthetic payloads — the shapes the four packers read
# ---------------------------------------------------------------------------


def _mesh(n_nodes: int = 40):
    rng = np.random.default_rng(7)
    p = rng.random((2, n_nodes)) * 100.0            # (2, n) mm, solver order
    t = np.stack([np.arange(n_nodes - 2),
                  np.arange(1, n_nodes - 1),
                  np.arange(2, n_nodes)])           # (3, m)
    return p.astype(float), t.astype(int)


def em_entry(computed_at: str) -> dict:
    p, t = _mesh()
    m = t.shape[1]
    return {
        "field": {"P_mm": p, "T": t, "tags": np.arange(m) % 4,
                  "Bx": np.linspace(0.1, 1.9, m), "By": np.linspace(0.0, 0.8, m),
                  "loss_dens": np.linspace(1e3, 9e4, m),
                  "loss_dens_label": "cycle-averaged"},
        "scalars": {"rpm": 3000.0, "T_avg_Nm": 245.5, "f_elec_Hz": 500.0,
                    "demag_coef_per_tri": np.full(m, 0.98)},
        "meta": {"computed_at": computed_at, "eddy": True,
                 "n_steps_per_period": 72},
    }


def thermal_result(computed_at: str, t_max: float = T_MAX) -> dict:
    p, t = _mesh()
    n, m = p.shape[1], t.shape[1]
    temps = np.linspace(60.0, t_max, n)
    return {
        "vertices": p.T / 1000.0, "triangles": t.T,
        "temperature_per_node": temps,
        "domain_per_tri": np.arange(m) % 5,
        "heat_flux_per_tri": np.linspace(0.0, 1e4, m),
        "part_names": {"0": "Stator core", "1": "Magnet"},
        "T_max": float(t_max), "T_min": 60.0, "rpm": 3000.0,
        "ambient_temp": 30.0, "P_loss_total_W": 8123.0,
        "computed_at": computed_at,
    }


def rotor_stress_result(computed_at: str) -> dict:
    p, t = _mesh()
    m = t.shape[1]
    case = {"vm_per_tri": np.linspace(10.0, 480.0, m),
            "s_p1_per_tri": np.linspace(5.0, 300.0, m),
            "sf_per_tri": np.linspace(0.8, 4.0, m),
            "u_mag_per_node": np.linspace(0.0, 60.0, p.shape[1]),
            "rpm": 3000.0, "sf_min": 1.42, "sf_min_part": "Rotor core",
            "rotor_od_growth_um": 31.0, "max_displacement_um": 58.0,
            "parts": {"Rotor core": {"material": "HM63", "safety_factor": 1.42,
                                     "von_mises_max_mpa": 480.0}},
            "contact": {"converged": True, "iterations": 4, "residual_um": 0.3}}
    return {
        "primary_case": "rated",
        "field": {"vertices": p, "triangles": t,
                  "domain_per_tri": np.arange(m) % 3,
                  "cases": {"rated": case},
                  "part_names": {"0": "Rotor core"}},
        "cases": {"rated": case},
        "rpm": 3000.0, "computed_at": computed_at,
    }


def modes_result(computed_at: str, n_modes: int = 12) -> dict:
    p, t = _mesh()
    n = p.shape[1]
    # A LIST of shapes, as the modal solver returns them (``pack_modes`` tests
    # the payload for truth, which a bare ndarray cannot answer).
    shapes = np.linspace(-1.0, 1.0, n_modes * n * 2).reshape(
        n_modes, n, 2).tolist()
    return {
        "field": {"vertices": p, "triangles": t, "modes": shapes,
                  "domain_per_tri": np.arange(t.shape[1]) % 2},
        "modes": [{"index": i, "f_hz": 1200.0 + 130.0 * i, "order": i + 2,
                   "nearest": {"name": "PWM carrier", "hz": F_SWITCH,
                               "margin_pct": 40.0, "flag": False}}
                  for i in range(n_modes)],
        "f_switch_hz": F_SWITCH, "rpm": 3000.0, "body": "stator",
        "support": "free", "computed_at": computed_at,
    }


def critical_speeds_result(computed_at: str) -> dict:
    rpm = list(np.linspace(0.0, 4000.0, 41))
    return {
        "rated_rpm": 3000.0, "verdict": "subcritical",
        "critical_speeds": [{"mode": 1, "whirl": "forward", "rpm": 5200.0,
                             "margin_vs_rated_pct": 42.0}],
        "campbell": {"rpm": rpm,
                     "forward": [[60.0 + 0.01 * r, 180.0] for r in rpm],
                     "backward": [[59.0 - 0.005 * r, 175.0] for r in rpm]},
        "rpm_plot_max": 4000.0, "f_switch_hz": F_SWITCH,
        "computed_at": computed_at,
    }


PARAMS = {"rpm": 3000.0, "torque_nm": 245.5, "mesh_size_mm": 4.0}
FP = "geo-fingerprint-abc123"


# ---------------------------------------------------------------------------
# A real catalogue and a finished sandbox, both throwaway
# ---------------------------------------------------------------------------


def _cfg_doc(duties) -> dict:
    return {
        "name": CFG, "locked": True,
        "parts": {"shaft": "included"},
        "duties": [{"name": d, "mode": "motor", "current_arms": 461.7,
                    "rpm": 3000.0, "gamma_deg": -8.0,
                    "summary": {"mass_total_kg": MASS_KG,
                                "mass_active_kg": 26.676,
                                "part_states": {"shaft": "included"}},
                    "runs": {"pwm_voltage": {
                        "summary": {"mass_total_kg": MASS_KG,
                                    "part_states": {"shaft": "included"}}}}}
                   for d in duties],
    }


@pytest.fixture()
def real(tmp_path_factory):
    """The REAL catalogue this test files into — a throwaway tree."""
    root = pathlib.Path(tempfile.mkdtemp(prefix="refile_real_"))
    dies = root / "dies" / DIE
    dies.mkdir(parents=True)
    (dies / f"{CFG}.yaml").write_text(
        yaml.safe_dump(_cfg_doc([DUTY, REF_DUTY, OTHER_DUTY]),
                       sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    (root / ".duty_results.json").write_text(
        json.dumps({"version": 1, "results": {}}), encoding="utf-8")
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def sandbox():
    """A FINISHED sandbox: the fields where ``duty_fields`` put them during the
    solve, the rows where ``note_*`` put them, the last-run pickles beside."""
    from motor_ai_sim import duty_fields as df

    sb = pathlib.Path(tempfile.mkdtemp(prefix="refile_sandbox_"))
    at = "2026-09-16T01:06:24+00:00"
    payloads = {"em": em_entry(at), "thermal": thermal_result(at),
                "rotor_stress": rotor_stress_result(at),
                "modes": modes_result(at)}
    for kind, payload in payloads.items():
        assert df.save(DIE, CFG, DUTY, kind, payload, root=sb / "dies",
                       geometry_fingerprint=FP, computed_at=at), kind

    from motor_ai_sim import duty_results as dr
    rows = {
        "rotor_stress": dr.compact_mechanical(
            "rotor_stress", rotor_stress_result(at), PARAMS, FP, at),
        "modes": dr.compact_mechanical("modes", modes_result(at), PARAMS, FP, at),
        "critical_speeds": dr.compact_mechanical(
            "critical_speeds", critical_speeds_result(at), PARAMS, FP, at),
        "thermal": dr.compact_thermal(thermal_result(at), PARAMS, FP, at),
        "coupled": {"computed_at": at, "drive": "pwm", "kind": "coupled"},
        "em": {"source": "duty.result/summary", "kind": "em"},
    }
    for k, v in rows.items():
        v["kind"] = k
    (sb / ".duty_results.json").write_text(json.dumps(
        {"version": 1, "results": {DIE: {CFG: {DUTY: rows,
                                               OTHER_DUTY: {"thermal": {
                                                   "kind": "thermal",
                                                   "computed_at": at}}}}}},
        ensure_ascii=False), encoding="utf-8")
    (sb / ".family_context.json").write_text(json.dumps(
        {"die": DIE, "config": CFG, "duty": DUTY}), encoding="utf-8")

    # …and the machine-level last-run stores, in the shape the routes persist
    # them: what the re-pack fallback reads when no npz was filed.
    with open(sb / ".last_thermal.pkl", "wb") as fh:
        pickle.dump({"field": {"result": thermal_result(at), "params": PARAMS,
                               "geometry_fingerprint": FP, "computed_at": at}},
                    fh)
    with open(sb / ".last_mechanical.pkl", "wb") as fh:
        pickle.dump({k: {"result": r, "params": PARAMS,
                         "geometry_fingerprint": FP, "computed_at": at}
                     for k, r in (("rotor_stress", rotor_stress_result(at)),
                                  ("modes", modes_result(at)),
                                  ("critical_speeds",
                                   critical_speeds_result(at)))}, fh)
    with open(sb / ".last_transient_field.pkl", "wb") as fh:
        pickle.dump({"key": ["k"], "entry": em_entry(at)}, fh)
    yield sb
    shutil.rmtree(sb, ignore_errors=True)


@pytest.fixture()
def reference(real):
    """THE NORMAL PATH, filed through the seams the solve routes call.

    ``duty_fields.save_active`` is what ``routes.simulation`` / ``thermal`` /
    ``mechanical`` call at the tail of a live-machine solve, and
    ``duty_results.note_thermal`` / ``note_mechanical`` are the rows beside them.
    Calling THOSE — rather than writing what this test believes they write — is
    what makes the comparison below a comparison with the product.
    """
    from motor_ai_sim import duty_fields as df
    from motor_ai_sim import duty_results as dr

    mp = pytest.MonkeyPatch()
    mp.setattr(df, "_dies_dir", lambda: real / "dies")
    mp.setattr(df, "active_context", lambda: (DIE, CFG, REF_DUTY))
    mp.setattr(dr, "active_context", lambda: (DIE, CFG, REF_DUTY))
    mp.setattr(dr, "store_path", lambda: real / ".duty_results.json")
    at = "2026-09-16T01:06:24+00:00"
    df.save_active("em", em_entry(at), geometry_fingerprint=FP, computed_at=at)
    df.save_active("thermal", thermal_result(at), geometry_fingerprint=FP,
                   computed_at=at)
    df.save_active("rotor_stress", rotor_stress_result(at),
                   geometry_fingerprint=FP, computed_at=at)
    df.save_active("modes", modes_result(at), geometry_fingerprint=FP,
                   computed_at=at)
    dr.note_thermal(thermal_result(at), PARAMS, FP, at)
    dr.note_mechanical("rotor_stress", rotor_stress_result(at), PARAMS, FP, at)
    dr.note_mechanical("modes", modes_result(at), PARAMS, FP, at)
    dr.note_mechanical("critical_speeds", critical_speeds_result(at), PARAMS,
                       FP, at)
    dr.record(DIE, CFG, REF_DUTY, "coupled",
              {"computed_at": at, "drive": "pwm"})
    dr.note_em_pointer(DIE, CFG, REF_DUTY, at, "build-sig")
    yield REF_DUTY
    mp.undo()


# ---------------------------------------------------------------------------
# (a) THE CONTRACT: the same artefacts as a normal save
# ---------------------------------------------------------------------------


def test_refiled_duty_has_the_normal_saves_artefact_set(real, sandbox,
                                                        reference):
    from motor_ai_sim import duty_refile as rf

    rep = rf.refile(sandbox, DIE, CFG, DUTY, config_dir=real,
                    result_kinds=rf.ALL_RESULT_KINDS)
    assert rep["mass_total_kg"] == pytest.approx(MASS_KG)

    got = rf.artefact_set(DIE, CFG, DUTY, config_dir=real)
    want = rf.artefact_set(DIE, CFG, reference, config_dir=real)
    assert got["fields"] == want["fields"] == list(rf.FIELD_KINDS)
    assert got["results"] == want["results"]
    assert set(rf.MECH_RESULT_KINDS) <= set(got["results"])


def test_every_field_kind_landed_and_is_loadable(real, sandbox):
    from motor_ai_sim import duty_fields as df
    from motor_ai_sim import duty_refile as rf

    rf.refile(sandbox, DIE, CFG, DUTY, config_dir=real)
    fdir = rf.target_fields_dir(DIE, CFG, DUTY, real / "dies")
    for kind in rf.FIELD_KINDS:
        p = fdir / f"{kind}.npz"
        assert p.is_file(), kind
        with np.load(p, allow_pickle=False) as z:      # data, never code
            meta = json.loads(str(z["meta"]))
            assert meta["duty"] == DUTY and meta["kind"] == kind
            assert meta["computed_at"] == "2026-09-16T01:06:24+00:00"
        # …and through the product's own reader, from the real dies dir.
        mp = pytest.MonkeyPatch()
        mp.setattr(df, "_dies_dir", lambda: real / "dies")
        assert df.load(DIE, CFG, DUTY, kind) is not None, kind
        mp.undo()


def test_the_thermal_map_agrees_with_the_thermal_row(real, sandbox):
    """The failure this whole module exists for: the picture and the table.

    Fig. 11's colour bar is the stored map's own maximum and §6's number is the
    row's — a refile that brought one without the other would reproduce exactly
    the 175.2-vs-153.9 report of 2026-09-16.
    """
    from motor_ai_sim import duty_refile as rf

    rf.refile(sandbox, DIE, CFG, DUTY, config_dir=real,
              result_kinds=rf.ALL_RESULT_KINDS)
    p = rf.target_fields_dir(DIE, CFG, DUTY, real / "dies") / "thermal.npz"
    with np.load(p, allow_pickle=False) as z:
        assert float(np.max(z["temperature_per_node"])) == pytest.approx(
            T_MAX, abs=0.05)
    node = json.loads((real / ".duty_results.json").read_text(
        encoding="utf-8"))["results"][DIE][CFG][DUTY]
    assert node["thermal"]["T_max"] == pytest.approx(T_MAX, abs=0.05)


def test_modes_carry_the_carrier_and_criticals_carry_the_sweep(real, sandbox):
    from motor_ai_sim import duty_refile as rf

    rf.refile(sandbox, DIE, CFG, DUTY, config_dir=real)
    node = json.loads((real / ".duty_results.json").read_text(
        encoding="utf-8"))["results"][DIE][CFG][DUTY]
    assert node["modes"]["f_switch_hz"] == pytest.approx(F_SWITCH)
    assert len(node["modes"]["modes"]) == 12
    cam = node["critical_speeds"]["campbell"]
    assert cam and cam["n_points"] == 41
    assert len(cam["forward"]) == 41 and len(cam["backward"]) == 41


# ---------------------------------------------------------------------------
# (b) What a refile must NOT do
# ---------------------------------------------------------------------------


def test_no_other_duty_and_no_yaml_is_touched(real, sandbox):
    from motor_ai_sim import duty_refile as rf

    cfg_file = real / "dies" / DIE / f"{CFG}.yaml"
    before_yaml = (cfg_file.read_bytes(), cfg_file.stat().st_mtime_ns)
    # a row of ANOTHER duty, already in the real store and not this run's
    doc = json.loads((real / ".duty_results.json").read_text(encoding="utf-8"))
    doc["results"] = {DIE: {CFG: {OTHER_DUTY: {"thermal": {"kind": "thermal",
                                                           "mine": True}}}}}
    (real / ".duty_results.json").write_text(json.dumps(doc), encoding="utf-8")

    rf.refile(sandbox, DIE, CFG, DUTY, config_dir=real,
              result_kinds=rf.ALL_RESULT_KINDS)

    after = json.loads((real / ".duty_results.json").read_text(
        encoding="utf-8"))["results"][DIE][CFG]
    assert after[OTHER_DUTY]["thermal"]["mine"] is True
    assert DUTY in after
    assert (cfg_file.read_bytes(), cfg_file.stat().st_mtime_ns) == before_yaml
    # …and no run sidecar was invented beside the fields
    runs = real / "dies" / DIE / "runs" / CFG
    assert sorted(p.name for p in runs.iterdir()) == [rf._stem(DUTY)]


def test_a_mis_weighed_machine_is_refused_before_anything_is_written(real,
                                                                    sandbox):
    from motor_ai_sim import duty_refile as rf

    cfg_file = real / "dies" / DIE / f"{CFG}.yaml"
    doc = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    doc["parts"]["shaft"] = "reference"
    cfg_file.write_text(yaml.safe_dump(doc, sort_keys=False,
                                       allow_unicode=True), encoding="utf-8")
    with pytest.raises(rf.RefileError) as e:
        rf.refile(sandbox, DIE, CFG, DUTY, config_dir=real)
    assert "shaft" in str(e.value)
    assert not rf.target_fields_dir(DIE, CFG, DUTY, real / "dies").exists()


def test_refiling_gives_a_fresh_mtime(real, sandbox):
    """A copied field must not inherit the sandbox's mtime: that stamp is the
    only outward sign a duty's maps were repaired."""
    from motor_ai_sim import duty_refile as rf

    src = rf.sandbox_fields_dir(sandbox, DIE, CFG, DUTY) / "thermal.npz"
    old = src.stat().st_mtime_ns
    time.sleep(0.01)
    rf.refile(sandbox, DIE, CFG, DUTY, config_dir=real)
    dst = rf.target_fields_dir(DIE, CFG, DUTY, real / "dies") / "thermal.npz"
    assert dst.stat().st_mtime_ns > old
    assert dst.read_bytes() == src.read_bytes()      # the bytes ARE the run's


# ---------------------------------------------------------------------------
# (c) The fallback: a sandbox that filed no npz
# ---------------------------------------------------------------------------


def test_missing_npz_are_repacked_from_the_last_run_stores(real, sandbox):
    from motor_ai_sim import duty_refile as rf

    shutil.rmtree(rf.sandbox_fields_dir(sandbox, DIE, CFG, DUTY))
    rep = rf.refile(sandbox, DIE, CFG, DUTY, config_dir=real)
    hows = {k: v["how"] for k, v in rep["fields"]["written"].items()}
    assert hows == {k: "repacked" for k in rf.FIELD_KINDS}, rep["fields"]
    p = rf.target_fields_dir(DIE, CFG, DUTY, real / "dies") / "thermal.npz"
    with np.load(p, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        assert meta["repacked_from"] == str(sandbox)
        assert float(np.max(z["temperature_per_node"])) == pytest.approx(
            T_MAX, abs=0.05)


def test_a_repack_is_refused_for_a_duty_the_sandbox_did_not_last_solve(real,
                                                                       sandbox):
    """The last-run pickles hold ONE answer per machine.  Re-packing them under
    a duty they do not describe is precisely how one duty's map ends up over
    another's name, so it is refused rather than guessed."""
    from motor_ai_sim import duty_refile as rf

    shutil.rmtree(rf.sandbox_fields_dir(sandbox, DIE, CFG, DUTY))
    (sandbox / ".family_context.json").write_text(json.dumps(
        {"die": DIE, "config": CFG, "duty": OTHER_DUTY}), encoding="utf-8")
    rep = rf.refile(sandbox, DIE, CFG, DUTY, config_dir=real)
    assert rep["fields"]["written"] == {}
    assert sorted(rep["fields"]["missing"]) == sorted(rf.FIELD_KINDS)

"""The per-duty DUTY-CYCLE record — what survives compaction, and how big it is.

``duty_results`` is the side store every report column is built from, and it has
one rule the rest of this project does not: it must stay a file an engineer can
open.  Every other kind in it is a few dozen numbers.  The duty cycle is the
first that carries SERIES — four nodes' temperature through one cycle, the hot
spot above them and the loss that drove it — and a 200-cycle S3 solved at 60
samples a segment produces 24 000 points per node, which is a megabyte and a half
of JSON nobody will ever read.

So this module pins two things and nothing else:

  (a) the record ROUND-TRIPS: what ``note_duty_cycle`` writes under (die,
      configuration, duty) is what ``get`` reads back, with the shape B.3
      describes — ``spec``, ``network``, ``cycle``, ``split``, ``limits``,
      ``point`` — so the report's per-duty reader finds what it expects;
  (b) it is SMALL: ≤ 400 samples per node and under 40 kB on disk for that
      200-cycle S3, with the peaks and the limits kept exactly.  A decimated
      chart is still the answer; a truncated one would not be.

Nothing here solves anything, and the store is redirected into ``tmp_path``:
``config/.duty_results.json`` is the user's own, and this suite promises not to
touch it.
"""
from __future__ import annotations

import json
import math

import pytest

from motor_ai_sim import duty_results as dr

DIE, CFG, DUTY = "TESTDIE 85", "L13", "peak 200C"

NODES = ("winding", "stator", "rotor", "magnet")


def _series(n: int, base: float, amp: float):
    return [round(base + amp * math.sin(i / 40.0), 3) for i in range(n)]


def _record(n_samples: int = 24_000) -> dict:
    """A duty-cycle record the shape the route returns one, with the series of a
    200-cycle S3 at 60 samples a segment."""
    return {
        "kind": "duty_cycle",
        "computed_at": "2026-09-14T11:00:00+00:00",
        "geometry_fingerprint": "fp-L13",
        "die": DIE, "configuration": CFG, "duty": DUTY,
        "spec": {
            "kind": "S3", "cycle_s": 60.0, "duration_s": 60.0, "ed_pct": 25.0,
            "t_on_s": None, "rest_duty": None,
            "calibration_duty": "rated 120C",
            "calibration_source": "the configuration's rated duty",
            "t_start_c": 40.0, "n_cycles_max": 200,
            "note": "intermittent duty",
            "segments": [
                {"duty": "peak 200C", "t_s": 15.0, "rpm": 1000.0,
                 "coil_ref_c": 200.0, "total_W": 687.3,
                 "losses_W": {"winding": 676.6, "stator": 2.3, "rotor": 0.2,
                              "magnet": 8.2},
                 "note": "", "SOMETHING_NEW": {"a": [1] * 1000}},
                {"duty": None, "t_s": 45.0, "rpm": 0.0, "coil_ref_c": 20.0,
                 "total_W": 0.0,
                 "losses_W": {n: 0.0 for n in NODES},
                 "note": "unpowered pause"},
            ],
        },
        "network": {
            "G_W_per_K": {"w_s": 6.37, "r_s": 0.0, "m_r": 0.0, "s_mount": 2.0,
                          "r_bore": 0.03, "r_shaft": 0.0},
            "areas_m2": {"housing": 0.00347, "winding_ends": 0.0054},
            "C_J_per_K": {"winding": 41.2, "stator": 59.2, "rotor": 39.0,
                          "magnet": 32.2},
            "cp_sources": {"winding": "library", "stator": "default",
                           "rotor": "default", "magnet": "library"},
            "t_ambient_c": 40.0, "t_mount_c": 40.0, "emissivity": 0.9,
            "d_housing_m": 0.085, "hot_spot_offset_K": 2.4,
            "merged": [],
            "active_nodes": ["winding", "stator", "rotor", "magnet"],
            "link_kinds": {"w_s": "calibrated", "r_s": "physical",
                           "m_r": "physical"},
            "links": {
                "w_s": {"kind": "calibrated", "nodes": ["winding", "stator"],
                        "G_W_per_K": 6.37,
                        "basis": "fitted to the calibration map"},
                "r_s": {"kind": "physical", "nodes": ["rotor", "stator"],
                        "G_W_per_K": 0.2732,
                        "basis": "G_rs = k_eff·A/δ + h_rad·A"},
                "m_r": {"kind": "physical", "nodes": ["magnet", "rotor"],
                        "G_W_per_K": 4.576,
                        "basis": "G_mr = k·A_root/(h_mag/2)"}},
            "h_sources": {"housing": "cooling_models.outer_still"},
            "calibration": {"duty": "rated 120C", "P_cu_W": 58.6},
            "calibration_duty": "rated 120C",
            "notes": ["rotor and stator keep a PHYSICAL conductance"],
            # the heavy thing a lazy "everything but the arrays" rule would keep
            "capacity_parts": {n: [{"name": n, "mass_kg": 0.1}] for n in NODES},
        },
        "cycle": {
            "converged": True, "n_cycles": 14, "residual_K": 0.03,
            "closure_pct": 0.004,
            "peak_c": {"winding": 186.4, "stator": 120.1, "rotor": 120.1,
                       "magnet": 120.1},
            "min_c": {n: 84.0 for n in NODES},
            "mean_c": {n: 110.0 for n in NODES},
            "start_state_c": {n: 84.0 for n in NODES},
            "winding_hot_peak_c": 188.8, "winding_hot_mean_c": 121.0,
            "hot_spot_offset_K": 2.4,
            "energy_in_J": 10310.0, "energy_out_J": 10309.0, "stored_J": 0.4,
            "segments": [[0.0, 15.0, "peak 200C"], [15.0, 60.0, None]],
            "note": "the cycle map's fixed point",
            "t_s": _series(n_samples, 30.0, 30.0),
            "T_c": {n: _series(n_samples, 120.0, 40.0) for n in NODES},
            "winding_hot_c": _series(n_samples, 122.0, 40.0),
            "P_W": {n: _series(n_samples, 100.0, 90.0) for n in NODES},
            "n_samples": n_samples, "n_solved": n_samples,
            # a payload that grew a new array tomorrow
            "flows_W": {"housing": _series(n_samples, 3.0, 1.0)},
        },
        "split": {"basis": "time-averaged over 60.000 s (one cycle)",
                  "stator_side_W": 168.0, "rotor_side_W": 3.0,
                  "stator_pct": 98.2, "rotor_pct": 1.8,
                  "housing_W": 3.1, "mount_W": 118.0,
                  "winding_end_faces_W": 32.0, "stator_end_faces_W": 15.0,
                  "rotor_end_faces_W": 1.8, "magnet_end_faces_W": 1.0,
                  "bore_W": 0.2, "shaft_ends_W": 0.0, "gap_W": -1.0,
                  "winding_to_core_W": 160.0, "closure_W": 0.02,
                  "closure_pct": 0.01, "note": "the four end-face lines"},
        "limits": {"winding_limit_c": 200.0,
                   "winding_limit_note": "judged on the HOT SPOT",
                   "magnet_limit_c": None,
                   "magnet_limit_note": "the magnet cards carry no maximum",
                   "limits_c": {"winding": 200.0},
                   "s2_time_to_limit_s": 34.1, "s2_limiting_part": "winding",
                   "s2_horizon_s": 600.0, "s2_note": "winding reaches 200 °C",
                   "ed_allowable_pct": 27.5, "ed_requested_pct": 25.0,
                   "ed_limiting_part": "winding",
                   "ed_curve": [[5.0, 70.0], [25.0, 188.8], [50.0, 300.0]],
                   "ed_note": "at 27.5 % duty the winding peak sits on its limit"},
        "point": {"rpm": 1000.0, "I_phase_rms": 14.708, "gamma_deg": 2.0,
                  "coil_temp_c": 120.0, "cooling_mode": "robotics",
                  "ambient_temp": 40.0, "bore_mode": "still",
                  "emissivity": 0.9, "mount_g_w_per_k": 2.0,
                  "mount_temp_c": 40.0, "calibration_duty": "rated 120C"},
        # the two blocks a whitelist must drop
        "calibration_map": {"components": {"winding": {"avg": 103.7}}},
        "temperature_per_node": [20.0] * 50_000,
        "elapsed_s": 41.2, "solve_time_s": 38.9, "cached": True,
    }


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """``.duty_results.json`` in tmp_path — never beside the user's machine."""
    p = tmp_path / ".duty_results.json"
    monkeypatch.setattr(dr, "store_path", lambda: p)
    monkeypatch.setattr(dr, "active_context", lambda: None)
    return p


# ---------------------------------------------------------------------------
# (a) the kind exists and is filed where the report looks
# ---------------------------------------------------------------------------

def test_duty_cycle_is_a_kind_the_store_accepts(store):
    assert "duty_cycle" in dr.KINDS
    ok = dr.note_duty_cycle(_record(200), {}, "fp-L13", "2026-09-14T11:00:00",
                            die=DIE, cfg=CFG, duty=DUTY)
    assert ok and store.exists()
    back = dr.get(DIE, CFG)[DUTY]["duty_cycle"]
    assert back["kind"] == "duty_cycle"
    assert dr.kinds_present(dr.get(DIE, CFG)[DUTY]) == ["duty_cycle"]


def test_without_a_name_and_without_a_context_nothing_is_filed(store):
    """A cycle solved for a machine that is not a catalogued duty has nowhere to
    go, and inventing a place for it is how one duty's answer ends up under
    another's name."""
    assert dr.note_duty_cycle(_record(50), {}, None) is False
    assert not store.exists()


def test_it_does_not_disturb_the_duties_other_kinds(store):
    """The failure this store exists to prevent: one solve emptying another's
    column."""
    dr.record(DIE, CFG, DUTY, "thermal",
              dr.compact_thermal({"components": {"winding": {"avg": 103.7}}},
                                 {"rpm": 1000}, "fp-L13"))
    dr.note_duty_cycle(_record(200), {}, "fp-L13", None,
                       die=DIE, cfg=CFG, duty=DUTY)
    entry = dr.get(DIE, CFG)[DUTY]
    assert entry["thermal"]["components"]["winding"]["avg"] == 103.7
    assert entry["duty_cycle"]["cycle"]["converged"] is True
    assert dr.kinds_present(entry) == ["thermal", "duty_cycle"]


# ---------------------------------------------------------------------------
# (b) the shape B.3 promises, and the whitelist
# ---------------------------------------------------------------------------

def test_the_record_round_trips_with_the_shape_the_report_reads(store):
    dr.note_duty_cycle(_record(24_000), {"rpm": 1000.0}, "fp-L13",
                       "2026-09-14T11:00:00", die=DIE, cfg=CFG, duty=DUTY)
    got = dr.get(DIE, CFG)[DUTY]["duty_cycle"]

    for block in ("spec", "network", "cycle", "split", "limits", "point"):
        assert isinstance(got[block], dict) and got[block], block
    assert got["computed_at"] == "2026-09-14T11:00:00"
    assert got["geometry_fingerprint"] == "fp-L13"
    assert got["duty"] == DUTY

    # the numbers a report table prints, exactly as they went in
    assert got["cycle"]["winding_hot_peak_c"] == 188.8
    assert got["cycle"]["peak_c"]["winding"] == 186.4
    assert got["cycle"]["n_cycles"] == 14
    assert got["limits"]["ed_allowable_pct"] == 27.5
    assert got["limits"]["s2_time_to_limit_s"] == 34.1
    assert got["limits"]["ed_curve"][1] == [25.0, 188.8]
    assert got["split"]["stator_pct"] == 98.2
    assert got["network"]["C_J_per_K"]["winding"] == 41.2
    assert got["network"]["cp_sources"]["stator"] == "default"
    assert got["network"]["G_W_per_K"]["s_mount"] == 2.0
    # WHERE each internal conductance came from survives compaction: a gap
    # conductance a report prints has to say whether it was fitted or computed
    assert got["network"]["link_kinds"]["r_s"] == "physical"
    assert got["network"]["links"]["m_r"]["G_W_per_K"] == 4.576
    assert got["network"]["active_nodes"][-1] == "magnet"
    assert got["spec"]["ed_pct"] == 25.0
    assert got["spec"]["calibration_duty"] == "rated 120C"
    assert [s["duty"] for s in got["spec"]["segments"]] == [DUTY, None]
    assert got["point"]["cooling_mode"] == "robotics"


def test_a_record_written_before_the_physical_links_still_reads_back(store):
    """The records on disk from before 2026-09-15 have ``merged`` and no
    ``links``, and they are still answers.

    A stored duty cycle is a machine's history: the columns that were solved
    with the merged network must keep loading and rendering exactly as they
    were, saying ``merged`` — they are not silently re-labelled, and nothing
    downstream may assume the new block is there.
    """
    old = _record(200)
    net = old["network"]
    net["merged"] = [["rotor", "stator"], ["magnet", "rotor"]]
    net["G_W_per_K"]["r_s"] = 0.0
    net["G_W_per_K"]["m_r"] = 0.0
    net["notes"] = ["rotor and stator are MERGED"]
    for gone in ("links", "link_kinds", "active_nodes"):
        net.pop(gone)

    dr.note_duty_cycle(old, {}, "fp-L13", None, die=DIE, cfg=CFG, duty=DUTY)
    got = dr.get(DIE, CFG)[DUTY]["duty_cycle"]["network"]

    assert got["merged"] == [["rotor", "stator"], ["magnet", "rotor"]]
    assert got["G_W_per_K"]["r_s"] == 0.0
    assert "MERGED" in got["notes"][0]
    # the new keys are simply absent — never invented, never defaulted
    assert "links" not in got and "link_kinds" not in got
    # and everything a report reads off the block is still there
    assert got["C_J_per_K"]["winding"] == 41.2
    assert got["hot_spot_offset_K"] == 2.4


def test_the_whitelist_drops_what_a_table_cannot_use(store):
    """Explicit lists, not "everything but the arrays": a solver that grows a
    new 40 MB payload tomorrow must not silently grow this file too."""
    dr.note_duty_cycle(_record(1_000), {}, "fp", None,
                       die=DIE, cfg=CFG, duty=DUTY)
    got = dr.get(DIE, CFG)[DUTY]["duty_cycle"]
    assert "temperature_per_node" not in got
    assert "calibration_map" not in got
    assert "flows_W" not in got["cycle"]
    assert "capacity_parts" not in got["network"]
    assert "SOMETHING_NEW" not in got["spec"]["segments"][0]


# ---------------------------------------------------------------------------
# (c) the size
# ---------------------------------------------------------------------------

def test_a_200_cycle_S3_decimates_to_400_samples_and_under_40_kB(store):
    """24 000 solved points per node → 400 stored, and the file stays readable."""
    rec = _record(24_000)
    dr.note_duty_cycle(rec, {}, "fp-L13", None, die=DIE, cfg=CFG, duty=DUTY)
    got = dr.get(DIE, CFG)[DUTY]["duty_cycle"]
    cyc = got["cycle"]

    assert len(cyc["t_s"]) <= dr.DUTY_CYCLE_MAX_SAMPLES == 400
    assert cyc["n_samples"] == len(cyc["t_s"])
    assert cyc["n_solved"] == 24_000          # what was actually integrated
    for n in NODES:
        assert len(cyc["T_c"][n]) == len(cyc["t_s"])
        assert len(cyc["P_W"][n]) == len(cyc["t_s"])
    assert len(cyc["winding_hot_c"]) == len(cyc["t_s"])

    # the first and the LAST sample are both kept — a decimated chart that lost
    # the end of the cycle would be a truncated one
    assert cyc["t_s"][0] == rec["cycle"]["t_s"][0]
    assert cyc["t_s"][-1] == rec["cycle"]["t_s"][-1]

    size = len(json.dumps(got, ensure_ascii=False))
    assert size < 40_000, "the stored duty-cycle record is %d bytes" % size
    # …and the whole store with it (one duty, one kind)
    assert store.stat().st_size < 60_000


def test_a_short_cycle_is_stored_whole(store):
    """Decimation is a ceiling, not a rule: 120 samples stay 120."""
    dr.note_duty_cycle(_record(120), {}, "fp", None,
                       die=DIE, cfg=CFG, duty=DUTY)
    cyc = dr.get(DIE, CFG)[DUTY]["duty_cycle"]["cycle"]
    assert len(cyc["t_s"]) == 120 and cyc["n_samples"] == 120

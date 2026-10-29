""""Refine ripple" (owner 2026-10-29, option 1).

The machine runs at its configured air-gap layers (1/side).  When the
rotor-/stator-side ring check does not pass, the ripple is shown as not
accurate and the engineer may ask for a refined ripple: the SAME point solved
at >= 3 layers/side, stepping one more while the ring check still fails (cap 4).

Pinned here, with a stub in place of the solve (no FEM):
  * the refine run asks the solver for >= 3 layers and for the stepping rule;
  * it carries a compact ``ripple_refined`` block (ripple, layers, ring error);
  * it never becomes the machine's last transient and never files a duty row;
  * a plain run is untouched (configured layers, no stepping, no block).
"""
from __future__ import annotations

import pytest

from tests.test_run_ledger import RUN, ledger_dir, sim, solver  # noqa: F401 (fixtures)


@pytest.fixture
def ringed(monkeypatch, solver):
    """The stub solve, answering with a ring self-check: failing at the
    configured 1/side, passing at the refined level."""
    import motor_ai_sim.simulation.fem_solver_2d as fs
    calls = solver
    base = fs.em_transient_eval

    def fake(**kw):
        out = dict(base(**kw))
        gl = float(kw.get("gap_layers") or 1.0)
        eps = 0.52 if gl < 2 else 0.03
        out.update({"T_ripple_pp_Nm": 34.0 if gl < 2 else 20.0,
                    "T_ripple_pct": 3.3 if gl < 2 else 1.9,
                    "gap_layers_effective": gl,
                    "coulomb_torque": {"layer_self_check": {
                        "rel_to_ripple_scale": eps,
                        "ripple_mesh_limited": eps > 0.05}}})
        return out
    monkeypatch.setattr(fs, "em_transient_eval", fake)
    return calls


def test_plain_run_keeps_the_configured_layers(sim, ringed):
    res = sim.get_fem_transient(**RUN, gap_layers=1.0)
    assert ringed[-1]["gap_layers"] == 1.0
    assert ringed[-1].get("gap_refine") is False
    assert "ripple_refined" not in res
    assert res["summary"]["ripple_mesh_limited"] is True


def test_refine_solves_at_three_layers_and_keeps_the_base_run(sim, ringed, monkeypatch):
    noted = []
    import motor_ai_sim.duty_results as dr
    monkeypatch.setattr(dr, "note_em_run", lambda *a, **k: noted.append(a))
    base = sim.get_fem_transient(**RUN, gap_layers=1.0)
    base_key = sim._last_transient_ref["key"]
    n_noted = len(noted)

    ref = sim.get_fem_transient(**RUN, gap_layers=1.0, ripple_refine=True)
    assert ringed[-1]["gap_layers"] == 3.0
    assert ringed[-1]["gap_refine"] is True
    blk = ref["ripple_refined"]
    assert blk["gap_layers_per_side"] == 3.0 and blk["base_gap_layers_per_side"] == 1.0
    assert blk["passed"] is True and blk["self_check_rel_to_ripple_scale"] == 0.03
    assert blk["T_ripple_pp_Nm"] == 20.0
    # the base run stays the machine's result: last transient, duty row untouched
    assert sim._last_transient_ref["key"] == base_key
    assert len(noted) == n_noted


def test_refine_has_its_own_cache_key(sim, ringed):
    sim.get_fem_transient(**RUN, gap_layers=3.0)
    n = len(ringed)
    ref = sim.get_fem_transient(**RUN, gap_layers=3.0, ripple_refine=True)
    assert len(ringed) == n + 1, "a plain 3-layer entry must not answer a refine"
    assert ref["ripple_refined"]["gap_layers_per_side"] == 3.0


# ── the one rule every reader quotes by (motor_ai_sim.ripple_accuracy) ─────

def test_quoted_ripple_prefers_the_refined_value():
    from motor_ai_sim.ripple_accuracy import quoted_ripple, ripple_note, ripple_text
    base = {"T_ripple_pct": 3.3, "ripple_mesh_limited": True,
            "ripple_self_check_rel": 0.52, "gap_layers_effective": 1.0}
    q = quoted_ripple(base)
    assert q == {"value": 3.3, "accurate": False, "refined": False,
                 "layers": 1.0, "ring_error": 0.52}
    assert ripple_text(base, lambda v: f"{v:.1f}") == "3.3 (not accurate)"
    assert "52 %" in ripple_note(base) and ">= 3 layers" in ripple_note(base)
    refined = dict(base, ripple_refined={"T_ripple_pct": 1.9, "passed": True,
                                         "gap_layers_per_side": 3.0,
                                         "self_check_rel_to_ripple_scale": 0.03})
    q = quoted_ripple(refined)
    assert (q["value"], q["accurate"], q["refined"], q["layers"]) == (1.9, True, True, 3.0)
    assert ripple_text(refined, lambda v: f"{v:.1f}") == "1.9"
    assert ripple_note(refined) == "refined at 3 air-gap layers/side"
    # a passing run, and a summary with no ring check on record: plain values
    assert ripple_text({"T_ripple_pct": 2.0, "ripple_mesh_limited": False},
                       lambda v: f"{v:.1f}") == "2.0"
    assert quoted_ripple({}, 4.2)["value"] == 4.2
    assert quoted_ripple({}, 4.2)["accurate"] is None

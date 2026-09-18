"""THE CATALOGUE CONSTANTS — the same machine at 20 °C.

Owner, 2026-09-18: *«для каждого отчёта делать прогон на холодную 20 °C, чтобы
находить все коэффициенты KV, Kt, Km, Km/mass, которые фигурируют во всех
каталогах моторов и нужны для сравнения; это нужно отдельно упомянуть в
отчёте»*.

Every constant this project reports is at the duty's own temperatures, which is
honest and is not COMPARABLE: a catalogue quotes room-temperature numbers, so a
Kt measured with the winding at 172 °C reads well below a competitor's page and
nothing says why.  The coupled loop therefore ends with one electromagnetic pass
at 20/20, and this file pins the five things that can go wrong with it:

  (a) IT IS MADE ONCE, at 20 and 20, at the SAME operating point and on the same
      drive — and it is the LAST pass, so nothing is fed back from it: it is a
      measurement of the machine, not a state the machine is in;
  (b) IT LEAVES NO TRACE.  A background solve — no field snapshot, no persisted
      last transient, no journal entry — or the cold run would replace the duty's
      own run on the Electromagnetic tab;
  (c) THE CONVENTIONS ARE §4's.  Kt, Km, Km/mass and ψ_PM carry k_3d; KV is
      DIVIDED by it (rpm per volt goes as 1/flux); in delta the Kt a catalogue
      prints is the one per LINE amp.  Both the 2-D and the corrected value are
      kept, so neither page has to guess which it is reading;
  (d) IT SURVIVES THE STORE and reaches the report and the datasheet;
  (e) NOTHING IS INVENTED.  A duty whose cold pass was never made gets a row
      saying so and naming the two ways to make it — never a constant
      extrapolated out of a hot one.

The halves are faked (the template of tests/test_coupled_duty_cycle): what is
unproven elsewhere is the WIRING and the conventions, and a real solve would
prove them most slowly.
"""
from __future__ import annotations

import pytest

from tests.test_coupled import COOLING as PANEL_COOLING, EM_BODY

LOOP_BODY = {**EM_BODY, "thermal_settings": PANEL_COOLING,
             "magnet_temp_c": 90.0, "mechanical": False}

#: A 2-D summary with a 3-D passport, delta-connected — the shape that makes
#: every convention in (c) visible at once.
COLD_SUMMARY = {
    "P_loss_total_W": 700.0, "T_em_avg_Nm": 5.0, "rpm": 1000.0,
    "psi_pm_Wb": 0.0100, "Kt_Nm_per_Arms": 0.2000,
    "Kt_Nm_per_A_line": 0.1000, "Km_Nm_sqrtW": 0.4000,
    "Km_per_mass_Nm_sqrtW_kg": 0.8000,
    "KV_noload_rpm_per_V_line": 50.0, "KV_rpm_per_V_line": 40.0,
    "Ld_mH": 0.5, "Lq_mH": 0.6, "saliency_Lq_over_Ld": 1.2,
    "R_phase_ohm": 0.0500, "mass_total_kg": 0.5,
    "star_delta": "delta", "connection": "2P",
    "end3d": {"k_flux": 0.9000},
}


def _fake(monkeypatch, *, summary=None, cold_raises=False, background=None):
    """``_em_run`` / ``_thermal_solve`` as recorders, with the cold pass visible.

    Every temperature pair an electromagnetic run is asked for is recorded,
    together with whether `_BACKGROUND_RUN` was set at the time — which is the
    only thing that keeps the cold pass from replacing the duty's own run.
    """
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes.simulation import _BACKGROUND_RUN

    seen = {"pairs": [], "bg": [], "drive": []}

    def _em(body, *, coil_temp_c, magnet_temp_c, inverter=None, **_k):
        seen["pairs"].append((round(float(coil_temp_c), 4),
                              None if magnet_temp_c is None
                              else round(float(magnet_temp_c), 4)))
        seen["bg"].append(bool(_BACKGROUND_RUN.get()))
        seen["drive"].append("pwm" if inverter else "sine")
        cold = float(coil_temp_c) == 20.0
        if cold and cold_raises:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="no solve at 20 °C")
        s = dict(summary if (cold and summary) else
                 {"P_loss_total_W": 700.0, "T_em_avg_Nm": 5.0, "rpm": 1000.0})
        s["coil_temp_C"] = float(coil_temp_c)
        return {"summary": s, "computed_at": "2026-09-18T09:00:00"}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        return {"ok": True,
                "components": {"winding": {"avg": 130.0, "max": 134.0},
                               "magnet": {"avg": 93.0, "max": 95.0}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_ttl_step", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None,
                        raising=True)
    return seen


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _run(client, **body):
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 2, "tol_k": 1.0, **body})
    assert r.status_code == 200, r.text[:800]
    return r.json()["coupling"]


# ---------------------------------------------------------------------------
# (a) + (b) one pass, at 20/20, at the end, leaving no trace
# ---------------------------------------------------------------------------

def test_the_loop_ends_with_one_pass_at_20_and_20(client, monkeypatch):
    seen = _fake(monkeypatch, summary=COLD_SUMMARY)
    c = _run(client)
    # The LAST pair, and only that one, is the cold one.
    assert seen["pairs"][-1] == (20.0, 20.0), seen["pairs"]
    assert seen["pairs"].count((20.0, 20.0)) == 1
    # …and it is a BACKGROUND solve, so it replaces nothing the user pressed.
    assert seen["bg"][-1] is True
    assert not any(seen["bg"][:-1])
    assert c["constants_20c"]["coil_temp_c"] == 20.0
    assert c["constants_20c"]["magnet_temp_c"] == 20.0
    # NOT fed back: the record's own temperatures are the loop's, never 20 °C.
    assert c["coil_temp_c"] != 20.0


def test_the_cold_pass_runs_at_this_duty_s_point_and_drive(client, monkeypatch):
    seen = _fake(monkeypatch, summary=COLD_SUMMARY)
    c = _run(client)
    pt = c["constants_20c"]["point"]
    assert pt["rpm"] == 1000.0 or pt["rpm"] > 0
    assert pt["I_phase_rms"] == LOOP_BODY["I_phase_rms"]
    assert pt["gamma_deg"] == LOOP_BODY["gamma_deg"]
    assert pt["drive"] == "current"
    assert seen["drive"][-1] == "sine"


def test_it_can_be_switched_off(client, monkeypatch):
    """A sweep point or an errand does not need a catalogue line, and the pass
    is a whole transient."""
    seen = _fake(monkeypatch, summary=COLD_SUMMARY)
    c = _run(client, cold_constants=False)
    assert (20.0, 20.0) not in seen["pairs"]
    assert "constants_20c" not in c


def test_a_refusal_at_20_degrees_leaves_the_key_absent(client, monkeypatch):
    """NOTHING INVENTED: a machine that could not be solved cold has no
    catalogue constants, and the loop's own answer is untouched."""
    _fake(monkeypatch, summary=COLD_SUMMARY, cold_raises=True)
    c = _run(client)
    assert "constants_20c" not in c
    assert c["coil_temp_c"] > 0          # the loop still answered


# ---------------------------------------------------------------------------
# (c) the conventions — §4's, to the digit
# ---------------------------------------------------------------------------

def test_kt_km_carry_k3d_and_kv_is_divided_by_it(client, monkeypatch):
    """The one arithmetic claim.  Kt goes as the flux and KV as 1/flux, so a
    passport of 0.9 multiplies one and divides the other — a block that applied
    the same correction to both would put KV 20 % out."""
    _fake(monkeypatch, summary=COLD_SUMMARY)
    c = _run(client)
    k = c["constants_20c"]
    assert k["k_3d"] == pytest.approx(0.9, abs=1e-9)
    assert k["two_d"]["Kt_Nm_per_A_line"] == pytest.approx(0.1, abs=1e-9)
    assert k["k3d"]["Kt_Nm_per_A_line"] == pytest.approx(0.09, abs=1e-9)
    assert k["k3d"]["Km_Nm_sqrtW"] == pytest.approx(0.36, abs=1e-9)
    assert k["k3d"]["Km_per_mass_Nm_sqrtW_kg"] == pytest.approx(0.72, abs=1e-9)
    assert k["k3d"]["psi_pm_Wb"] == pytest.approx(0.009, abs=1e-9)
    # …and KV the other way.
    assert k["two_d"]["KV_noload_rpm_per_V_line"] == pytest.approx(50.0)
    assert k["k3d"]["KV_noload_rpm_per_V_line"] == pytest.approx(50.0 / 0.9,
                                                                 abs=1e-6)


def test_in_delta_the_headline_kt_is_the_one_per_line_amp(client, monkeypatch):
    """What an inverter is rated against, and what a catalogue prints."""
    _fake(monkeypatch, summary=COLD_SUMMARY)
    k = _run(client)["constants_20c"]
    assert k["point"]["star_delta"] == "delta"
    assert k["kt_line_Nm_per_A"] == pytest.approx(0.09, abs=1e-9)   # ×k_3d
    assert k["kv_line_rpm_per_V"] == pytest.approx(50.0 / 0.9, abs=1e-6)
    assert k["km_Nm_sqrtW"] == pytest.approx(0.36, abs=1e-9)
    assert k["km_per_mass_Nm_sqrtW_kg"] == pytest.approx(0.72, abs=1e-9)
    assert k["R_phase_20_ohm"] == pytest.approx(0.05, abs=1e-9)
    assert k["mass_kg"] == pytest.approx(0.5, abs=1e-9)


def test_a_star_machine_takes_the_per_winding_kt(client, monkeypatch):
    _fake(monkeypatch, summary={**COLD_SUMMARY, "star_delta": "star"})
    k = _run(client)["constants_20c"]
    assert k["point"]["star_delta"] == "star"
    assert k["kt_line_Nm_per_A"] == pytest.approx(0.2 * 0.9, abs=1e-9)


def test_no_passport_means_no_corrected_column_and_no_invention(
        client, monkeypatch):
    _fake(monkeypatch, summary={k: v for k, v in COLD_SUMMARY.items()
                                if k != "end3d"})
    k = _run(client)["constants_20c"]
    assert k["k_3d"] is None
    assert k["k3d"] == {}
    # The headline figures then fall back to the 2-D ones, said in the note.
    assert k["kt_line_Nm_per_A"] == pytest.approx(0.1, abs=1e-9)
    assert "k_3d" not in k["note"]


# ---------------------------------------------------------------------------
# (d) the store, the report and the datasheet
# ---------------------------------------------------------------------------

BLOCK = {
    "coil_temp_c": 20.0, "magnet_temp_c": 20.0,
    "point": {"rpm": 1000.0, "I_phase_rms": 45.96, "gamma_deg": 2.0,
              "drive": "current", "star_delta": "star"},
    "k_3d": 0.925,
    "two_d": {"Kt_Nm_per_Arms": 0.2, "Km_Nm_sqrtW": 0.4,
              "Km_per_mass_Nm_sqrtW_kg": 0.8, "psi_pm_Wb": 0.01,
              "KV_noload_rpm_per_V_line": 54.31, "Ld_mH": 0.5, "Lq_mH": 0.6,
              "R_phase_ohm": 0.05, "mass_total_kg": 0.367},
    "k3d": {"Kt_Nm_per_Arms": 0.185, "Km_Nm_sqrtW": 0.37,
            "Km_per_mass_Nm_sqrtW_kg": 0.74, "psi_pm_Wb": 0.00925,
            "KV_noload_rpm_per_V_line": 58.71},
    "kt_line_Nm_per_A": 0.185, "kv_line_rpm_per_V": 58.71,
    "km_Nm_sqrtW": 0.37, "km_per_mass_Nm_sqrtW_kg": 0.74,
    "R_phase_20_ohm": 0.05, "mass_kg": 0.367,
    "note": "solved with the winding and the magnets at 20 °C…",
}


def test_the_block_survives_compact_coupled():
    from motor_ai_sim.duty_results import compact_coupled
    rec = compact_coupled({"coupling": {"coil_temp_c": 183.6,
                                        "constants_20c": dict(BLOCK)},
                           "computed_at": "2026-09-18T09:00:00"})
    assert rec["constants_20c"]["kt_line_Nm_per_A"] == 0.185
    assert rec["constants_20c"]["point"]["rpm"] == 1000.0
    assert "constants_20c" not in compact_coupled({"coupling": {}})


def test_the_report_prints_the_four_catalogue_constants():
    from motor_ai_sim.report import cold_constant_rows, cold_constants_of
    rows = cold_constant_rows({"constants_20c": dict(BLOCK)})
    labels = [r[0] for r in rows]
    assert labels[0] == "Constant"
    for want in ("KV, no load [rpm/V]", "Torque constant Kt [N·m/A rms]",
                 "Motor constant Km [N·m/√W]", "Km per mass [N·m/(√W·kg)]",
                 "Phase resistance R₂₀ [mΩ]"):
        assert want in labels, (want, labels)
    cells = {r[0]: r[1] for r in rows}
    # The 3-D-corrected values, which is what §4 prints for the hot ones too.
    assert "58.71" in cells["KV, no load [rpm/V]"]
    assert "0.185" in cells["Torque constant Kt [N·m/A rms]"]
    assert "0.37" in cells["Motor constant Km [N·m/√W]"]
    # …and the point they were taken at is part of the answer.
    assert any(r[0] == "…measured at" for r in rows)
    assert cold_constants_of({"constants_20c": dict(BLOCK)}) is not None


def test_a_duty_with_no_cold_pass_says_so_and_invents_nothing():
    """(e).  The subsection is always printed — an absent one would read as a
    machine that HAS no catalogue constants, which is a different statement."""
    from motor_ai_sim.report import cold_constant_rows, cold_constants_of
    rows = cold_constant_rows({"coil_temp_c": 183.6})
    assert len(rows) == 2
    assert rows[1][0] == "KV, Kt, Km, Km per mass"
    assert "re-run the coupled loop" in rows[1][2]
    assert "/api/coupled/constants_20c" in rows[1][2]
    # No number anywhere in that row.
    assert not any(ch.isdigit() for ch in str(rows[1][1]))
    assert cold_constants_of({"coil_temp_c": 183.6}) is None
    assert cold_constants_of(None) is None


def test_the_datasheet_carries_the_same_four_when_a_duty_has_them(tmp_path):
    """The Excel card is what a buyer actually compares, so the four constants
    have to reach it — and stay out of it when nothing solved them."""
    from openpyxl import load_workbook

    from motor_ai_sim.datasheet import build_datasheet

    die_doc = {"geometry": {"num_poles": 28, "num_slots": 24,
                            "stator_diameter": 85.0, "motor_length": 13.0}}
    cfg_doc = {"winding": {"star_delta": "star"},
               "duties": [{"name": "peak", "current_arms": 45.96, "rpm": 1000.0,
                           "gamma_deg": 2.0,
                           "summary": {"T_em_avg_Nm": 7.57,
                                       "I_phase_rms_A": 45.96,
                                       "R_phase_ohm": 0.05}}]}

    def _labels(blob):
        p = tmp_path / "card.xlsx"
        p.write_bytes(blob)
        ws = load_workbook(p).active
        return {str(ws.cell(row=r, column=1).value or ""): ws.cell(row=r, column=2).value
                for r in range(1, ws.max_row + 1)}

    with_ = _labels(build_datasheet(
        die="D", cfg="L13", die_doc=die_doc, cfg_doc=cfg_doc,
        coupled={"peak": {"constants_20c": dict(BLOCK)}}))
    assert "KV at 20 °C (rpm/V)" in with_
    assert with_["Kt at 20 °C (N·m/A rms)"] == pytest.approx(0.185, abs=1e-9)
    assert with_["Km at 20 °C (N·m/√W)"] == pytest.approx(0.37, abs=1e-9)
    assert with_["Km per mass at 20 °C (N·m/(√W·kg))"] == pytest.approx(
        0.74, abs=1e-9)

    without = _labels(build_datasheet(die="D", cfg="L13", die_doc=die_doc,
                                      cfg_doc=cfg_doc, coupled=None))
    assert not any(k.endswith("at 20 °C (rpm/V)") for k in without)


# ---------------------------------------------------------------------------
# the standalone route — for a duty that already converged
# ---------------------------------------------------------------------------

def test_the_route_makes_one_cold_pass_and_nothing_else(client, monkeypatch):
    seen = _fake(monkeypatch, summary=COLD_SUMMARY)
    r = client.post("/api/coupled/constants_20c",
                    json={**LOOP_BODY, "record": False})
    assert r.status_code == 200, r.text[:600]
    d = r.json()
    assert d["ok"] is True
    assert seen["pairs"] == [(20.0, 20.0)]        # ONE run, and it is the cold one
    assert seen["bg"] == [True]
    assert d["constants_20c"]["kt_line_Nm_per_A"] == pytest.approx(0.09,
                                                                   abs=1e-9)
    # `record: false` files nothing, here as everywhere.
    assert d["written_to_last"] is False


def test_the_route_refuses_by_name_when_the_machine_will_not_solve_cold(
        client, monkeypatch):
    _fake(monkeypatch, summary=COLD_SUMMARY, cold_raises=True)
    r = client.post("/api/coupled/constants_20c",
                    json={**LOOP_BODY, "record": False})
    assert r.status_code == 422
    assert r.json()["detail"]["error_code"] == "cold_constants_unsolved"


# ---------------------------------------------------------------------------
# the no-load probe, walked — the one number that is not a direct reading
# ---------------------------------------------------------------------------
# Measured on the L13 rated duty (2026-09-18): the cold pass moved Kt by 5.3 %
# and R by 51 % and left KV at 53.26 rpm/V, bit for bit — because
# `simulation.noload_psi_pm` takes no run temperature and reads the magnet
# CARD.  A "KV at 20 °C" that is really a KV at the card's 120 °C is exactly
# the wrong number to put in a catalogue line.

def test_the_no_load_kv_is_walked_to_20_degrees_on_the_cards_own_coefficient(
        client, monkeypatch):
    from motor_ai_sim.routes import coupled as cp

    _fake(monkeypatch, summary={**COLD_SUMMARY,
                                "demag": {"magnet_name": "N52UH_150C"}})
    # The card's own rule, asked directly, so this test pins the WIRING and
    # never a second opinion about Br(T).
    from motor_ai_sim.report import kv_at_magnet_temp
    want, why = kv_at_magnet_temp(50.0, "N52UH_150C", cp.COLD_CONSTANTS_C)
    assert want is not None and why, "the fixture's grade carries no dBr/dT"

    k = _run(client)["constants_20c"]
    assert k["kv_walked"] is True
    assert k["magnet_grade"] == "N52UH_150C"
    assert "20 °C" in k["kv_note"]
    assert k["two_d"]["KV_noload_rpm_per_V_line"] == pytest.approx(want,
                                                                  abs=1e-5)
    # COLDER MAGNETS MEAN MORE FLUX, so the speed per volt goes DOWN — a walk
    # with the sign the other way would read as a weaker magnet.
    assert k["two_d"]["KV_noload_rpm_per_V_line"] < 50.0
    # …and Ψ_PM is the same probe read the other way round, by ONE factor, so
    # the two can never drift apart.
    assert k["two_d"]["psi_pm_Wb"] == pytest.approx(
        0.01 * 50.0 / want, abs=1e-9)


def test_a_card_with_no_coefficient_leaves_the_probe_alone_and_says_so(
        client, monkeypatch):
    """NOTHING INVENTED, again: a KV at a temperature nothing was measured at
    would be worse than a KV whose provenance is stated."""
    _fake(monkeypatch, summary=COLD_SUMMARY)          # no `demag` block at all
    k = _run(client)["constants_20c"]
    assert k["kv_walked"] is False
    assert k["two_d"]["KV_noload_rpm_per_V_line"] == pytest.approx(50.0)
    assert "card" in k["kv_note"]

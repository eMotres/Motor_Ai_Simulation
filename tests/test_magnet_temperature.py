"""The magnet temperature model — phase 1 of the EM-thermal coupling.

Until 2026-09-08 a magnet was a card quoted at ONE temperature and the solver
used it exactly as written; ``temperature_c`` was metadata.  A coupled loop
needs Br and the demagnetisation knee at whatever temperature the thermal solve
converges to, so every magnet card now carries two reversible coefficients and
``MagnetMaterial.at_temperature`` moves it.

What this file guards, in order of how much it would cost to get wrong:

1. **Nothing moves when nothing is asked.**  ``magnet_temp_c=None`` — every
   caller in the app today — must return the card bit for bit, and the FEM run
   it produces must be the run the app has always produced.  A temperature model
   that quietly shifts today's numbers is worse than no model.
2. **The library agrees with itself.**  Where two cards of one grade exist, the
   model must carry either one onto the other: N52UH 100 → 150 °C and N45EH
   150 → 180 °C.  The N45EH pair is the honest check — its coefficients come
   from the published datasheet slope, re-referenced, not from fitting the pair.
3. **The direction is the physics.**  A hotter NdFeB magnet is weaker AND its
   knee moves toward the operating point.  Fe16N2 is the exception the library
   documents: its coercivity RISES with temperature (+0.4 Oe/K), and the model
   has to reproduce that sign, not average it away.
4. **The request reaches the solve, and the caches know about it.**  A field or
   a loss map solved with a 170 °C magnet must never be served to a request that
   asked for the card's own 120 °C.
"""
from __future__ import annotations

import dataclasses as dc
import math

import pytest

from motor_ai_sim import materials as M
from motor_ai_sim.materials import (MU0, MagnetTemperatureError, all_magnets,
                                    get_magnet, magnet_at)

# Cards of one grade at two temperatures — the only independent check the
# library can make on its own coefficients.  (source, target, Br tol %, knee tol %)
GRADE_PAIRS = [
    ("N52UH_100C", "N52UH_150C", 1.0, 5.0),
    ("N52UH_150C", "N52UH_100C", 1.0, 5.0),
    ("N45EH_150C", "N45EH_180C", 1.0, 5.0),
    ("N45EH_180C", "N45EH_150C", 1.0, 5.0),
]

# The sintered-NdFeB cards: Br and |H_knee| both fall as they heat up.
NDFEB = ["N52UH_100C", "N52UH_150C", "N45EH_150C", "N45EH_180C",
         "F45SH_120C", "F52SH_30C", "F52SH_80C", "F52SH_120C"]

# Iron nitride: Br falls like everything else, but Hcj RISES with temperature.
FE16N2 = ["Fe16N2_optimistic", "Fe16N2_lab_best"]


def _pct(got: float, want: float) -> float:
    return abs(got - want) / abs(want) * 100.0


# ── (a) at the card's own temperature, nothing happens ──────────────────────

@pytest.mark.parametrize("name", sorted(all_magnets()))
def test_at_reference_temperature_returns_an_identical_copy(name):
    """dT = 0 must be a no-op, field for field.

    This is the invariant the whole "today's numbers do not move" promise rests
    on: the coupled loop's first iteration asks for the card's own temperature,
    and if the round trip through J = B - mu0*H moved the last bit of a curve
    point, every downstream cache key that hashes a solved result would shift
    for no physical reason.  Short-circuited in `at_temperature` for exactly
    this reason, and asserted here so it stays short-circuited.
    """
    card = get_magnet(name)
    same = card.at_temperature(card.temperature_c)
    assert same is not card, "at_temperature must return a COPY, never self"
    assert dc.asdict(same) == dc.asdict(card), (
        f"{name}: at_temperature(T_ref) changed the card")
    # Spelled out, because these four are what the solver actually reads.
    assert same.Br == card.Br
    assert same.Hc == card.Hc
    assert same.mu_rec == card.mu_rec
    assert list(same.bh_curve) == list(card.bh_curve)


@pytest.mark.parametrize("name", sorted(all_magnets()))
def test_no_temperature_requested_returns_the_card_as_quoted(name):
    """`magnet_temp_c=None` is the path every existing caller is on."""
    card = get_magnet(name)
    assert dc.asdict(card.at_temperature(None)) == dc.asdict(card)
    assert dc.asdict(magnet_at(name, None)) == dc.asdict(card)


@pytest.mark.parametrize("name", sorted(all_magnets()))
def test_every_magnet_card_carries_the_temperature_model(name):
    """A card the model cannot move is a card a coupled run will fail on.

    Checked at the LIBRARY level rather than inside `at_temperature`, so a card
    added without the two coefficients is caught here — at a name and a line
    number — instead of six months later inside somebody's thermal sweep.
    """
    card = get_magnet(name)
    assert card.temperature_c is not None, f"{name}: no reference temperature"
    assert card.alpha_br_pct_per_k is not None, f"{name}: no alpha_br_pct_per_k"
    assert card.beta_hcj_pct_per_k is not None, f"{name}: no beta_hcj_pct_per_k"
    assert card.alpha_br_pct_per_k < 0.0, (
        f"{name}: Br rises with temperature? alpha must be negative")
    assert abs(card.alpha_br_pct_per_k) < 1.0, (
        f"{name}: alpha {card.alpha_br_pct_per_k} %/K is out of any physical "
        f"range — check whether it was entered as a fraction instead of %/K")


# ── (b) + (d) the library's own grade pairs must reproduce each other ───────

@pytest.mark.parametrize("src,dst,br_tol,knee_tol", GRADE_PAIRS)
def test_one_card_of_a_grade_reproduces_the_other(src, dst, br_tol, knee_tol):
    """Two cards of one grade are ONE material measured twice.

    The coefficients are what says so.  If this fails, either a card's Br /
    curve was edited without its pair, or the coefficients are referenced to the
    wrong temperature — the mistake the YAML section header warns about at
    length (a datasheet's 20 °C slope applied at a 150 °C reference puts
    N45EH's Hcj(180 °C) at 861 kA/m against the library's own 672).
    """
    a, b = get_magnet(src), get_magnet(dst)
    hot = a.at_temperature(b.temperature_c)

    d_br = _pct(hot.Br, b.Br)
    assert d_br <= br_tol, (
        f"{src} -> {b.temperature_c:g} degC gives Br {hot.Br:.4f} T, but "
        f"{dst} says {b.Br:.4f} T ({d_br:.2f} % > {br_tol} %)")

    d_knee = _pct(hot.h_knee, b.h_knee)
    assert d_knee <= knee_tol, (
        f"{src} -> {b.temperature_c:g} degC gives a knee of "
        f"{hot.h_knee * 1e-3:.1f} kA/m, but {dst} says "
        f"{b.h_knee * 1e-3:.1f} kA/m ({d_knee:.2f} % > {knee_tol} %)")


def test_the_measured_consistency_numbers_are_the_documented_ones():
    """The two figures the YAML header quotes, pinned.

    The header is what an engineer reads before trusting a rescaled magnet, so
    the numbers in it have to be the numbers the code produces.  Tight bounds on
    purpose: this is a pin, not a tolerance.
    """
    n52 = get_magnet("N52UH_100C").at_temperature(150.0)
    assert n52.Br == pytest.approx(1.2200, abs=5e-4)
    assert n52.h_knee == pytest.approx(-730_000.0, rel=1e-4)

    n45 = get_magnet("N45EH_150C").at_temperature(180.0)
    assert n45.Br == pytest.approx(1.1014, abs=5e-4)
    assert n45.h_knee == pytest.approx(-606_400.0, rel=2e-3)


# ── (c) direction ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", NDFEB)
def test_ndfeb_weakens_and_its_knee_closes_in_as_it_heats(name):
    """Br down, |H_knee| down — monotonically, over the grade's own range.

    Both halves matter and they matter for different reasons: falling Br is the
    torque the machine loses, while a falling |H_knee| is the margin it loses.
    A model that got only the first right would report a hot magnet as weaker
    and just as safe, which is the opposite of why magnets fail.
    """
    card = get_magnet(name)
    t0 = float(card.temperature_c)
    ladder = [t0 + k * 10.0 for k in range(0, 6)]
    brs = [card.at_temperature(t).Br for t in ladder]
    knees = [abs(card.at_temperature(t).h_knee) for t in ladder]
    for i in range(1, len(ladder)):
        assert brs[i] < brs[i - 1], (
            f"{name}: Br did not fall from {ladder[i-1]:g} to {ladder[i]:g} degC")
        assert knees[i] < knees[i - 1], (
            f"{name}: |H_knee| did not fall from {ladder[i-1]:g} to "
            f"{ladder[i]:g} degC ({knees[i-1]:.0f} -> {knees[i]:.0f} A/m)")
    # …and cooling goes the other way, or the model is not reversible.
    cold = card.at_temperature(t0 - 20.0)
    assert cold.Br > card.Br
    assert abs(cold.h_knee) > abs(card.h_knee)


@pytest.mark.parametrize("name", FE16N2)
def test_fe16n2_coercivity_rises_with_temperature(name):
    """The one card family whose knee moves the OTHER way.

    +0.4 Oe/K measured over 27-152 °C (Acta Mater. 2020) — two orders of
    magnitude smaller than NdFeB's -81.9 Oe/K, and positive.  It is the whole
    argument for the material in a hot machine, so a model that quietly gave it
    NdFeB's sign would erase the reason it is in the library.
    """
    card = get_magnet(name)
    hot = card.at_temperature(float(card.temperature_c) + 100.0)
    assert card.beta_hcj_pct_per_k > 0.0
    assert abs(hot.h_knee) > abs(card.h_knee)
    assert hot.Br < card.Br, "Br still falls with temperature, even here"


# ── the transform itself ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(all_magnets()))
def test_the_scaled_curve_keeps_its_shape_and_its_conventions(name):
    """H strictly ascending, B ascending with it, and the knee still readable.

    `fem_solver_2d` and `simulation/demag.py` both read the knee as
    ``bh_curve[1][0]`` under the "point[0] below zero" rule.  A scaled curve
    that broke the ordering, or moved the B = 0 crossing off index 1 on a card
    that had it there, would not fail loudly — it would hand the solver a
    plausible wrong knee.
    """
    card = get_magnet(name)
    src = list(card.bh_curve)
    if len(src) < 2:
        pytest.skip(f"{name} has no 2nd-quadrant curve")
    follows = src[0][1] <= 0.0 and abs(src[1][1]) <= M._B_ZERO_TOL

    for dt in (-20.0, +15.0, +40.0):
        hot = card.at_temperature(float(card.temperature_c) + dt)
        pts = list(hot.bh_curve)
        hs = [p[0] for p in pts]
        assert hs == sorted(hs), f"{name} at dT={dt:+g}: H is not ascending"
        assert len(set(hs)) == len(hs), f"{name} at dT={dt:+g}: duplicate H"
        assert pts[0][1] <= 0.0, f"{name} at dT={dt:+g}: point[0] is above B=0"
        assert pts[-1][0] == pytest.approx(0.0, abs=1e-9)
        # The curve's H = 0 point and the scalar Br must keep the SAME relation
        # the card states.  Not equality: two cards disagree with themselves in
        # the 4th digit (N45EH_150C's curve ends at 1.1482 while its Br says
        # 1.148), and the model's job is to carry a card faithfully, not to
        # quietly clean it up.
        assert (pts[-1][1] / hot.Br) == pytest.approx(src[-1][1] / card.Br,
                                                      rel=1e-9), (
            f"{name} at dT={dt:+g}: the curve's H=0 point drifted away from Br "
            f"(card {src[-1][1]:.4f}/{card.Br:.4f}, scaled "
            f"{pts[-1][1]:.4f}/{hot.Br:.4f})")
        if follows:
            assert abs(pts[1][1]) <= 1e-9, (
                f"{name} at dT={dt:+g}: point[1] is no longer the B=0 crossing, "
                f"so the solver's H_knee rule now reads B={pts[1][1]:.4f} T")


def test_the_intrinsic_polarisation_is_what_gets_scaled():
    """The transform is on J = B - mu0*H, not on B.

    Scaling B directly would slide the remanence and the knee by the same
    factor, which is the one thing every datasheet says does not happen: Br
    falls at ~0.12 %/K while Hcj falls several times faster.  Checked against
    the algebra rather than against another number, so this test still means
    something if the coefficients are ever re-derived.
    """
    card = get_magnet("N45EH_150C")
    dt = 25.0
    a = 1.0 + card.alpha_br_pct_per_k / 100.0 * dt
    b = 1.0 + card.beta_hcj_pct_per_k / 100.0 * dt
    hot = card.at_temperature(float(card.temperature_c) + dt)
    # point[0] is never re-derived (only the B=0 crossing is), so it carries the
    # raw transform and can be checked point-blank.
    h0, b0 = card.bh_curve[0]
    j0 = b0 - MU0 * h0
    assert hot.bh_curve[0][0] == pytest.approx(h0 * b, rel=1e-12)
    assert hot.bh_curve[0][1] == pytest.approx(j0 * a + MU0 * h0 * b, rel=1e-9)


def test_hc_follows_the_card_s_own_identity():
    """Hc = Br/(mu0*mu_rec) where the card obeys it; scaled with Br where not.

    Every NdFeB card here defines Hc that way (to 0.03 %), and the FEM magnet
    source is built from it, so the identity has to survive a temperature
    change.  The Fe16N2 cards do NOT: their Hc is read off the curve's B = 0
    crossing and is a third of Br/(mu0*mu_rec), so recomputing it from the
    identity would triple a number that means something else.
    """
    nd = get_magnet("N52UH_150C").at_temperature(170.0)
    assert nd.Hc == pytest.approx(nd.Br / (MU0 * nd.mu_rec), rel=1e-9)
    assert nd.mu_rec == get_magnet("N52UH_150C").mu_rec, "mu_rec must not scale"

    fe0 = get_magnet("Fe16N2_optimistic")
    fe = fe0.at_temperature(120.0)
    assert fe.Hc == pytest.approx(fe0.Hc * (fe.Br / fe0.Br), rel=1e-9)


def test_a_second_call_lands_where_one_call_would():
    """T_ref -> T1 -> T2 must equal T_ref -> T2.

    The coefficients are referenced to the card's own temperature, so a scaled
    copy has to carry RE-REFERENCED ones or a coupled loop — which rescales the
    magnet every iteration — would walk away from the line it started on.
    """
    card = get_magnet("N52UH_150C")
    one = card.at_temperature(185.0)
    two = card.at_temperature(160.0).at_temperature(185.0)
    assert two.Br == pytest.approx(one.Br, rel=1e-12)
    assert two.h_knee == pytest.approx(one.h_knee, rel=1e-9)


# ── refusals: a request the card cannot answer must say so ─────────────────

def test_a_card_without_coefficients_refuses_instead_of_guessing():
    card = dc.replace(get_magnet("N52UH_150C"),
                      alpha_br_pct_per_k=None, beta_hcj_pct_per_k=None)
    # Its own temperature is still fine — nothing has to be corrected.
    assert card.at_temperature(150.0).Br == card.Br
    with pytest.raises(MagnetTemperatureError) as e:
        card.at_temperature(163.0)
    assert "alpha_br_pct_per_k" in str(e.value)


def test_a_card_without_a_reference_temperature_refuses():
    card = dc.replace(get_magnet("N52UH_150C"), temperature_c=None)
    with pytest.raises(MagnetTemperatureError):
        card.at_temperature(163.0)


def test_a_temperature_past_total_demagnetisation_refuses():
    """Beyond Br = 0 or Hcj = 0 the linear model has left physics behind.

    Returning a negative magnet would be a solve that runs and answers a
    question nobody asked — the failure mode this project refuses everywhere
    else (UnknownMaterialError, the F6 fallback).
    """
    card = get_magnet("F45SH_120C")     # beta -1.0 %/K -> Hcj = 0 at 220 degC
    with pytest.raises(MagnetTemperatureError) as e:
        card.at_temperature(400.0)
    assert "Hcj" in str(e.value) or "Br" in str(e.value)


def test_a_non_finite_temperature_refuses():
    with pytest.raises(MagnetTemperatureError):
        get_magnet("N52UH_150C").at_temperature(float("nan"))


# ── (f) the request reaches the keys ───────────────────────────────────────

def _snap_kw(**extra):
    kw = dict(gamma_deg=0.0, I_phase_rms=60.0, mesh_size_mm=1.4,
              min_size_mm=0.35, outer_air_factor=1.3, n_sectors=2,
              stator_fillet_mm=0.0, gap_layers=1.0, coil_temp_c=120.0,
              comp_mesh={}, pole_copy=False, iron_template=True, geo_mesh=True,
              structured_gap=True, airgap_macro=False, n_steps_per_period=12,
              n_periods=1.0, eddy=False, rotor_eddy=False, demag=False,
              drive="current", element_order=2, cfg_fingerprint="fp",
              geo_ov=None, mat_ov=None)
    kw.update(extra)
    return kw


def test_no_magnet_temperature_leaves_the_snapshot_key_byte_identical():
    """Every snapshot already on disk has to keep matching.

    The field views look a run's snapshot up by an exact tuple, including after
    a restart from the persisted copy.  Appending a field unconditionally would
    have orphaned every one of them at once — "No matching simulation run"
    right after a run that matched, which this codebase has already paid for
    three times.  So the field is appended only when it carries information,
    the same rule the battery block follows.
    """
    from motor_ai_sim.routes.simulation import _field_snap_key_fields as F
    base = F(**_snap_kw())
    none = F(**_snap_kw(magnet_temp_c=None))
    assert tuple(base.values()) == tuple(none.values())
    assert "magnet_temp_c" not in base


def test_a_magnet_temperature_makes_a_different_snapshot_key():
    from motor_ai_sim.routes.simulation import _field_snap_key_fields as F
    hot = F(**_snap_kw(magnet_temp_c=170.0))
    assert hot["magnet_temp_c"] == 170.0
    assert tuple(hot.values()) != tuple(F(**_snap_kw()).values())
    assert tuple(hot.values()) != tuple(F(**_snap_kw(magnet_temp_c=120.0)).values())


def test_the_field_view_cache_key_separates_two_magnet_temperatures():
    """`_field2d_cache_key` swallows unknown kwargs — this one must not be."""
    from motor_ai_sim.routes.simulation import _field2d_cache_key as K
    assert K() == K(magnet_temp_c=None)
    assert K(magnet_temp_c=170.0) != K()
    assert K(magnet_temp_c=170.0) != K(magnet_temp_c=120.0)


def test_the_thermal_physics_identity_carries_the_magnet_temperature():
    """A loss map is only reusable if the magnet was at the same temperature.

    The magnet's Br and its knee both move with it, so the air-gap field, the
    iron loss and the magnet eddy loss are all different numbers — exactly the
    reuse a coupled thermal run must not be allowed to make.
    """
    from motor_ai_sim.routes.thermal import _PHYSICS_ID_FIELDS, _physics_identity
    assert "magnet_temp_c" in _PHYSICS_ID_FIELDS
    from motor_ai_sim.routes.simulation import _field_snap_key_fields as F
    cold = F(**_snap_kw())
    same = F(**_snap_kw(magnet_temp_c=None))
    hot = F(**_snap_kw(magnet_temp_c=170.0))
    assert _physics_identity(cold) == _physics_identity(same)
    assert _physics_identity(cold) != _physics_identity(hot)


def test_the_solver_entry_points_all_take_the_field():
    """Plumbed end to end, or the route accepts a temperature nothing applies."""
    import inspect
    from motor_ai_sim.simulation import fem_solver_2d as FS
    from motor_ai_sim.optimization import refine_proc as RP
    from motor_ai_sim.routes import optimization as RO
    from motor_ai_sim.routes import simulation as RS
    for fn in (FS.build_materials, FS.fem_transient_sliding_band,
               FS.em_transient_eval, FS.fem_field2d, FS._field2d_static_inputs,
               RP.run_one, RO._subprocess_eval,
               RS.get_fem_transient, RS.get_fem_field2d):
        assert "magnet_temp_c" in inspect.signature(fn).parameters, (
            f"{fn.__module__}.{fn.__name__} does not take magnet_temp_c")
        assert inspect.signature(fn).parameters["magnet_temp_c"].default is None


# ── (e) the FEM path ───────────────────────────────────────────────────────

@pytest.mark.slow
def test_the_solve_uses_the_magnet_temperature_and_only_when_asked():
    """One real sliding-band solve, three ways.

    * ``magnet_temp_c=None`` and ``magnet_temp_c=T_ref`` must return the SAME
      torque.  They are the same magnet — the first because nothing is
      corrected, the second because dT = 0 short-circuits — so anything but
      equality here means the temperature path perturbs a machine it was told
      not to touch, and every number in the app moves the day this ships.
    * ``T_ref + 50 K`` must return LESS torque.  F45SH_120C at 170 °C keeps
      93.3 % of its Br (alpha -0.1344 %/K), and this spoke machine turns that
      into a few percent of torque — small, unambiguous, and in the only
      direction physics allows.

    12s/14p, 4 frames over a sixth of a period, d-axis pinned at the calibrated
    60.000° for this topology so the run costs one mesh and four solves rather
    than a 24-frame calibration.
    """
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
    from tests.test_physics_regression import (CONNECTION, GEO_30MM, MAGNET,
                                               OVERRIDE, RPM)

    t_ref = float(get_magnet(MAGNET).temperature_c)
    kw = dict(n_steps_per_period=4, n_periods=1.0 / 6.0, mesh_size_mm=1.4,
              min_size_mm=0.35, gap_layers=1.0, n_sectors=2,
              structured_gap=True, iron_template=True, geo_mesh=True,
              coil_temp_c=120.0, rotor_eddy=False, element_order=2,
              demag=False, I_phase_rms=60.0, gamma_deg=0.0,
              # 12s/14p: theta* = 30/7 mech => 60.000 deg, measured on three
              # cross-sections (see DAXIS_SHIFT_DEG in fem_solver_2d).  Pinned
              # so the test does not pay for the no-load calibration sweep.
              daxis_deg=60.0)

    def _solve(magnet_temp_c):
        set_request_materials(OVERRIDE)
        try:
            return fem_transient_sliding_band(
                geo_override=dict(GEO_30MM), rpm=RPM, connection=CONNECTION,
                magnet_temp_c=magnet_temp_c, **kw)
        finally:
            set_request_materials(None)

    t_none = float(_solve(None)["T_avg_Nm"])
    t_at_ref = float(_solve(t_ref)["T_avg_Nm"])
    t_hot = float(_solve(t_ref + 50.0)["T_avg_Nm"])

    assert abs(t_none) > 1e-6, "the fixture solved no torque at all"
    # The two solves are handed a bit-identical material map, so the only thing
    # between them is solver jitter; the effect being looked for below is four
    # orders of magnitude bigger than this bound.
    assert abs(t_at_ref - t_none) <= 1e-7 * abs(t_none), (
        f"asking for the card's own {t_ref:g} degC changed the answer: "
        f"{t_none:.9f} -> {t_at_ref:.9f} N*m")
    assert t_hot < t_none, (
        f"a magnet 50 K hotter did not lose torque: {t_none:.6f} -> "
        f"{t_hot:.6f} N*m")
    # Sanity on the SIZE: a few percent, not a rounding error and not a
    # collapsed magnet.  Br is down 6.7 %, and this machine's torque is part
    # reluctance, so the loss lands under that.
    drop = (t_none - t_hot) / t_none
    assert 0.005 <= drop <= 0.067, (
        f"torque fell {drop * 100:.2f} % for a 6.7 % Br drop — outside the "
        f"range a partly-reluctance spoke machine can explain")

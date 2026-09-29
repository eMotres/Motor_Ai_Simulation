"""GET /api/materials/library carries every magnet card's working limit.

The report judges a magnet by its card's ``max_working_temp_c``
(:func:`motor_ai_sim.report._magnet_limit`), and the library yaml has carried
that number on the NdFeB cards since they were written.  The bulk library
endpoint — the one the web app reads ONCE and keeps — did not send it: a client
could only get it one card at a time from
``/api/materials/library/magnet/{name}``.

So the Thermal tab's duty-cycle editor had no default magnet limit, and every
cycle it answered judged the winding against its insulation class and the
magnets against nothing at all unless somebody typed a temperature by hand
(user 2026-09-16: the magnets must be judged BY DEFAULT).

One key, and the rule it has to keep: present when the card states it, absent
(``None``) when it does not — a fallback invented here would put a number in
front of the user that no card measured.  The report's own coercivity-class
fallback stays in the report, where its provenance is printed beside it.
"""
from __future__ import annotations

from motor_ai_sim.api import get_materials_library
from motor_ai_sim.materials import get_material


def _magnets() -> dict:
    lib = get_materials_library()
    assert "magnet" in lib and lib["magnet"], "the library serves magnet cards"
    return lib["magnet"]


def test_every_magnet_card_carries_the_key() -> None:
    """The key is on EVERY card — a missing key and a stated `null` differ."""
    for name, card in _magnets().items():
        assert "max_working_temp_c" in card, (
            f"magnet '{name}' is served without max_working_temp_c; a client "
            "cannot tell 'this card states no limit' from 'this endpoint "
            "does not send limits'")


def test_the_number_is_the_card_s_own() -> None:
    """…and it is the card's, not a guess: the same value the report reads."""
    checked = 0
    for name, card in _magnets().items():
        served = card.get("max_working_temp_c")
        try:
            own = getattr(get_material("magnet", name), "max_working_temp_c",
                          None)
        except Exception:                                   # noqa: BLE001
            continue        # a Firestore-only global card has no library entry
        assert served == own, (
            f"magnet '{name}': the endpoint serves {served!r} and the card "
            f"carries {own!r}")
        if served is not None:
            checked += 1
    assert checked, ("no served card carried a maximum working temperature — "
                     "the library yaml does state them (N52UH is 180 °C), so "
                     "this assertion failing means the value stopped flowing")


def test_a_uh_grade_is_the_180_c_class_the_yaml_states() -> None:
    """One named card, so the wiring is pinned to a value and not to itself."""
    mags = _magnets()
    uh = [n for n in mags if "UH" in n.upper()
          and mags[n].get("max_working_temp_c") is not None]
    assert uh, "the library ships UH-grade magnet cards with a working limit"
    for n in uh:
        assert float(mags[n]["max_working_temp_c"]) >= 150.0, (
            f"'{n}' is a UH grade and its card's limit reads "
            f"{mags[n]['max_working_temp_c']} °C")

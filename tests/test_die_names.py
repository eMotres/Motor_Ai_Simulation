"""Short motor names: ``<family base> <user suffix> <n>`` (owner decision 2026-10-26).

Pure naming rule (``motor_ai_sim.die_names``) plus the way the paths that create
a die use it (the automatic die transition, the dialogs' suggestion route).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from motor_ai_sim import die_names as dn

# The real catalog names (config/dies of the workstation) and what their family base is.
REAL = [
    ("100 mm · 24s-28p mid-torque", "100 mm"),
    ("40 mm · 12s-14p high-speed", "40 mm"),
    ("CIANO 150_40", "CIANO 150_40"),
    ("CIANO10 200", "CIANO10 200"),
    ("CIANO10 200 opt", "CIANO10 200"),
    ("CIANO14 30_10", "CIANO14 30_10"),
    ("CIANO14 40 new", "CIANO14 40"),
    ("CIANO14 50 edited", "CIANO14 50"),
    ("CIANO28 150_35", "CIANO28 150_35"),
    ("CIANO28 150_35 new", "CIANO28 150_35"),
    ("CIANO28 85 20RSW175", "CIANO28 85"),
    ("CIANO28 85 20SW1200", "CIANO28 85"),
    ("CILN28", "CILN28"),
    ("M1 850Nm", "M1"),
    # the owner's example, and names the old rules produced
    ("CIANO56 400_80 Ø400 48s56p opt3", "CIANO56 400_80"),
    ("CIANO14 50 edited 12s16p", "CIANO14 50"),
    ("CIANO14 edited Ø60 12s14p", "CIANO14"),
    ("CIANO56 400_80 vadim 2", "CIANO56 400_80"),
]


@pytest.mark.parametrize("name,base", REAL)
def test_family_base_of_the_real_names(name, base):
    assert dn.family_base(name) == base


def test_family_base_edge_cases():
    assert dn.family_base("") == ""
    assert dn.family_base("   ") == ""
    assert dn.family_base("Ø400 48s56p") == "Ø400"          # unknown shape: the first word, never the decorated whole


def test_every_real_name_on_disk_has_a_base_that_starts_it():
    """Whatever dies this checkout can see (config/dies and a sibling / env private checkout)."""
    root = Path(__file__).resolve().parents[1]
    places = [root / "config" / "dies"]
    env = os.environ.get("MOTOR_AI_SIM_PRIVATE_DATA", "").strip()
    if env:
        places.append(Path(env).expanduser() / "config" / "dies")
    places.append(root.parent / "motor-ai-sim-private" / "config" / "dies")
    seen = 0
    for d in places:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if (p / "die.yaml").is_file():
                seen += 1
                base = dn.family_base(p.name)
                assert base and p.name.replace("·", " ").split()[0] == base.split()[0]
    assert seen >= 1


def test_user_suffix_first_name_then_email_local_part():
    assert dn.user_suffix("Vadim Shcherbakov", "vadim@example.com") == "vadim"
    assert dn.user_suffix("", "John.Smith+tag@example.com") == "john.smith"
    assert dn.user_suffix(None, "alice@example.com") == "alice"
    assert dn.user_suffix("Вадим Щербаков", "x@example.com") == "vadim"          # transliterated
    assert dn.user_suffix("José", "x@example.com") == "jose"                       # accents dropped
    assert dn.user_suffix("🙂", "bob@example.com") == "bob"                         # nothing usable -> e-mail
    assert dn.user_suffix(None, None) == "user"
    assert dn.user_suffix("Bartholomew-Maximilian-Alexander", "x@example.com") == "bartholomew-maxi"
    assert len(dn.user_suffix("A" * 80, None)) <= dn.MAX_SUFFIX


def test_variants_get_the_first_free_number():
    taken = set()
    names = []
    for _ in range(4):
        n = dn.suggest("CIANO56 400_80 Ø400 48s56p opt3", "Vadim", "vadim@example.com",
                       lambda x: x in taken)
        taken.add(n)
        names.append(n)
    assert names == ["CIANO56 400_80 vadim", "CIANO56 400_80 vadim 2",
                     "CIANO56 400_80 vadim 3", "CIANO56 400_80 vadim 4"]
    # a hole is filled first
    taken.discard("CIANO56 400_80 vadim 2")
    assert dn.suggest("CIANO56 400_80", "Vadim", None, lambda x: x in taken) == "CIANO56 400_80 vadim 2"
    # a variant of a variant stays in the family
    assert dn.family_base("CIANO56 400_80 vadim 3") == "CIANO56 400_80"


def test_a_source_less_variant_still_has_a_name():
    assert dn.suggest("", "Vadim", None, lambda _x: False) == "Motor vadim"


def test_no_name_is_longer_than_a_die_name_may_be():
    long_base = "CIANO" + "X" * 70
    name = dn.suggest(long_base, "Vadim", None, lambda _x: False)
    assert len(name) <= 64


# ── the paths that create a die ──────────────────────────────────────────────

def test_transition_name_is_base_plus_suffix(monkeypatch):
    from motor_ai_sim.routes import family as fam
    monkeypatch.setattr(fam, "_acting_account", lambda *_a, **_k: ("Vadim", "vadim@example.com"))
    geo = {"stator_diameter": 50}
    # the topology and the diameter are NOT in the name any more
    assert fam._die_transition_name("CIANO14 50 edited", geo, {"stator_diameter": 50,
            "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 8},
            [{"key": "num_poles_per_segment"}]) == "CIANO14 50 vadim"
    assert fam._die_transition_name("CIANO14 50 edited", geo, {"stator_diameter": 60,
            "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7},
            [{"key": "stator_diameter"}]) == "CIANO14 50 vadim"

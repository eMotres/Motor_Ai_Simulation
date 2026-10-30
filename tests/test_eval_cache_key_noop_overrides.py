"""The sweep/optimizer eval cache must key on the EFFECTIVE machine, not on
which parameters a request happened to name.

User 2026-09-06: "I ran a sweep and added one more parameter (bridge
thickness) — why didn't it plot the previous measurements?"  The new
sweep named rotor_house_height at its base value on a third of its points;
that extra name alone made every key miss the previous sweep's entries for
the very same machines.  `_override_is_noop` drops such overrides from the key."""
from motor_ai_sim.config import get_config
from motor_ai_sim.routes import optimization as O


def _key(ov):
    return O._eval_cache_key(ov, 100.0, 12, 120.0, 1.0, 0.0, 4.0, 0.3, 2, False, False,
                             "fp", 1.0, 0.0, False, False, True, False, True, True, 2, False)


def test_base_valued_overrides_do_not_change_the_key():
    g = get_config()["geometry"]
    ag, mh, rh = float(g["air_gap"]), float(g["magnet_height"]), float(g["rotor_house_height"])
    assert _key({}) == _key({"air_gap": ag}) == _key({"air_gap": ag, "magnet_height": mh,
                                                      "rotor_house_height": rh})


def test_real_overrides_still_separate_and_naming_does_not():
    g = get_config()["geometry"]
    mh, rh = float(g["magnet_height"]), float(g["rotor_house_height"])
    ag2 = float(g["air_gap"]) + 0.5
    same_machine_a = _key({"air_gap": ag2, "magnet_height": mh})
    same_machine_b = _key({"air_gap": ag2, "magnet_height": mh, "rotor_house_height": rh})
    other_machine = _key({"air_gap": ag2, "magnet_height": mh, "rotor_house_height": rh + 3.0})
    assert same_machine_a == same_machine_b
    assert same_machine_a != other_machine
    assert _key({}) != same_machine_a


def test_unknown_or_non_numeric_keys_are_never_noops():
    assert O._override_is_noop("no_such_parameter", 1.0) is False
    assert O._override_is_noop("air_gap", "abc") is False


def test_a_swept_key_at_its_base_value_still_names_its_value(monkeypatch):
    """Server 2026-10-30 (M1-L220-R3): a 17-turn scan, then a 16-turn scan of the
    same machine, each on a config whose base was its own count — the six 1400
    rpm points of the second were the first's, to the digit (104.509 N·m,
    425.88 V…).  The scan's fingerprint EXCLUDES the swept keys, so dropping a
    base-valued override left the turns in neither half of the key."""
    base = dict(get_config()["geometry"])

    def key_on(turns, excl):
        geo = dict(base, num_wires_per_slot=turns)
        monkeypatch.setattr("motor_ai_sim.config.get_config",
                            lambda: {"geometry": geo})
        # the excluded fingerprint is the same string for both machines: the
        # only key in which they differ is the excluded one
        return O._eval_cache_key({"num_wires_per_slot": turns}, 30.0, 12, 150.0,
                                 1.0, 5.0, 4.0, 0.3, 2, False, False, "fp-excl",
                                 1.0, 0.0, False, False, True, False, True, True, 2,
                                 False, fp_excludes=excl)

    assert (key_on(17, ("num_wires_per_slot",))
            != key_on(16, ("num_wires_per_slot",)))
    # a fingerprint that DOES carry the base keeps the 2026-09-06 rule: the
    # base-valued name drops out (the fingerprint already tells them apart)
    assert key_on(17, ()) == key_on(16, ())

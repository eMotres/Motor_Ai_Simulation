"""The sweep/optimizer eval cache must key on the EFFECTIVE machine, not on
which parameters a request happened to name.

User 2026-09-06: "запустил sweep и добавил ещё один параметр (толщину
перемычки) — почему он не вывел предыдущие измерения на график?"  The new
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

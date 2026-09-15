"""A die lock must not make its own configurations unloadable.

User 2026-09-11: *"откуда здесь взялось wire split 2?"* — the Ø200 die still
carries the old 4.5 x 1 mm strip winding, every L180 configuration overrides it
with 9 x 0.5 mm, and the die had just been locked.  Loading the configuration
then failed key by key with 423: `geometry_lock_check` read the DIE's value as
the only value a locked key may hold and never looked at the configuration's
own override.  The live machine kept the die's wire, the panel showed a winding
nobody had asked for, and the report of that configuration dropped every stored
result as "another machine".
"""
import motor_ai_sim.routes.family as F


def _ctx(monkeypatch, die, cfg):
    monkeypatch.setattr(F, "_read_ctx", lambda: {"die": die, "config": cfg})


def _docs(monkeypatch, die_geo, ov, die_locked=True, cfg_locked=False):
    def _load(path, what):
        return ({"geometry": die_geo, "locked": die_locked} if what == "die"
                else {"geometry_overrides": ov, "locked": cfg_locked})
    monkeypatch.setattr(F, "_load_yaml", _load)
    monkeypatch.setattr(F, "_die_file", lambda d: d)
    monkeypatch.setattr(F, "_cfg_file", lambda d, c: (d, c))


DIE = {"wire_width": 4.5, "wire_height": 1.0, "num_wires_per_slot": 15,
       "wire_split": 2, "wire_parallel": 3}
OV = {"wire_width": 9.0, "wire_height": 0.5, "num_wires_per_slot": 24,
      "wire_split": 1, "wire_parallel": 4}


def test_a_locked_die_still_loads_its_own_configuration(monkeypatch):
    """Every key of the configuration's stored build is accepted."""
    _ctx(monkeypatch, "D", "C")
    _docs(monkeypatch, DIE, OV)
    assert F.geometry_lock_check(dict(OV)) is None


def test_b_a_hand_typed_value_is_still_refused(monkeypatch):
    """The lock still does its job: a third value is neither the die's nor the
    configuration's."""
    _ctx(monkeypatch, "D", "C")
    _docs(monkeypatch, DIE, OV)
    bad = F.geometry_lock_check({"wire_width": 7.25})
    assert bad and bad["invalid_parameters"][0]["field"] == "wire_width"
    assert bad["invalid_parameters"][0]["locked_value"] == 9.0,         "the refusal must name the value this configuration IS, not the die's"


def test_c_the_die_value_is_the_canon_when_the_configuration_is_silent(monkeypatch):
    """A key the configuration does not override still answers to the die."""
    _ctx(monkeypatch, "D", "C")
    _docs(monkeypatch, DIE, {k: v for k, v in OV.items() if k != "wire_split"})
    assert F.geometry_lock_check({"wire_split": 2}) is None
    assert F.geometry_lock_check({"wire_split": 1}) is not None


def test_d_free_keys_move_under_a_die_lock(monkeypatch):
    """Stack length, wire height and turns stay editable — that is what a die
    lock (as opposed to a configuration lock) means."""
    _ctx(monkeypatch, "D", "C")
    _docs(monkeypatch, DIE, OV)
    assert F.geometry_lock_check({"num_wires_per_slot": 31}) is None


def test_e_wire_parallel_is_free_under_a_die_lock(monkeypatch):
    """Strands in hand are an electrical choice, not a shape.

    User 2026-09-11: *"нужно дать ещё возможность менять wire parallel
    (strands) — это никак не затрагивает геометрию"*, and the code agrees:
    `wire_parallel` appears nowhere in `cadquery_geometry`, only in the winding
    maths (turns = conductors / strands).  `wire_split` is the opposite case —
    N narrower strips with spacing, and the slot grows around them — so it
    stays locked.
    """
    _ctx(monkeypatch, "D", "C")
    _docs(monkeypatch, DIE, OV)
    assert "wire_parallel" in F.EDITABLE_UNDER_DIE_LOCK
    assert F.geometry_lock_check({"wire_parallel": 2}) is None,         "a die lock must not freeze the strands in hand"
    bad = F.geometry_lock_check({"wire_split": 3})
    assert bad and bad["invalid_parameters"][0]["field"] == "wire_split",         "a split wire IS geometry and stays locked"


def test_f_a_configuration_lock_freezes_the_free_keys_too(monkeypatch):
    """Locking the CONFIGURATION is the stronger statement: even the winding's
    electrical knobs answer to its stored build."""
    _ctx(monkeypatch, "D", "C")
    _docs(monkeypatch, DIE, OV, cfg_locked=True)
    assert F.geometry_lock_check({"wire_parallel": OV["wire_parallel"]}) is None
    assert F.geometry_lock_check({"wire_parallel": 2}) is not None

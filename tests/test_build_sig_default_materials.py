"""ABSENT IS NOT CHANGED for stored results (owner 2026-11-11, phone screenshot of a catalog motor on "⚠ rated / ⚠ peak").

On 2026-10-30/31 the catalog files that never named the slot liner, the wire enamel or the conductor got them written in -
the very values a load had always filled for them (``family._ABSENT_MATERIALS``: Nomex, polyimide, copper).  The build did
not change, its description did, and the plain hash flagged every stored duty "computed on an older build".

Pinned here:

* ``_build_sig`` (the STAMP a new result is written with) is unchanged;
* ``_build_sigs`` names the same build in all its spellings, so a stamp taken before the defaults were written matches;
* a material that CONTRADICTS the default (Al2O3 for the liner, a magnet change) still moves the build;
* ``build_sig_moved``: unknown on either side is not "moved"; the tree's run rows and the cogging view judge with it.

Pure functions on dicts - no catalog files, no FEM.
"""
from __future__ import annotations

from motor_ai_sim import cogging
from motor_ai_sim.routes import family as fam

DIE = {"geometry": {"stator_diameter": 40.0, "num_slots": 12}}
OLD = {"geometry_overrides": {"motor_length": 12.0}, "winding": {"connection": "1S"},
       "materials": {"magnet": "F52SH_150C", "stator_core": "20SW1200", "rotor_core": "20SW1200"}}
NAMED = {**OLD, "materials": {**OLD["materials"], "slot": "copper", "slot_insulation": "Nomex",
                              "wire_insulation": "polyimide"}}


def test_stamp_itself_is_unchanged():
    # the stamp a new result gets is the plain hash of the file, as before
    assert fam._build_sig(DIE, OLD) == fam._build_sigs(DIE, OLD)[0]
    assert fam._build_sig(DIE, NAMED) == fam._build_sigs(DIE, NAMED)[0]
    assert fam._build_sig(DIE, OLD) != fam._build_sig(DIE, NAMED)


def test_naming_the_defaults_is_the_same_build():
    before = fam._build_sig(DIE, OLD)                 # results recorded before the defaults were written
    after = fam._build_sig(DIE, NAMED)                # results recorded after
    assert not fam.build_sig_moved(before, fam._build_sigs(DIE, NAMED))
    assert not fam.build_sig_moved(after, fam._build_sigs(DIE, NAMED))
    assert not fam.build_sig_moved(after, fam._build_sigs(DIE, OLD))


def test_a_contradicting_material_still_moves_the_build():
    before = fam._build_sig(DIE, OLD)
    ceramic = {**NAMED, "materials": {**NAMED["materials"], "slot_insulation": "Al2O3"}}
    assert fam.build_sig_moved(before, fam._build_sigs(DIE, ceramic))
    magnet = {**NAMED, "materials": {**NAMED["materials"], "magnet": "N52UH_20C"}}
    assert fam.build_sig_moved(before, fam._build_sigs(DIE, magnet))
    longer = {**NAMED, "geometry_overrides": {"motor_length": 20.0}}
    assert fam.build_sig_moved(before, fam._build_sigs(DIE, longer))


def test_unknown_is_not_moved():
    assert not fam.build_sig_moved(None, fam._build_sigs(DIE, NAMED))
    assert not fam.build_sig_moved("abc", ())
    assert fam.build_sig_moved("abc", "def")          # a bare string still works
    assert not fam.build_sig_moved("abc", "abc")


def test_run_rows_and_cogging_view_use_every_spelling():
    before = fam._build_sig(DIE, OLD)
    entry = {"name": "rated", "runs": {"current": {"build_sig": before, "payload_file": "x.json.gz"}}}
    rows = fam._run_rows(fam._build_sigs(DIE, NAMED), entry)
    assert rows and rows[0]["stale"] is False
    rows = fam._run_rows(fam._build_sig(DIE, NAMED), entry)       # judged against the stamp alone: the old false alarm
    assert rows[0]["stale"] is True
    blk = {"pkpk_Nm": 0.02, "build_sig": before}
    assert cogging.view(blk, current_sig=fam._build_sigs(DIE, NAMED))["stale"] is False
    assert cogging.view(blk, current_sig=fam._build_sig(DIE, NAMED))["stale"] is True
    assert cogging.is_stale({"pkpk_Nm": 1}, fam._build_sigs(DIE, NAMED)) is True    # no stamp = unknown = stale

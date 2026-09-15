"""``POST /api/family/activate`` carries the duty's mesh block to the server.

2026-09-09 morning: the 40 mm L12/rated had been loaded through the API
overnight (activate → PUT geometry → …) and the user's browser picked it up on
boot — but the browser adopts the server's ``mesh:`` config ONCE at boot
(``web/src/lib/meshConfigSync.ts``), and that block still said the Ø200's
"4 sectors, 2.97 mm".  The first Run answered "n_sectors=4 is not a symmetry
of this machine: 12 slots / 14 poles repeat only in 1/2 fractions".  The duty's
own ``mesh`` block (``mesh.nSectors: 2``, ``mesh.meshSize: 1`` …) is the
machine's statement of how it is run, so activation writes it server-side.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from motor_ai_sim.routes import family as fam

DIE = "TESTDIE 40"
CFG = "L12"


def test_the_patch_is_the_five_fidelity_keys_and_nothing_else():
    duty = {"name": "rated", "mesh": {
        "mesh.meshSize": 1, "mesh.minSize": 0.3, "mesh.outerAir": 1.2,
        "mesh.gapLayers": 1, "mesh.nSectors": 2, "mesh.poleCopy": False,
        "sim.stepsPP": 36, "sim.rpm": 13000, "mesh.componentMesh": {}}}
    assert fam.duty_mesh_patch(duty) == {
        "mesh_size_mm": 1.0, "min_size_mm": 0.3, "outer_air_factor": 1.2,
        "gap_layers": 1.0, "n_sectors": 2}
    # blanks, booleans and junk are skipped; a duty with no block is nothing
    assert fam.duty_mesh_patch({"name": "x", "mesh": {"mesh.nSectors": "",
                                                       "mesh.meshSize": "abc",
                                                       "mesh.gapLayers": True}}) == {}
    assert fam.duty_mesh_patch({"name": "x"}) == {}
    assert fam.duty_mesh_patch(None) == {}


@pytest.fixture
def dies(monkeypatch, tmp_path):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": True,
        "geometry": {"stator_diameter": 40.0, "num_seg": 2,
                     "num_slots_per_segment": 6, "num_poles_per_segment": 7},
    }, sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "geometry_overrides": {"motor_length": 12},
        "duties": [{"name": "rated", "mode": "motor", "rpm": 13000.0,
                    "mesh": {"mesh.meshSize": 1, "mesh.nSectors": 2,
                             "mesh.gapLayers": 1, "sim.stepsPP": 36}},
                   {"name": "bare", "mode": "motor", "rpm": 14400.0}],
    }, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    return root


@pytest.fixture
def mesh_config(monkeypatch, tmp_path):
    """A throwaway motor_config.yaml behind ``update_mesh_config``."""
    from motor_ai_sim import api as api_mod
    p = tmp_path / "motor_config.yaml"
    p.write_text(yaml.safe_dump({"geometry": {"stator_diameter": 40.0},
                                 "mesh": {"n_sectors": 4, "mesh_size_mm": 2.97,
                                          "min_size_mm": 0.3}},
                                sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(api_mod, "_CONFIG_PATH", pathlib.Path(p))
    return p


def _mesh(p: pathlib.Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))["mesh"]


def test_activating_a_duty_writes_its_mesh_block_server_side(dies, mesh_config):
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated"), _w={})
    assert out["ok"] is True and out["mesh_synced"] is True
    m = _mesh(mesh_config)
    assert m["n_sectors"] == 2 and m["mesh_size_mm"] == 1.0 and m["gap_layers"] == 1.0
    assert m["min_size_mm"] == 0.3            # untouched keys stay


def test_a_duty_without_a_block_leaves_the_server_mesh_alone(dies, mesh_config):
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="bare"), _w={})
    assert out["ok"] is True and out["mesh_synced"] is False
    assert _mesh(mesh_config)["n_sectors"] == 4
    out2 = fam.activate(fam.Activate(die=DIE, config=CFG, duty=None), _w={})
    assert out2["mesh_synced"] is False


def test_a_mesh_config_that_cannot_be_written_does_not_fail_the_activation(
        dies, mesh_config, monkeypatch):
    from motor_ai_sim import api as api_mod
    monkeypatch.setattr(api_mod, "_CONFIG_PATH", pathlib.Path(mesh_config.parent / "missing" / "x.yaml"))
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated"), _w={})
    assert out["ok"] is True and out["mesh_synced"] is False

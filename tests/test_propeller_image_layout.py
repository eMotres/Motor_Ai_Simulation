"""The production image has NO <app>/config (deploy/Dockerfile.api keeps it as <app>/config-seed): the own-propeller design file and the
airfoil folder must load from the seed.  Live bug 2026-11-15: Propellers > Designer answered HTTP 500."""
import shutil
import tempfile
from pathlib import Path

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_airfoil as af
from motor_ai_sim import propeller_design as pd

REPO = Path(pp.__file__).resolve().parents[2]


def _image_layout() -> Path:
    base = Path(tempfile.mkdtemp(prefix="image-layout-"))
    shutil.copytree(REPO / "config" / "propellers", base / "config-seed" / "propellers")
    return base


def test_image_layout_has_no_config_dir_and_loads_family_and_airfoils():
    base = _image_layout()
    assert not (base / "config").exists()
    design_path = pp.seeded_config_path("propellers", "_designs", "own_family.yaml", base=base)
    airfoil_dir = pp.seeded_config_path("propellers", "_airfoils", base=base)
    assert design_path == base / "config-seed" / "propellers" / "_designs" / "own_family.yaml"
    d = pd.load_design(design_path)
    assert d.id == "own_family" and d.diameters_in
    af.load_library(airfoil_dir)                                   # must not raise


def test_checkout_wins_when_both_exist():
    base = _image_layout()
    shutil.copytree(REPO / "config" / "propellers", base / "config" / "propellers")
    assert pp.seeded_config_path("propellers", base=base) == base / "config" / "propellers"


def test_neither_exists_names_the_usual_place():
    base = Path(tempfile.mkdtemp(prefix="image-layout-"))
    assert pp.seeded_config_path("propellers", base=base) == base / "config" / "propellers"


def test_module_constants_resolve_in_this_checkout():
    assert pd.DESIGN_PATH.is_file()
    assert af.AIRFOIL_DIR.is_dir()

"""Hotfix 2026-11-24: the deployed image has no ``config/``; it keeps the repo's config as a read-only seed at ``config-seed/``
(deploy/Dockerfile.api).  The mfoil tables were looked up at ``<app>/config`` only, so the Designer's mfoil default failed on live.
These tests fail when a propeller data path is resolved only at ``<app>/config``."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_airfoil as af
from motor_ai_sim import propeller_bemt as pb
from motor_ai_sim import propeller_design as pd

ROOT = Path(__file__).resolve().parents[1]


def test_seeded_path_prefers_the_checkout_and_falls_back_to_the_seed(tmp_path):
    (tmp_path / "config-seed" / "propellers" / "_polars_mfoil").mkdir(parents=True)
    assert pp.seeded_config_path("propellers", "_polars_mfoil", base=tmp_path) == tmp_path / "config-seed" / "propellers" / "_polars_mfoil"
    (tmp_path / "config" / "propellers" / "_polars_mfoil").mkdir(parents=True)
    assert pp.seeded_config_path("propellers", "_polars_mfoil", base=tmp_path) == tmp_path / "config" / "propellers" / "_polars_mfoil"


def test_every_propeller_data_path_goes_through_the_seed_helper():
    assert pb.MFOIL_POLAR_DIR == pp.seeded_config_path("propellers", "_polars_mfoil")
    assert pb.POLAR_DIR == pp.seeded_config_path("propellers", "_polars")
    assert af.AIRFOIL_DIR == pp.seeded_config_path("propellers", "_airfoils")
    assert pd.DESIGN_PATH == pp.seeded_config_path("propellers", "_designs", "own_family.yaml")
    # a new checkout-only data path must not appear; the two catalogue roots below read the shared / private stores on live as well
    allowed = {("propeller.py", '_REPO_DIR = Path(__file__).resolve().parents[2] / "config" / "propellers"'),
               ("propeller_s1.py", '_REPO_DIR = Path(__file__).resolve().parents[2] / "config" / "propeller_s1"')}
    pat = re.compile(r'parents\[2\]\s*/\s*"config"')
    found = set()
    for f in sorted((ROOT / "src" / "motor_ai_sim").glob("propeller*.py")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if pat.search(line):
                found.add((f.name, line.strip()))
    assert found <= allowed, "checkout-only config path(s): %s" % sorted(found - allowed)


def test_mfoil_tables_load_in_the_image_layout(tmp_path):
    """The deployed layout (src + config-seed, NO config): the mfoil polar of the shipped design must load and evaluate."""
    app = tmp_path / "app"
    shutil.copytree(ROOT / "src", app / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "config" / "propellers", app / "config-seed" / "propellers")
    assert not (app / "config").exists()
    code = (
        "from motor_ai_sim import propeller_bemt as pb, propeller_design as pd\n"
        "assert 'config-seed' in str(pb.MFOIL_POLAR_DIR), pb.MFOIL_POLAR_DIR\n"
        "d = pd.with_polar_source(pd.load_design(), 'mfoil_table')\n"
        "r = pd.evaluate_candidate(d, pd.seed_vector(d, 10, 2), 'L12')\n"
        "assert r['thrust_N'] > 0\n"
        "print('ok', len(list(pb.MFOIL_POLAR_DIR.glob('*.csv'))))\n")
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("MOTOR_AI_SIM_CONFIG",)}
    env["PYTHONPATH"] = str(app / "src")
    p = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr[-2000:]
    assert p.stdout.strip().startswith("ok 63")

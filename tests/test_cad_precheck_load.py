# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The CAD template pre-check must load in the API image.

The API image carries only the few template files ``deploy/Dockerfile.api`` copies, not the whole ``cad/`` tree.  ``health.py``
gained ``from .common import ...`` and the image kept shipping ``health.py`` alone: every check of a CAD-only edit failed on
the server with ``ImportError: attempted relative import with no known parent package`` while the repository checkout (which
has every file) showed nothing.  These tests load the pre-check from a folder that holds exactly what the image holds.
"""
from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

import pytest

from motor_ai_sim import cad_params as CP

_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE = _ROOT / "cad" / "motor_templates" / "segmented_flatwire"
_PREFIX = "cad/motor_templates/segmented_flatwire/"


def _image_files() -> list[str]:
    """The template files ``deploy/Dockerfile.api`` copies into the API image."""
    text = (_ROOT / "deploy" / "Dockerfile.api").read_text(encoding="utf-8")
    return sorted({m.group(1) for m in re.finditer(r"^COPY\s+" + re.escape(_PREFIX) + r"(\S+)\s", text, re.M)})


def _module_level_siblings(path: Path) -> set[str]:
    """Sibling files a template module imports when it is loaded (relative imports outside any function)."""
    out: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            out.update([node.module.split(".")[0]] if node.module else [a.name for a in node.names])
    return {n + ".py" for n in out}


@pytest.fixture()
def fresh(monkeypatch):
    monkeypatch.setattr(CP, "_PRECHECK", None)
    yield
    CP._PRECHECK = None


def test_image_ships_every_file_the_precheck_imports():
    shipped = set(_image_files())
    assert "health.py" in shipped
    todo, seen = ["health.py"], set()
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        assert f in shipped, f"{f} is imported by the pre-check at load time but deploy/Dockerfile.api does not copy it"
        todo.extend(_module_level_siblings(_TEMPLATE / f))


def test_precheck_loads_from_the_files_of_the_image(tmp_path, monkeypatch, fresh):
    for f in _image_files():
        shutil.copy(_TEMPLATE / f, tmp_path / f)
    monkeypatch.setenv("MOTRES_CAD_TEMPLATE_DIR", str(tmp_path))
    pre = CP.load_precheck()
    assert callable(pre)
    # the function really runs: with no parameter at all it reports the missing name instead of failing on an import
    with pytest.raises(KeyError):
        pre({}.__getitem__)


def test_template_that_does_not_load_is_unavailable_not_a_crash(tmp_path, monkeypatch, fresh):
    shutil.copy(_TEMPLATE / "health.py", tmp_path / "health.py")       # the state of the image before the fix
    monkeypatch.setenv("MOTRES_CAD_TEMPLATE_DIR", str(tmp_path))
    assert CP.load_precheck() is None
    with pytest.raises(CP.CadParamError):
        CP.validate({}, {})


def test_precheck_loads_from_the_repository_template(monkeypatch, fresh):
    monkeypatch.delenv("MOTRES_CAD_TEMPLATE_DIR", raising=False)
    assert callable(CP.load_precheck())

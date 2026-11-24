"""datasheet Recalculate sheet: a stale k(L) table (multi-row, no k_flux_vs_L_method) is not interpolated."""
import json
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parents[1] / "web/src/lib/__tests__/fixtures/live_l12_reference_cards.json"


def _pp(**end3d):
    if not FIX.is_file():
        pytest.skip("web fixture not present in this checkout")
    pp = json.loads(FIX.read_text(encoding="utf-8"))["cat_ciano14_40_new"]["passport"]["passport"]
    pp = dict(pp)
    pp["end3d"] = {**pp["end3d"], **end3d}
    return pp


def _rows(pp):
    from openpyxl import Workbook
    from motor_ai_sim.datasheet import _build_calculator
    wb = Workbook()
    _build_calculator(wb, pp=pp, geo={"num_poles": 14}, wind={}, duties=[])
    return [[c.value for c in r] for ws in wb.worksheets for r in ws.iter_rows()]


def _k_row(rows):
    return next(r for r in rows if r and r[0] == "3-D factor k(L)")


def test_unmarked_table_uses_the_scalar_and_says_so():
    from motor_ai_sim.end3d_factors import STALE_KL_FLAG
    pp = _pp()
    rows = _rows(pp)
    k = _k_row(rows)
    assert k[1] == pytest.approx(pp["end3d"]["k_flux"])           # a number, not the interpolation formula
    assert any(STALE_KL_FLAG in [c for c in r if isinstance(c, str)] for r in rows)


def test_marked_table_is_interpolated_as_before():
    from motor_ai_sim.end3d_factors import STALE_KL_FLAG
    rows = _rows(_pp(k_flux_vs_L_method="own_mid_ratio"))
    k = _k_row(rows)
    assert isinstance(k[1], str) and k[1].startswith("=MIN(")
    assert not any(STALE_KL_FLAG in [c for c in r if isinstance(c, str)] for r in rows)

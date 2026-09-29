"""Round-trip test for the owner-approved (2026-09-25) canonical Fusion
naming: our export -> (simulated) Fusion import -> (simulated) Fusion export
-> our import must reproduce the SAME geometry.

No new backend endpoint or web button was added for this -- the export
(`GET /api/fusion/params.csv`) and import (`POST /api/fusion/import`) already
existed and already use canonical names (`config/fusion_param_map.yaml`'s
`map:` section is identity for the 33 approved geometry inputs), so this test
exercises exactly those two existing entry points.  The "Fusion" legs are
simulated rather than run inside real Fusion (not available in this
environment): importing a CSV into Fusion just means Fusion's User
Parameters end up holding exactly the (name, value, unit) triples the CSV
carries -- which is exactly the six-column CSV parsed and re-serialised
here -- and exporting from Fusion (fusion360_export_params.py) reads those
same triples back out, unit-for-unit, name-for-name.  What is real: our own
export function, our own import function (in dry-run, so nothing is
written), and the six-column CSV format both sides speak.
"""
from __future__ import annotations

import asyncio
import csv
import io

from fastapi import UploadFile

from motor_ai_sim.routes.fusion import export_params_csv, import_params


def _our_export_csv() -> str:
    r = export_params_csv()
    return bytes(r.body).decode("utf-8-sig")


def _simulate_fusion_roundtrip(csv_text: str) -> str:
    """our CSV -> "Fusion" (a name->(unit,value) dict, exactly what an
    import into User Parameters would hold) -> re-exported CSV.

    Identity in every field: this is what makes it a valid simulation of
    "import into Fusion, then export from Fusion" for parameters that are
    already canonical -- no renaming or conversion is exercised in THIS
    test (that is scripts/fusion_param_rename.py's job, already covered by
    tests/test_fusion_param_rename.py); this test is about the existing
    app <-> Fusion channel staying lossless for names/values/units it
    already agrees on.
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    header, body = rows[0], rows[1:]
    fusion_params = {r[0]: {"unit": r[1], "value": r[3]} for r in body if r and r[0]}

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for name, d in fusion_params.items():
        w.writerow([name, d["unit"], d["value"], d["value"], "", "False"])
    return buf.getvalue()


def _dry_run_import(csv_text: str) -> dict:
    up = UploadFile(filename="params.csv", file=io.BytesIO(csv_text.encode("utf-8-sig")))
    return asyncio.run(import_params(file=up, dry_run=1, _admin={"role": "admin"}))


def test_export_fusion_roundtrip_export_import_reports_no_changes():
    original = _our_export_csv()
    reexported = _simulate_fusion_roundtrip(original)
    result = _dry_run_import(reexported)
    assert result["ok"] is True
    assert result["applied"] == {}, (
        "round trip changed values: %r" % (result["applied"],))
    # every primary geometry key we exported should show up as either
    # unchanged (present, identical) -- proving the CSV -> Fusion -> CSV ->
    # import path is lossless for the values our app itself produced.
    original_names = {row[0] for row in csv.reader(io.StringIO(original))
                       if row and row[0] != "Name"}
    assert set(result["unchanged"]) == original_names


def test_reexported_csv_keeps_the_six_parameter_io_columns():
    reexported = _simulate_fusion_roundtrip(_our_export_csv())
    rows = list(csv.reader(io.StringIO(reexported)))
    assert rows[0] == ["Name", "Unit", "Expression", "Value", "Comment", "Favorite"]
    for r in rows[1:]:
        assert len(r) == 6, r

"""The Fusion add-in's pure-Python parts (fusion/AeroStatorSync/aerostator_sync): units, Import diff, bundle manifest, HTTP client.
The Fusion-side code (fusion_collect.py, AeroStatorSync.py) needs Fusion and is exercised through the Fusion MCP; see
docs/FUSION_SYNC.md section 7."""
import io
import json
import os
import sys
import urllib.error
import zipfile

import pytest

ADDIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fusion", "AeroStatorSync")
sys.path.insert(0, ADDIN)

from aerostator_sync import bundle as B  # noqa: E402
from aerostator_sync import client as C  # noqa: E402
from aerostator_sync import params as P  # noqa: E402


# ── units / expressions ──────────────────────────────────────────────────────────────────────────────────────────────
def test_internal_values_convert_to_the_parameter_unit():
    assert P.internal_to_unit(5.0, "mm") == pytest.approx(50.0)       # Fusion keeps lengths in cm
    assert P.internal_to_unit(0.5, "cm") == pytest.approx(0.5)
    assert P.internal_to_unit(3.141592653589793, "deg") == pytest.approx(180.0)
    assert P.internal_to_unit(7.0, "") == 7.0


def test_formula_detection_ignores_functions_and_units():
    assert P.is_formula("stator_diameter / 2")
    assert P.is_formula("sqrt(( tooth_width / 2 ) ^ 2 + 1 mm)")
    assert not P.is_formula("12 mm")
    assert not P.is_formula("( 4.9 / 2 ) * 1 mm")
    assert not P.is_formula("360 deg")
    assert P.references("2 * ceil(holder_screw_req / 2 mm) * 1 mm") == ["holder_screw_req"]


def test_expression_formatting_keeps_explicit_units():
    assert P.format_expression(50, "mm") == "50 mm"
    assert P.format_expression(0.267, "") == "0.267"
    assert P.plain_number("-0.21 mm") == pytest.approx(-0.21)
    assert P.plain_number("motor_length") is None


# ── Import diff ──────────────────────────────────────────────────────────────────────────────────────────────────────
FUSION = {
    "stator_diameter": {"expression": "40 mm", "unit": "mm", "value": 40.0},
    "motor_length": {"expression": "15 mm", "unit": "mm", "value": 15.0},
    "magnet_height": {"expression": "rotor_h * 2", "unit": "mm", "value": 5.7},
    "num_seg": {"expression": "2", "unit": "", "value": 2.0},
}
SERVER = [
    {"name": "stator_diameter", "key": "stator_diameter", "value": 50, "unit": "mm"},
    {"name": "motor_length", "key": "motor_length", "value": 15, "unit": "mm"},
    {"name": "magnet_height", "key": "magnet_height", "value": 7, "unit": "mm"},
    {"name": "num_seg", "key": "num_seg", "value": 2, "unit": ""},
    {"name": "shaft_height", "key": "shaft_height", "value": 2, "unit": "mm"},
]


def test_import_diff_rows_and_default_ticks():
    rows = {r["name"]: r for r in P.import_diff(FUSION, SERVER)}
    assert rows["stator_diameter"]["action"] == "update" and rows["stator_diameter"]["tick"]
    assert rows["stator_diameter"]["new_expression"] == "50 mm"
    assert rows["motor_length"]["action"] == "unchanged" and not rows["motor_length"]["tick"]
    # a formula row is shown but NOT ticked: writing a number would cut the owner's formula
    assert rows["magnet_height"]["action"] == "update" and rows["magnet_height"]["formula"] and not rows["magnet_height"]["tick"]
    assert rows["shaft_height"]["action"] == "create" and rows["shaft_height"]["tick"]
    assert [r["action"] for r in P.import_diff(FUSION, SERVER)][:2] == ["update", "update"]       # changes first


def test_export_diff_lists_only_disagreements():
    d = P.export_diff(FUSION, SERVER)
    assert {x["name"] for x in d} == {"stator_diameter", "magnet_height"}


def test_legacy_names_are_detected_not_written():
    assert P.legacy_names_present(["stator_up_r", "magnet_h", "motor_length"]) == {"stator_up_r": "stator_diameter",
                                                                                     "magnet_h": "magnet_height"}
    # once renamed (canonical present), the leftover derived stator_up_r is no longer a legacy hit
    assert P.legacy_names_present(["stator_up_r", "stator_diameter"]) == {}


# ── bundle ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _fake_bundle(tmp_path):
    root = tmp_path / "b"
    (root / "parts").mkdir(parents=True)
    (root / "sketches").mkdir()
    (root / "parameters.json").write_text(json.dumps([{"name": "motor_length", "expression": "15 mm", "unit": "mm", "value": 15}]))
    (root / "assembly.step").write_text("ISO-10303-21; assembly")
    (root / "parts" / "Core.step").write_text("ISO-10303-21; core")
    (root / "sketches" / "Core__Sketch1.dxf").write_text("0\nSECTION")
    parts = {"Core": {"file": "parts/Core.step", "bodies": [{"name": "Body565", "volume_mm3": 9015.3}], "occurrences": []}}
    sk = {"Core/Sketch1": {"file": "sketches/Core__Sketch1.dxf"}}
    m = B.make_manifest(document="CIANO14 50", urn="urn:x", version=2, link={"die": "D", "config": "L"}, fusion_build="t",
                        user="u", parts=parts, sketches=sk, root_dir=str(root))
    return root, m


def _reader(root):
    def read(rel):
        p = os.path.join(str(root), *rel.split("/"))
        return open(p, "rb").read() if os.path.isfile(p) else None
    return read


def test_manifest_lists_every_file_with_sha256(tmp_path):
    root, m = _fake_bundle(tmp_path)
    assert m["schema"] == B.SCHEMA and m["version"] == 2
    assert set(m["files"]) == {"parameters.json", "assembly.step", "parts/Core.step", "sketches/Core__Sketch1.dxf"}
    assert B.check_manifest(m, _reader(root)) == []


def test_tampered_or_missing_file_is_reported(tmp_path):
    root, m = _fake_bundle(tmp_path)
    (root / "parts" / "Core.step").write_text("changed")
    os.remove(root / "sketches" / "Core__Sketch1.dxf")
    errs = " ".join(B.check_manifest(m, _reader(root)))
    assert "parts/Core.step: sha256 mismatch" in errs and "sketches/Core__Sketch1.dxf listed but absent" in errs


def test_zip_round_trip(tmp_path):
    root, m = _fake_bundle(tmp_path)
    z = B.write_zip(str(root), m, str(tmp_path / B.bundle_name("CIANO14 50", 2)))
    assert os.path.basename(z) == "CIANO14_50_v2.zip"
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        assert names == set(m["files"]) | {"manifest.json"}
        back = json.loads(zf.read("manifest.json"))
        assert B.check_manifest(back, lambda rel: zf.read(rel) if rel in names else None) == []


def test_file_names_are_safe_and_unique():
    used = set()
    a = B.unique_file(B.safe_name("91292A335_NO THREADS_18-8 Stainless Steel"), used, ".step")
    b = B.unique_file(B.safe_name("91292A335 NO THREADS 18-8 Stainless Steel"), used, ".step")
    assert a == "91292A335_NO_THREADS_18-8_Stainless_Steel.step" and b == "91292A335_NO_THREADS_18-8_Stainless_Steel_2.step"


# ── client ───────────────────────────────────────────────────────────────────────────────────────────────────────────
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_client_sends_bearer_and_quotes_die_names():
    seen = {}

    def opener(req, timeout=0):
        seen.update(url=req.full_url, auth=req.get_header("Authorization"), method=req.get_method())
        return _Resp(json.dumps({"parameters": []}).encode())

    cl = C.Client("https://example.test/", "emk_a_b", opener=opener)
    assert cl.params("CIANO14 50 edited", "L15") == {"parameters": []}
    assert seen["url"] == "https://example.test/api/fusion/sync/CIANO14%2050%20edited/L15/params.json"
    assert seen["auth"] == "Bearer emk_a_b" and seen["method"] == "GET"


def test_client_reports_the_server_refusal():
    def opener(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                     io.BytesIO(json.dumps({"detail": {"code": "fusion_sync_not_enabled",
                                                                       "message": "die CIANO14 40 new is not enabled"}}).encode()))

    with pytest.raises(C.SyncError) as e:
        C.Client("https://x.test", "k", opener=opener).params("CIANO14 40 new", "L12")
    assert e.value.status == 403 and "not enabled" in str(e.value)


def test_client_link_404_means_not_linked():
    def opener(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"detail": "not linked"}'))

    assert C.Client("https://x.test", "k", opener=opener).get_link("urn:x") is None


def test_upload_is_multipart_with_the_zip(tmp_path):
    z = tmp_path / "CIANO14_50_v2.zip"
    z.write_bytes(b"PK\x03\x04zipdata")
    seen = {}

    def opener(req, timeout=0):
        seen.update(ctype=req.get_header("Content-type"), body=req.data, url=req.full_url)
        return _Resp(b'{"version": 2}')

    assert C.Client("https://x.test", "k", opener=opener).upload(str(z)) == {"version": 2}
    assert seen["url"].endswith("/api/fusion/sync/bundles")
    assert seen["ctype"].startswith("multipart/form-data; boundary=")
    assert b'filename="CIANO14_50_v2.zip"' in seen["body"] and b"PK\x03\x04zipdata" in seen["body"]


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_key_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from aerostator_sync import keystore as K
    K.save_key("emk_test_secret")
    assert K.load_key() == "emk_test_secret"
    assert b"emk_test_secret" not in (tmp_path / "MOTRES" / "fusion_sync" / "key.dpapi").read_bytes()

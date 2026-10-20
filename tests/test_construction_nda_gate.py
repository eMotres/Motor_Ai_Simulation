# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Security review 2026-10-20 (Codex Astra pre-launch review).

F8 - the construction dimensions are the FULL construction: GET / PUT ``cad_params``, the import and the rebuild answer 403
``nda_required`` unless the caller passes ``model3d.can_download`` (the gate of the full STEP and of the part tree: the owner /
admin; an account flagged "NDA signed" by an administrator; an online NDA signer only once the applicant verification is
VERIFIED).  A hidden / missing configuration still answers 404 first.  Flat and layered installations.

F5 - an FCStd upload is read through a bounded stream: the expanded Document.xml, its element count, its aliased cells and the
expression dependency depth are capped (413 / 422).  The tests lower the limits instead of building a real bomb.

Synthetic stores only (the fixture of tests/test_config_visibility.py); the built CAD parameters are stubbed.
"""
from __future__ import annotations

import io
import zipfile

import pytest
import yaml

from tests.test_config_visibility import ADMIN, DIE, NOSUCH, PRIV, PUB, client, env  # noqa: F401  (env = the fixture)

ONLINE = "online@example.com"        # signed the NDA ONLINE, applicant verification not complete
VERIFIED = "verified@example.com"    # signed the NDA ONLINE, verification VERIFIED
FLAGGED = "flagged@example.com"      # "NDA signed" set by an administrator
SECRET_DIM = 3.14159                 # the synthetic shaft dimension that must not leave the server without the gate

CP = "/api/family/config/{die}/{cfg}/cad_params"
REBUILD = "/api/family/config/{die}/{cfg}/cad_rebuild"


@pytest.fixture()
def gate(env, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import cad_params as cp
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import construction
    from motor_ai_sim.verification import service as VS

    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    monkeypatch.setenv("MOTRES_VERIFICATION_ROOT", str(env["tmp"] / "verification"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(env["tmp"] / "admin_audit.jsonl"))
    for e, name in ((ONLINE, "Online"), (VERIFIED, "Verified"), (FLAGGED, "Flagged")):
        U.create_user(e, "password-" + name.lower(), role="user", name=name)
        U.set_motor_grants(e, all_motors=True)
    U.set_nda(ONLINE, True, "2026-10-01", by="online")
    U.set_nda(VERIFIED, True, "2026-10-01", by="online")
    U.set_nda(FLAGGED, True, "2026-10-01", by=ADMIN)
    # the verification store's decision; access_ok itself (NDA source -> verification) runs for real
    monkeypatch.setattr(VS, "is_verified", lambda uid: uid == VERIFIED)
    monkeypatch.setattr(cp, "read_built_params", lambda *a, **k: {
        "shaft_shoulder_len": {"name": "shaft_shoulder_len", "value": SECRET_DIM, "role": "input", "unit": "mm",
                               "freecad_expression": "%s mm" % SECRET_DIM}})
    monkeypatch.setattr(cp, "validate", lambda *a, **k: [])
    monkeypatch.setattr(construction, "_bearing_choices", lambda: [])
    hdr = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}   # noqa: E731
    return dict(env, online=hdr(ONLINE), verified=hdr(VERIFIED), flagged=hdr(FLAGGED))


def _nda_denied(r) -> bool:
    return r.status_code == 403 and isinstance(r.json().get("detail"), dict) and r.json()["detail"].get("code") == "nda_required"


def _calls(cfg):
    csv_file = {"file": ("p.csv", b"Name,Unit,Expression,Value\nring_t,mm,0.9,0.9\n", "text/csv")}
    return (("GET", CP.format(die=DIE, cfg=cfg), {}),
            ("PUT", CP.format(die=DIE, cfg=cfg), {"json": {"values": {}}}),
            ("POST", CP.format(die=DIE, cfg=cfg) + "/import?dry_run=1", {"files": csv_file}),
            ("POST", REBUILD.format(die=DIE, cfg=cfg), {}))


def _no_override_written(env):
    docs = list(env["dies_dir"].rglob(f"{PUB}.yaml"))
    if env["layered"]:
        docs += list((env["tmp"] / "srv" / "workspaces").rglob(f"{PUB}.yaml"))
    return all("cad_overrides" not in (yaml.safe_load(p.read_text("utf-8")) or {}) for p in docs)


# -- F8 ------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("who", ["plain", "online"])
def test_without_a_verified_nda_every_construction_route_is_denied(gate, who):
    """ordinary without NDA, and online-NDA but unverified: 403 nda_required on GET / PUT / import / rebuild; nothing returned,
    nothing written, nothing queued."""
    from motor_ai_sim.cad import rebuild_queue as RQ
    from motor_ai_sim import cad_params as cp
    for method, url, kw in _calls(PUB):
        r = client.request(method, url, headers=gate[who], **kw)
        if method == "GET" or gate["layered"]:
            assert _nda_denied(r), (who, method, url, r.status_code, r.text[:300])
        else:                                                   # flat: the owner's catalog, writes are admin-only anyway
            assert r.status_code == 403, (who, method, url, r.status_code, r.text[:300])
        assert str(SECRET_DIM) not in r.text
    assert _no_override_written(gate)
    assert RQ.read(cp.cad_root(), DIE, PUB) is None


@pytest.mark.parametrize("who", ["verified", "flagged", "admin"])
def test_a_verified_nda_account_and_the_admin_keep_access(gate, who):
    r = client.get(CP.format(die=DIE, cfg=PUB), headers=gate[who])
    assert r.status_code == 200, (who, r.text[:300])
    assert any(row["name"] == "shaft_shoulder_len" and row["value"] == SECRET_DIM for row in r.json()["rows"])
    for method, url, kw in _calls(PUB)[1:]:
        r = client.request(method, url, headers=gate[who], **kw)
        assert not _nda_denied(r), (who, method, url, r.text[:300])
        if who == "admin" or gate["layered"]:
            assert r.status_code == 200, (who, method, url, r.status_code, r.text[:300])
        else:
            assert r.status_code == 403                         # flat: the catalog-write rule (admin only) is unchanged


@pytest.mark.parametrize("who", ["plain", "online", "verified"])
def test_a_hidden_configuration_still_reads_like_a_missing_one_before_the_gate(gate, who):
    """The 404 of a hidden / missing configuration comes BEFORE the NDA gate: a 403 would tell a stranger it exists."""
    for (m1, u1, kw1), (_m2, u2, kw2) in zip(_calls(PRIV), _calls(NOSUCH)):
        a = client.request(m1, u1, headers=gate[who], **kw1)
        b = client.request(m1, u2, headers=gate[who], **kw2)
        if gate["layered"] or m1 == "GET":
            assert a.status_code == b.status_code == 404, (who, m1, u1, a.status_code, b.status_code)
            assert a.text.replace(PRIV, "<C>") == b.text.replace(NOSUCH, "<C>")


def test_the_part_tree_and_cad_params_share_one_gate(gate):
    """The part tree already had the gate; cad_params now uses the same function, and the same callers pass or fail."""
    for who in ("plain", "online", "verified", "flagged", "admin"):
        tree = client.get(f"/api/family/die/{DIE}/part_tree", params={"config": PUB}, headers=gate[who])
        cad = client.get(CP.format(die=DIE, cfg=PUB), headers=gate[who])
        assert _nda_denied(tree) == _nda_denied(cad), (who, tree.status_code, cad.status_code)


def test_astra_f8_repro_now_denied(gate):
    """Codex Astra's F8 probe (astra_prelaunch/test_review_repros.py::test_cad_dimensions_are_returned_without_nda) with the
    expectation flipped: the ordinary caller who fails model3d.can_download no longer reads the shaft dimension."""
    from motor_ai_sim import auth, model3d, users
    identity = auth.caller_identity(gate["plain"]["Authorization"])
    assert not model3d.can_download(identity, users.get_user(identity["id"]))
    r = client.get(f"/api/family/config/{DIE}/{PUB}/cad_params", headers=gate["plain"])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "nda_required"
    assert "rows" not in r.json() and str(SECRET_DIM) not in r.text


# -- F5 ------------------------------------------------------------------------------------------------------------------------

def _fcstd(xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Document.xml", xml)
    return buf.getvalue()


def _doc(cells: str, pad: int = 0) -> bytes:
    return (b"<?xml version='1.0' encoding='utf-8'?>\n<Document>" + b" " * pad
            + b'<Objects><Object type="Spreadsheet::Sheet" name="Params"/></Objects><ObjectData><Object name="Params">'
            + b'<Properties><Property name="cells" type="Spreadsheet::PropertySheet"><Cells>' + cells.encode()
            + b"</Cells></Property></Properties></Object></ObjectData></Document>")


def test_reader_matches_the_old_tree_reader_on_a_normal_file():
    from motor_ai_sim.cad import store as S
    data = _fcstd(_doc('<Cell address="A1" content="=2 mm" alias="a"/><Cell address="A2" content="=a*3" alias="b"/>'
                       '<Cell address="A3" content="note"/>'))
    assert S.read_fcstd_params(io.BytesIO(data)) == {"a": "2 mm", "b": "a*3"}
    assert S.evaluate(S.read_fcstd_params(io.BytesIO(data))) == {"a": 2.0, "b": 6.0}
    assert S.read_fcstd_cells(io.BytesIO(data)) == [("a", "=2 mm"), ("b", "=a*3")]


def test_astra_f5_repro_small_archive_large_xml_is_refused(monkeypatch):
    """Codex Astra's F5 probe with the expectation flipped (limit lowered so nothing large is allocated): ~1.3 kB of zip
    expanding to 1 MB of Document.xml is refused with 413 BEFORE it is read into memory."""
    from motor_ai_sim.cad import store as S
    monkeypatch.setattr(S, "MAX_DOCUMENT_XML_BYTES", 64 * 1024)
    data = _fcstd(_doc('<Cell address="A1" content="1" alias="x"/>', pad=1024 * 1024))
    assert len(data) < 4096
    with pytest.raises(S.FcstdLimitError) as ei:
        S.read_fcstd_params(io.BytesIO(data))
    assert ei.value.status == 413


def test_a_lying_size_header_cannot_read_past_the_limit(monkeypatch):
    """The central directory says the entry is small; the stream is longer.  The read stops at the declared size (zipfile) and
    never yields more than the limit; the file is refused, not parsed."""
    from motor_ai_sim.cad import store as S
    monkeypatch.setattr(S, "MAX_DOCUMENT_XML_BYTES", 4096)
    xml = _doc('<Cell address="A1" content="1" alias="x"/>', pad=40_000)
    data = bytearray(_fcstd(xml))
    cd = data.rfind(b"PK\x01\x02")                             # central directory entry: uncompressed size at +24
    data[cd + 24:cd + 28] = (1000).to_bytes(4, "little")
    lh = data.find(b"PK\x03\x04")                              # local header: uncompressed size at +22
    data[lh + 22:lh + 26] = (1000).to_bytes(4, "little")
    seen = []
    real = S._document_xml_chunks

    def spy(source, max_bytes):
        for c in real(source, max_bytes):
            seen.append(len(c))
            yield c
    monkeypatch.setattr(S, "_document_xml_chunks", spy)
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        S.read_fcstd_params(io.BytesIO(bytes(data)))
    assert sum(seen) <= 4096


def test_element_cell_and_depth_limits(monkeypatch):
    from motor_ai_sim.cad import store as S
    monkeypatch.setattr(S, "MAX_XML_ELEMENTS", 50)
    many = "".join('<Cell address="A%d" content="1" alias="c%d"/>' % (i, i) for i in range(60))
    with pytest.raises(S.FcstdLimitError) as ei:
        S.read_fcstd_params(io.BytesIO(_fcstd(_doc(many))))
    assert ei.value.status == 422 and "elements" in str(ei.value)
    monkeypatch.setattr(S, "MAX_XML_ELEMENTS", 10_000)
    monkeypatch.setattr(S, "MAX_SHEET_CELLS", 20)
    with pytest.raises(S.FcstdLimitError) as ei:
        S.read_fcstd_params(io.BytesIO(_fcstd(_doc(many))))
    assert ei.value.status == 422 and "cells" in str(ei.value)
    monkeypatch.setattr(S, "MAX_DEPENDENCY_DEPTH", 10)
    chain = {"c0": "1"}
    chain.update({"c%d" % i: "c%d + 1" % (i - 1) for i in range(1, 30)})
    with pytest.raises(ValueError, match="deeper than 10"):
        S.evaluate(dict(reversed(list(chain.items()))))
    short = {k: v for k, v in chain.items() if int(k[1:]) < 9}
    assert S.evaluate(short)["c8"] == 9.0


def test_a_doctype_or_entity_declaration_is_refused():
    from motor_ai_sim.cad import store as S
    xml = (b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
           b'<Document><Object name="Params"><Property name="cells"><Cell alias="x" content="&b;"/></Property></Object></Document>')
    with pytest.raises(ValueError, match="DOCTYPE"):
        S.read_fcstd_params(io.BytesIO(_fcstd(xml)))


def test_the_import_route_answers_413_and_422(gate, monkeypatch):
    """Over HTTP, by a caller who passes every gate (the admin): too large -> 413, too complex -> 422; nothing is saved."""
    from motor_ai_sim.cad import store as S
    url = CP.format(die=DIE, cfg=PUB) + "/import?dry_run=0"
    monkeypatch.setattr(S, "MAX_DOCUMENT_XML_BYTES", 64 * 1024)
    big = _fcstd(_doc('<Cell address="A1" content="1" alias="x"/>', pad=1024 * 1024))
    r = client.post(url, files={"file": ("m.FCStd", big, "application/octet-stream")}, headers=gate["admin"])
    assert r.status_code == 413, r.text[:300]
    monkeypatch.setattr(S, "MAX_XML_ELEMENTS", 20)
    many = "".join('<Cell address="A%d" content="1" alias="c%d"/>' % (i, i) for i in range(40))
    r = client.post(url, files={"file": ("m.FCStd", _fcstd(_doc(many)), "application/octet-stream")}, headers=gate["admin"])
    assert r.status_code == 422, r.text[:300]
    monkeypatch.setattr(S, "MAX_XML_ELEMENTS", 10_000)
    monkeypatch.setattr(S, "MAX_DEPENDENCY_DEPTH", 5)
    # the deepest cell first in the document, so evaluate() walks the whole chain at once
    deep = "".join('<Cell address="A%d" content="=c%d+1" alias="c%d"/>' % (i, i - 1, i) for i in range(11, 0, -1))
    deep += '<Cell address="A0" content="1" alias="c0"/>'
    r = client.post(url, files={"file": ("m.FCStd", _fcstd(_doc(deep)), "application/octet-stream")}, headers=gate["admin"])
    assert r.status_code == 422 and "deeper" in r.text, r.text[:300]
    assert _no_override_written(gate)


# -- follow-up: the per-part materials / masses, the open CAD exports ----------------------------------------------------------

PM = f"/api/family/die/{DIE}/part_materials"


@pytest.mark.parametrize("who", ["plain", "online"])
def test_part_materials_breakdown_is_denied_without_a_verified_nda(gate, who):
    """The per-part materials + masses are the BOM of the full construction: the same gate as the part tree / bom.csv."""
    r = client.get(PM, params={"config": PUB}, headers=gate[who])
    assert _nda_denied(r), (who, r.status_code, r.text[:300])
    assert "parts" not in r.json() and "mass_kg" not in r.text
    r = client.get(PM, headers=gate[who])                               # "the first configuration" form too
    assert _nda_denied(r)
    r = client.put(PM, json={"part": "screws", "material": "copper"}, headers=gate[who])
    assert r.status_code == 403
    assert "part_materials" not in yaml.safe_load((env_die_yaml(gate)).read_text("utf-8"))


def env_die_yaml(gate):
    return gate["dies_dir"] / DIE / "die.yaml"


@pytest.mark.parametrize("who", ["verified", "flagged", "admin"])
def test_part_materials_stay_open_to_a_verified_nda_account_and_the_admin(gate, who):
    r = client.get(PM, params={"config": PUB}, headers=gate[who])
    assert r.status_code == 200, (who, r.text[:300])
    assert r.json()["config"] == PUB and "parts" in r.json()


@pytest.mark.parametrize("who", ["plain", "verified"])
def test_part_materials_hidden_and_missing_configurations_still_answer_alike(gate, who):
    a = client.get(PM, params={"config": PRIV}, headers=gate[who])
    b = client.get(PM, params={"config": NOSUCH}, headers=gate[who])
    assert a.status_code == b.status_code and a.text.replace(PRIV, "<C>") == b.text.replace(NOSUCH, "<C>"), (a.text, b.text)
    assert client.get("/api/family/die/NO SUCH DIE/part_materials", headers=gate[who]).status_code == 404


def test_the_public_motor_mass_is_not_behind_the_gate(gate, monkeypatch):
    """Only the per-part breakdown is gated: the motor mass Configure / the catalog show (mass_summary full_kg / active_kg)
    stays public by owner decision - an ordinary caller without NDA still gets it."""
    from motor_ai_sim.modules import motor_mass as _mm
    monkeypatch.setattr(_mm, "mass_summary", lambda die, config, **k: {"full_kg": 0.181, "active_kg": 0.151, "partial": False})
    r = client.get("/api/catalog/cat_pub/configure_context", headers=gate["plain"])
    assert r.status_code == 200, r.text[:300]
    masses = [p.get("mass") for p in r.json().get("presets") or []]
    assert {"full_kg": 0.181, "active_kg": 0.151} in [{k: m[k] for k in ("full_kg", "active_kg")} for m in masses if m]
    assert _nda_denied(client.get(PM, params={"config": PUB}, headers=gate["plain"]))


@pytest.mark.parametrize("path", ["/api/freecad/export", "/api/fusion/params.csv", "/api/fusion/params.json"])
def test_the_cad_exports_need_a_signed_in_account_but_no_nda(gate, path, monkeypatch):
    from motor_ai_sim import auth
    assert auth.required_role("GET", path) == "user"
    r = client.get(path)                                               # anonymous exhibit visitor
    assert r.status_code == 401, r.text[:200]
    if path != "/api/freecad/export":                                  # the bundle builds solids: not run here (CPU)
        r = client.get(path, headers=gate["plain"])                    # signed in, no NDA
        assert r.status_code == 200, r.text[:200]

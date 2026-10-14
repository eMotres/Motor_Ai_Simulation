"""Optional consent to share a customer's own data with the engineering dataset (data_sharing.py, routes/data_sharing.py).

Default false, signing never depends on it, toggle + append-only audit, the export filter (consent false -> excluded, true ->
included and anonymised, created before the consent -> excluded, Restricted Material -> never), the admin view and EN / zh-CN parity.
Same throwaway registry / legal directory as test_nda.py; no FreeCAD, no SMTP, no solver.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from motor_ai_sim import data_sharing as DS
from motor_ai_sim import users as U
from tests.test_nda import FREE, USER, client, env, records, sign  # noqa: F401  (env is a fixture)

REPO = Path(__file__).resolve().parents[1]


def audit(env):
    p = env["legal"] / "data_sharing_audit.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def put(env, who, consent):
    return client.put("/api/data-sharing/mine", json={"consent": consent}, headers=env[who])


# -- default, signing, record ---------------------------------------------------------

def test_consent_defaults_to_false_everywhere(env):
    assert U.get_data_sharing(USER) == {"consent": False, "version": "", "time": ""}
    assert U.public_user(USER)["data_sharing"]["consent"] is False
    mine = client.get("/api/data-sharing/mine", headers=env["user"]).json()
    assert mine["consent"] is False and mine["current_version"] == "data_sharing_consent_v1"
    assert DS.consenting_users() == []


def test_signing_works_without_consent_and_records_false(env):
    assert sign(env).status_code == 200                                       # no data_sharing key at all
    rec = records(env)[-1]
    assert rec["data_sharing"]["consent"] is False and rec["data_sharing"]["version"] == "data_sharing_consent_v1"
    assert U.get_nda(USER)["signed"] is True and U.get_data_sharing(USER)["consent"] is False
    assert [a["consent"] for a in audit(env)] == [False] and audit(env)[0]["source"] == "nda_sign"
    assert audit(env)[0]["record_id"] == rec["id"]


def test_signing_with_consent_stores_it_in_the_record_the_user_and_the_audit(env):
    r = sign(env, data_sharing=True)
    assert r.status_code == 200 and r.json()["data_sharing"]["consent"] is True
    rec = records(env)[-1]
    assert rec["data_sharing"]["consent"] is True and rec["data_sharing"]["version"] == "data_sharing_consent_v1"
    assert rec["data_sharing"]["time"].endswith("Z")
    st = U.get_data_sharing(USER)
    assert st["consent"] is True and st["version"] == "data_sharing_consent_v1"
    assert DS.consenting_users() == [USER]
    # a non-boolean value is not a yes
    assert sign(env, who="free", data_sharing="true").status_code == 200
    assert U.get_data_sharing(FREE)["consent"] is False


def test_the_consent_is_not_part_of_the_signed_text_or_hash(env):
    a = sign(env, data_sharing=False)
    b = sign(env, who="free", data_sharing=True)
    assert a.status_code == b.status_code == 200
    ra, rb = records(env)
    assert ra["template_sha256"] == rb["template_sha256"]
    assert b"data_sharing" not in (env["legal"] / "nda_signed" / (rb["id"] + ".pdf")).read_bytes()


def test_a_revoked_nda_does_not_touch_the_consent(env):
    sign(env, data_sharing=True)
    U.set_nda(USER, False)
    assert U.get_data_sharing(USER)["consent"] is True


# -- toggle + audit -------------------------------------------------------------------------

def test_toggle_appends_one_audit_line_per_change(env):
    assert put(env, "user", True).json()["consent"] is True
    assert put(env, "user", True).status_code == 200                              # unchanged: no new line
    assert put(env, "user", False).json()["consent"] is False
    assert put(env, "user", True).json()["consent"] is True
    lines = audit(env)
    assert [(a["consent"], a["previous"], a["source"]) for a in lines] == [
        (True, False, "account"), (False, True, "account"), (True, False, "account")]
    assert all(a["user"] == USER and a["version"] == "data_sharing_consent_v1" and a["id"] for a in lines)
    assert U.get_data_sharing(USER)["consent"] is True and DS.consenting_users() == [USER]


def test_toggle_needs_a_boolean_and_a_signed_in_account(env):
    assert client.put("/api/data-sharing/mine", json={"consent": "yes"}, headers=env["user"]).status_code == 422
    assert client.put("/api/data-sharing/mine", json={}, headers=env["user"]).status_code == 422
    assert client.put("/api/data-sharing/mine", json={"consent": True}).status_code == 401
    assert client.get("/api/data-sharing/mine").status_code == 401
    assert audit(env) == []


def test_nothing_changes_when_the_audit_line_cannot_be_written(env, monkeypatch):
    def boom(row):
        raise OSError("disk full")
    monkeypatch.setattr(DS, "_audit_append", boom)
    assert put(env, "user", True).status_code == 503
    assert U.get_data_sharing(USER)["consent"] is False


def test_toggle_is_audited_in_the_admin_log(env):
    put(env, "user", True)
    text = Path(os.environ["ADMIN_AUDIT_FILE"]).read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    assert any(r["action"] == "user.data_sharing" and r["details"]["consent"] is True for r in rows)


# -- admin ------------------------------------------------------------------------------------

def test_admin_sees_the_state_and_the_count(env):
    sign(env, data_sharing=True)
    sign(env, who="free", data_sharing=False)
    put(env, "free", True)
    put(env, "free", False)
    j = client.get("/api/nda/signatures", headers=env["admin"]).json()
    assert j["consenting_users"] == 1
    by = {r["user"]: r for r in j["signatures"]}
    assert by[USER]["data_sharing"]["consent"] is True and by[USER]["data_sharing_now"] is True
    assert by[FREE]["data_sharing"]["consent"] is False and by[FREE]["data_sharing_now"] is False
    users = {u["email"]: u for u in U.list_users()}
    assert users[USER]["data_sharing"]["consent"] is True and users[FREE]["data_sharing"]["consent"] is False


def test_old_records_without_the_field_read_as_no_consent():
    from motor_ai_sim import nda as N
    row = N.public_record({"id": "n1", "user": "a@b.c"})
    assert row["data_sharing"] == {"consent": False, "version": "", "time": ""}


# -- the export filter ------------------------------------------------------------------------

def consent_at(who_email, value, ts):
    """Write an audit line (and the user flag) as if it happened at ``ts``."""
    DS._audit_append(dict(id="d-%s" % ts, ts=ts, iso="x", user=who_email, consent=value, previous=not value,
                          version="data_sharing_consent_v1", source="test"))
    U.set_data_sharing(who_email, value, "data_sharing_consent_v1", "x")


def rec(**kw):
    base = dict(id="r1", owner=USER, ts=1500.0, tags=["customer"], kind="run", inputs={"turns": 8, "magnet": "N52UH"},
                results={"torque": 12.5})
    base.update(kw)
    return base


def test_no_consent_means_excluded(env):
    inc, why = DS.filter_for_dataset([rec()])
    assert inc == [] and why == {"no_consent": 1}


def test_consent_true_includes_and_anonymises(env):
    U.update_user(USER, name="Erika Mustermann")
    U.set_nda(USER, True, by="online", version="v1", company="Acme Drones Ltd", record_id="n20261014-aabbccddeeff")
    consent_at(USER, True, 1000.0)
    r = rec(note="Prototype for Acme Drones Ltd, contact erika@acme.example, Erika Mustermann", company="Acme Drones Ltd",
            requester=dict(email=USER, ip="203.0.113.7", user_agent="x"), workspace="ws_abc123",
            variants=[dict(owner=USER, name="N52UH variant", signatory="Erika Mustermann")])
    inc, why = DS.filter_for_dataset([r])
    assert why == {"consent": 1} and len(inc) == 1
    out = inc[0]
    assert out["inputs"] == {"turns": 8, "magnet": "N52UH"} and out["results"] == {"torque": 12.5} and out["tags"] == ["customer"]
    blob = json.dumps(out)
    for needle in ("Acme", "Erika", "Mustermann", "@", "203.0.113.7", USER, "ws_abc123", "aabbccddeeff"):
        assert needle not in blob, needle
    assert out["variants"] == [dict(name="N52UH variant")] and out["note"].count("[removed]") == 3
    assert "owner" not in out and "company" not in out and out["requester"] == {}


def test_a_record_created_before_the_consent_stays_out(env):
    consent_at(USER, True, 2000.0)
    inc, why = DS.filter_for_dataset([rec(ts=1500.0), rec(id="r2", ts=2500.0)])
    assert [r["id"] for r in inc] == ["r2"] and why == {"created_before_consent": 1, "consent": 1}


def test_a_record_made_while_withdrawn_stays_out_after_the_consent_is_given_again(env):
    consent_at(USER, True, 1000.0)
    consent_at(USER, False, 2000.0)
    consent_at(USER, True, 3000.0)
    inc, _ = DS.filter_for_dataset([rec(id="a", ts=1500.0), rec(id="b", ts=2500.0), rec(id="c", ts=3500.0)])
    assert [r["id"] for r in inc] == ["a", "c"]


def test_withdrawal_excludes_everything_including_what_was_made_under_the_consent(env):
    consent_at(USER, True, 1000.0)
    consent_at(USER, False, 2000.0)
    inc, why = DS.filter_for_dataset([rec(ts=1500.0)])
    assert inc == [] and why == {"no_consent": 1}


def test_the_user_flag_alone_is_not_enough_without_the_audit_trail(env):
    U.set_data_sharing(USER, True, "data_sharing_consent_v1", "x")             # flag set, no audit line
    inc, why = DS.filter_for_dataset([rec()])
    assert inc == [] and why == {"no_consent": 1}


def test_unprovable_or_ownerless_customer_records_are_refused(env):
    consent_at(USER, True, 1000.0)
    inc, why = DS.filter_for_dataset([rec(ts=None), rec(ts="not a date"), rec(owner=None, tags=["customer"])])
    assert inc == [] and why == {"creation_time_unknown": 2, "customer_without_owner": 1}
    inc, _ = DS.filter_for_dataset([rec(ts="1970-01-01T00:30:00Z", id="iso")])                  # ISO strings are read
    assert [r["id"] for r in inc] == ["iso"]


def test_restricted_material_is_never_exported_even_with_consent(env):
    consent_at(USER, True, 1000.0)
    bad = [rec(restricted=True), rec(restricted_material=True), rec(origin="motres_restricted"), rec(tags=["customer", "nda"]),
           rec(tags=["confidential"]), rec(confidential="yes"), rec(nda=True), rec(owner=None, tags=["restricted"])]
    inc, why = DS.filter_for_dataset(bad)
    assert inc == [] and why == {"restricted_material": len(bad)}


def test_motres_records_pass_but_are_still_anonymised(env):
    inc, why = DS.filter_for_dataset([dict(id="m1", ts=1.0, inputs={"x": 1}, build=dict(user="vadim@motresres.com"), author="V")])
    assert why == {"motres_data": 1} and inc == [dict(id="m1", ts=1.0, inputs={"x": 1}, build={})]


def test_export_jsonl_end_to_end(env, tmp_path):
    consent_at(USER, True, 1000.0)
    src = tmp_path / "in.jsonl"
    rows = [rec(id="ok"), rec(id="old", ts=10.0), rec(id="other", owner=FREE), rec(id="rest", restricted=True)]
    src.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    rep = DS.export_jsonl(src, out)
    assert rep["written"] == 1 and rep["read"] == 5
    assert rep["reasons"] == {"consent": 1, "created_before_consent": 1, "no_consent": 1, "restricted_material": 1, "unreadable": 1}
    got = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [g["id"] for g in got] == ["ok"] and "owner" not in got[0]


def test_consenting_users_needs_both_the_flag_and_the_last_audit_line(env):
    consent_at(USER, True, 1000.0)
    consent_at(FREE, True, 1000.0)
    consent_at(FREE, False, 2000.0)
    assert DS.consenting_users() == [USER]


# -- text parity (EN + zh-CN) ---------------------------------------------------------------------------

def _flat(o, p=""):
    out = {}
    for k, v in o.items():
        out.update(_flat(v, "%s%s." % (p, k)) if isinstance(v, dict) else {"%s%s" % (p, k): v})
    return out


def test_consent_text_exists_in_en_and_zh_cn_with_the_same_keys_and_the_version():
    en = json.loads((REPO / "web/src/locales/en/common.json").read_text(encoding="utf-8"))["dataSharing"]
    zh = json.loads((REPO / "web/src/locales/zh-CN/common.json").read_text(encoding="utf-8"))["dataSharing"]
    assert set(_flat(en)) == set(_flat(zh)) and len(_flat(en)) >= 10
    assert en["version"] == zh["version"] == DS.CONSENT_VERSION
    assert all(v.strip() for v in _flat(en).values()) and all(v.strip() for v in _flat(zh).values())
    assert any("一" <= c <= "鿿" for c in zh["label"])
    assert "cannot be recalled" in en["withdraw"]            # the tooltip says plainly what cannot be undone

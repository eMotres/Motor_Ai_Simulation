"""A production seen through the development project that links its motor (owner 2026-11-02).

Access matrix (customer / project engineer / factory / outsider / admin; linked vs unlinked motor), several linked motors,
derived access gone after unlink or member removal, and bench-test duties hidden from the project results."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.motor_tests import model_source
from motor_ai_sim.motor_tests import store as MTS

ADMIN = "admin@example.com"
PROD_ENG = "prod-eng@example.com"          # production engineer member (ours)
OEM = "oem@example.com"                    # the factory: production oem member, NDA signed
CUSTOMER = "customer@example.com"          # project customer, NDA signed
CUSTOMER_NO_NDA = "customer2@example.com"  # project customer, no NDA
PROJ_ENG = "proj-eng@example.com"          # project engineer (an ordinary account)
OUTSIDER = "outsider@example.com"
GLOBAL_ENG = "global-eng@example.com"      # global engineer, member of nothing
DIE = "Demo die"
M1, G1, OTHER = "M1", "G1", "X9"
PROJECT = "maxim-ciln28"

PARAM_TEST = {"kind": "parameters", "date": "2026-01-15", "instrument": "LCR meter", "profile": "profile-a",
              "conditions": {"ambient_c": 25.0, "rotor": "in"},
              "resistance": {"temp_c": 25.0, "pairs": [{"pair": "1-2", "value_mohm": 20.0}]}}


def _empty_model(die, config):
    return {"available": False, "duties": [], "noload": [], "r_ref": None, "l_ref": None, "l_stator_only": None,
            "magnet": {"points": []}, "mass_kg": None, "mech": None, "connection": "star", "has_bearings": False}


def _base(cfg):
    return f"/api/production/{DIE}/{cfg}"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth, users as U
    from motor_ai_sim.api import app
    from motor_ai_sim.development import motors, store as DS

    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(tmp_path / "motor_tests"))
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-project-production-1234")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setattr(model_source, "collect", _empty_model)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(PROD_ENG, "password-peng", role="engineer", name="Prod engineer")
    U.create_user(OEM, "password-oem", role="oem", name="Factory")
    U.create_user(CUSTOMER, "password-cust", role="user", name="Customer")
    U.create_user(CUSTOMER_NO_NDA, "password-cust2", role="user", name="Customer 2")
    U.create_user(PROJ_ENG, "password-proj", role="user", name="Project engineer")
    U.create_user(OUTSIDER, "password-out", role="user", name="Outsider")
    U.create_user(GLOBAL_ENG, "password-geng", role="engineer", name="Global engineer")
    for e in (OEM, CUSTOMER):
        U.set_nda(e, True, "2026-10-01", by=ADMIN)
    client = TestClient(app)
    h = {e: {"Authorization": f"Bearer {U.issue_token(e)}"}
         for e in (ADMIN, PROD_ENG, OEM, CUSTOMER, CUSTOMER_NO_NDA, PROJ_ENG, OUTSIDER, GLOBAL_ENG)}
    for cfg in (M1, G1, OTHER):
        r = client.post(f"/api/admin/production/{DIE}/{cfg}", headers=h[ADMIN], json={
            "title": {"en": f"Motor {cfg}", "zh": f"电机 {cfg}", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
            "order_quantity": 2, "share_costs_with_oem": True,
            "members": [{"email": PROD_ENG, "role": "engineer"}, {"email": OEM, "role": "oem"}]})
        assert r.status_code == 200, r.text
    DS.create_project({"id": PROJECT, "title": "CILN28", "members": [
        {"email": CUSTOMER, "role": "customer"}, {"email": CUSTOMER_NO_NDA, "role": "customer"},
        {"email": PROJ_ENG, "role": "engineer"}],
        "linked_motors": [{"die": DIE, "config": M1, "status": "current"}, {"die": DIE, "config": G1, "status": "current"},
                          {"die": DIE, "config": "G2", "status": "current"}]})          # G2: linked, no production yet
    DS.save_index({"motors": {motors._key(DIE, c): {"project_id": PROJECT, "die": DIE, "config": c} for c in (M1, G1, "G2")}})

    # M1: one visible and one internal document, a factory-only thread and a thread opened to the project, one bench test
    def upload(name):
        up = client.post(_base(M1) + "/documents", headers=h[PROD_ENG], files={"file": (name, b"%PDF-1.4 x", "application/pdf")})
        assert up.status_code == 200, up.text
        return up.json()
    visible, internal = upload("drawing.pdf"), upload("quote.pdf")
    assert client.patch(_base(M1) + f"/documents/{internal['id']}", headers=h[PROD_ENG], json={"internal": True}).status_code == 200
    factory = client.post(_base(M1) + "/threads", headers=h[OEM], json={}).json()
    r = client.post(_base(M1) + f"/threads/{factory['id']}/messages", headers=h[OEM],
                    files={"files": ("price.pdf", b"%PDF price", "application/pdf")},
                    data={"text": '{"en": "factory price", "zh": "", "src_lang": "en"}', "src_lang": "en"})
    assert r.status_code == 200, r.text
    factory_file = r.json()["messages"][0]["attachments"][0]["file_id"]
    shared = client.post(_base(M1) + "/threads", headers=h[PROD_ENG], json={"part_id": "p_stator"}).json()
    client.post(_base(M1) + f"/threads/{shared['id']}/messages", headers=h[PROD_ENG],
                json={"text": {"en": "first article ok", "zh": "", "src_lang": "en"}, "src_lang": "en"})
    MTS.mutate(DIE, M1, lambda d: d["tests"].append({**PARAM_TEST, "id": "test_a"}))
    return {"client": client, "h": h, "visible": visible, "internal": internal, "factory": factory, "shared": shared,
            "factory_file": factory_file, "DS": DS, "motors": motors}


def _open_shared(env):
    c, h = env["client"], env["h"]
    r = c.patch(_base(M1) + f"/threads/{env['shared']['id']}", headers=h[PROD_ENG], json={"customer_visible": True})
    assert r.status_code == 200 and r.json()["customer_visible"] is True


# ── the project's productions ─────────────────────────────────────────────────────────────

def test_several_linked_motors_give_several_blocks_counted_as_the_caller_sees_them(env):
    c, h = env["client"], env["h"]
    r = c.get(f"/api/projects/{PROJECT}/productions", headers=h[CUSTOMER])
    assert r.status_code == 200, r.text
    rows = r.json()["productions"]
    assert [(p["die"], p["config"]) for p in rows] == [(DIE, M1), (DIE, G1)]      # G2 has no production; X9 not linked
    m1 = rows[0]
    assert m1["access"] == "project" and m1["role"] == "viewer"
    assert m1["title"] == {"en": "Motor M1", "zh": "电机 M1"} and m1["revision"] == "1.0" and m1["status"] == "active"
    assert m1["documents"] == 1 and m1["discussions"] == 0            # internal doc and factory threads not counted
    assert "tests" not in m1                                           # a customer: tests behind the configuration flag
    text = r.text
    for secret in ("members", "share_costs_with_oem", OEM, "quote.pdf", "cost"):
        assert secret not in text
    _open_shared(env)
    MTS.mutate(DIE, M1, lambda d: d["settings"].update(tests_visible_to_customer=True))
    m1 = c.get(f"/api/projects/{PROJECT}/productions", headers=h[CUSTOMER]).json()["productions"][0]
    assert m1["discussions"] == 1 and m1["tests"] == {"count": 1, "by_kind": {"parameters": 1}}


def test_project_staff_see_tests_admin_sees_everything_outsiders_and_the_factory_see_no_project(env):
    c, h = env["client"], env["h"]
    eng = c.get(f"/api/projects/{PROJECT}/productions", headers=h[PROJ_ENG]).json()["productions"][0]
    assert eng["access"] == "project" and eng["tests"]["count"] == 1   # project engineer: tests without the flag
    adm = c.get(f"/api/projects/{PROJECT}/productions", headers=h[ADMIN]).json()["productions"][0]
    assert adm["access"] == "member" and adm["role"] == "admin" and adm["documents"] == 2 and adm["discussions"] == 2
    for who in (OUTSIDER, OEM):
        assert c.get(f"/api/projects/{PROJECT}/productions", headers=h[who]).status_code == 404
    # a global engineer may read the project but is not given a production through it
    geng = c.get(f"/api/projects/{PROJECT}/productions", headers=h[GLOBAL_ENG]).json()["productions"][0]
    assert geng["access"] == "none" and "documents" not in geng and "tests" not in geng


# ── the production module, read through the project ───────────────────────────────────────

def test_customer_reads_header_visible_documents_and_opened_threads_only(env):
    c, h = env["client"], env["h"]
    p = c.get(_base(M1), headers=h[CUSTOMER])
    assert p.status_code == 200, p.text
    assert p.json()["access"] == "project" and p.json()["members"] == [] and "share_costs_with_oem" not in p.json()
    assert c.get(_base(M1) + "/access", headers=h[CUSTOMER]).json() == {
        "access": "member", "role": "viewer", "can_share": False, "factory": False}
    docs = c.get(_base(M1) + "/documents", headers=h[CUSTOMER]).json()["documents"]
    assert [d["name"] for d in docs] == ["drawing.pdf"]
    assert c.get(_base(M1) + "/threads", headers=h[CUSTOMER]).json()["threads"] == []
    _open_shared(env)
    threads = c.get(_base(M1) + "/threads", headers=h[CUSTOMER]).json()["threads"]
    assert [t["id"] for t in threads] == [env["shared"]["id"]]
    # the factory still reads every thread
    assert len(c.get(_base(M1) + "/threads", headers=h[OEM]).json()["threads"]) == 2
    listed = {(p["die"], p["config"]): p for p in c.get("/api/production", headers=h[CUSTOMER]).json()["productions"]}
    assert set(listed) == {(DIE, M1), (DIE, G1)} and all(p["access"] == "project" and p["members"] == [] for p in listed.values())


def test_customer_files_only_visible_ones_and_only_with_the_nda(env):
    c, h = env["client"], env["h"]
    assert c.get(_base(M1) + f"/files/{env['visible']['file_id']}", headers=h[CUSTOMER]).status_code == 200
    assert c.get(_base(M1) + f"/files/{env['internal']['file_id']}", headers=h[CUSTOMER]).status_code == 404
    assert c.get(_base(M1) + f"/files/{env['factory_file']}", headers=h[CUSTOMER]).status_code == 404
    nda = c.get(_base(M1) + f"/files/{env['visible']['file_id']}", headers=h[CUSTOMER_NO_NDA])
    assert nda.status_code == 403 and nda.json()["detail"]["code"] == "nda_required"
    assert c.get(_base(M1) + "/documents.zip", headers=h[CUSTOMER]).status_code == 404


def test_customer_never_reaches_costs_parts_bom_process_or_writes(env):
    c, h = env["client"], env["h"]
    for path in ("/parts", "/bom", "/bom?list=tooling", "/costs", "/procurement", "/schedule", "/process", "/proposals",
                 "/revisions", "/assembly", "/assembly_process"):
        assert c.get(_base(M1) + path, headers=h[CUSTOMER]).status_code == 404, path
    assert c.post(_base(M1) + "/threads", headers=h[CUSTOMER], json={}).status_code == 404
    assert c.post(_base(M1) + f"/threads/{env['shared']['id']}/messages", headers=h[CUSTOMER],
                  json={"text": {"en": "hi", "zh": "", "src_lang": "en"}}).status_code == 404
    assert c.patch(_base(M1) + f"/threads/{env['shared']['id']}", headers=h[CUSTOMER],
                   json={"customer_visible": False}).status_code == 404
    assert c.patch(_base(M1) + f"/threads/{env['shared']['id']}", headers=h[OEM],
                   json={"customer_visible": True}).status_code == 403          # the factory does not open threads
    assert c.post(_base(M1) + "/documents", headers=h[CUSTOMER],
                  files={"file": ("x.pdf", b"x", "application/pdf")}).status_code == 404


def test_tests_through_the_production_route_follow_the_project_role(env):
    c, h = env["client"], env["h"]
    url = f"/api/motor-tests/{DIE}/{M1}"
    assert c.get(url, headers=h[CUSTOMER]).status_code == 404                    # flag off
    eng = c.get(url, headers=h[PROJ_ENG])
    assert eng.status_code == 200 and eng.json()["can"] == {"add": False, "edit": False, "settings": False}
    assert [d["name"] for d in eng.json()["documents"]] == ["drawing.pdf"]       # never an internal document
    assert c.post(url + "/tests", headers=h[PROJ_ENG], json=PARAM_TEST).status_code == 403
    MTS.mutate(DIE, M1, lambda d: d["settings"].update(tests_visible_to_customer=True))
    assert [t["id"] for t in c.get(url, headers=h[CUSTOMER]).json()["tests"]] == ["test_a"]
    assert c.get(url, headers=h[OUTSIDER]).status_code == 404
    assert c.get(url, headers=h[OEM]).json()["can"]["add"] is True                # the factory keeps its own rights


def test_unlinked_motor_outsider_and_removed_member_get_nothing(env):
    c, h = env["client"], env["h"]
    DS, motors = env["DS"], env["motors"]
    for path in ("", "/documents", "/threads"):
        assert c.get(f"/api/production/{DIE}/{OTHER}{path}", headers=h[CUSTOMER]).status_code == 404   # not linked
        assert c.get(_base(M1) + path, headers=h[OUTSIDER]).status_code == 404
    assert c.get(f"/api/production/{DIE}/{OTHER}/access", headers=h[CUSTOMER]).json()["access"] == "none"
    # unlink M1: the derived access is gone at once (nothing was written to the production)
    motors.unlink_motor(PROJECT, DIE, M1, actor=ADMIN)
    assert c.get(_base(M1), headers=h[CUSTOMER]).status_code == 404
    assert [p["config"] for p in c.get(f"/api/projects/{PROJECT}/productions", headers=h[CUSTOMER]).json()["productions"]] == [G1]
    assert c.get(_base(G1), headers=h[CUSTOMER]).status_code == 200
    # remove the customer from the project: G1 closes too
    DS.mutate_project(PROJECT, lambda d: d.update(members=[m for m in d["members"] if m["email"] != CUSTOMER]))
    assert c.get(_base(G1), headers=h[CUSTOMER]).status_code == 404
    assert (DIE, G1) not in {(p["die"], p["config"]) for p in c.get("/api/production", headers=h[CUSTOMER]).json()["productions"]}
    # the production members are untouched
    assert c.get(_base(M1), headers=h[OEM]).status_code == 200 and c.get(_base(M1), headers=h[ADMIN]).status_code == 200


def test_a_stale_index_row_grants_nothing(env):
    c, h = env["client"], env["h"]
    DS, motors = env["DS"], env["motors"]
    # the index still names the project, but the project no longer lists the motor
    DS.mutate_project(PROJECT, lambda d: d.update(linked_motors=[m for m in d["linked_motors"] if m["config"] != M1]))
    assert c.get(_base(M1), headers=h[CUSTOMER]).status_code == 404


# ── bench-test duties stay out of the project results ─────────────────────────────────────

def test_test_duties_are_not_rows_of_the_project_results():
    from motor_ai_sim.development import motors
    cfg = {"duties": [{"name": "rated", "rpm": 3000, "current_arms": 100, "summary": {"T_em_avg_Nm": 10.0}},
                      {"name": "test 1000 rpm 50 A", "kind": "test", "rpm": 1000, "current_arms": 50,
                       "summary": {"T_em_avg_Nm": 4.0}},
                      {"name": "noload_3021", "rpm": 3021, "current_arms": 0.1}]}          # legacy unflagged bench point
    rows = motors._results("die", "cfg", cfg, {}, set())
    assert [r["duty"] for r in rows] == ["rated"]
    assert [r["duty"] for r in motors._results("die", "cfg", cfg, {}, set(), own_only=True)] == ["rated"]

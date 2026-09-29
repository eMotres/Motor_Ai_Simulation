"""Data protection fixes of 2026-09-29 (docs/DATA_PROTECTION_2026-09-29.md).

1. fail-closed workspace resolution, cross-workspace reads, path traversal
   (``..``, absolute, encoded, double-encoded, backslash, symlink), admin-only
   routes;
2. account deletion (re-auth, grace period, cancel, purge of every store,
   backup re-apply) and export (signed single-use link, ZIP content);
3. admin audit + break-glass visibility for the subject;
4. retention;
5. file modes;
6. log redaction.

Everything runs against tmp copies; the real config/users.json is untouched.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"

ADMIN = "admin@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
PW = "correct horse battery 42"
DIE = "PRIVATEDIE 40"
CFG = "L40"

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _real_users_untouched():
    before = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    yield
    after = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    assert before == after, "config/users.json was modified by a test"


def _write_die(dies: Path, die: str = DIE, marker: str = "secret-of-bob") -> Path:
    d = dies / die
    d.mkdir(parents=True, exist_ok=True)
    (d / "die.yaml").write_text(yaml.safe_dump({
        "name": die, "locked": False, "created": "2026-09-29T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0}}), encoding="utf-8")
    (d / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": die, "role": "motor", "note": marker,
        "duties": [{"name": "rated", "mode": "motor", "current_arms": 10.0,
                    "rpm": 1000.0, "gamma_deg": 0.0}]}), encoding="utf-8")
    return d


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import shutil

    from motor_ai_sim import agent_keys as K  # noqa: F401 — path follows users
    from motor_ai_sim import auth
    from motor_ai_sim import log_redaction as LR
    from motor_ai_sim import newsletter as N
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam

    tree = tmp_path / "srv"
    shared, published, works, ident = (tree / "shared", tree / "published",
                                        tree / "workspaces", tree / "identity")
    for d in (shared / "dies", published, works, ident):
        d.mkdir(parents=True)
    shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")

    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    monkeypatch.setenv("SUPPORT_STORE_DIR", str(tree / "support"))
    monkeypatch.setenv("CLUSTER_MONITOR_DIR", str(tree / "cluster"))
    monkeypatch.setenv("MOTOR_AI_SIM_LOG_DIR", str(tree / "logs"))
    monkeypatch.setenv("CATALOG_GRANT_ALL_REGISTERED", "1")
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()

    users_file = ident / "users.json"
    users_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(S, "_SESSIONS_FILE", ident / ".sessions.json")
    monkeypatch.setattr(S, "_EVENTS_FILE", tree / "logs" / "auth_events.jsonl")
    monkeypatch.setattr(N, "_STORE_FILE", ident / "newsletter.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    LR.reset_salt_cache()
    U.create_user(ADMIN, "password-admin-1", role="admin", name="Admin")
    U.create_user(ALICE, PW, role="user", name="Alice")
    U.create_user(BOB, PW, role="user", name="Bob")

    from motor_ai_sim import workspace as W

    def login(email):
        r = client.post("/api/auth/login", json={"email": email, "password":
                        "password-admin-1" if email == ADMIN else PW})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    yield {"tree": tree, "works": works, "ident": ident, "published": published,
           "login": login, "ws_of": lambda e: works / W.workspace_id(e)}
    fam._TREE_CACHE.clear()
    LR.reset_salt_cache()


# ═════════════════════════════════════════════════════════════════════════════
# 1. fail closed
# ═════════════════════════════════════════════════════════════════════════════

def test_resolver_exception_is_refused_never_the_owners_workspace(monkeypatch, tmp_path):
    from motor_ai_sim import auth
    from motor_ai_sim import workspace as W
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))

    def boom(authorization=None):
        raise RuntimeError("users.json locked")
    monkeypatch.setattr(auth, "caller_identity", boom)
    with pytest.raises(W.WorkspaceResolutionError) as ei:
        W.workspace_for_request("Bearer x")
    assert ei.value.status == 500


def test_store_unavailable_is_503_not_the_process_workspace(monkeypatch, tmp_path):
    from motor_ai_sim import auth
    from motor_ai_sim import workspace as W
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    monkeypatch.setattr(auth, "caller_identity",
                        lambda authorization=None: {"id": auth.ANON_OWNER})
    monkeypatch.setattr(auth, "resolve_user_detail",
                        lambda authorization, **kw: {"user": None,
                                                     "reason": "store_unavailable"})
    with pytest.raises(W.WorkspaceResolutionError) as ei:
        W.workspace_for_request("Bearer something")
    assert ei.value.status == 503


def test_single_user_install_is_unchanged(monkeypatch):
    from motor_ai_sim import workspace as W
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    assert W.workspace_for_request("Bearer garbage") is W.process_workspace()


def test_bad_token_gets_401_and_open_paths_get_the_quarantine(env):
    bad = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.forgedsig"}
    r = client.get("/api/config", headers=bad)
    assert r.status_code == 401
    # /api/me must still answer so the client learns the token was rejected
    me = client.get("/api/me", headers=bad)
    assert me.status_code == 200 and me.json()["tokenRejected"] is True


def test_quarantine_workspace_is_not_the_process_one(env):
    from motor_ai_sim import workspace as W
    q = W.quarantine_workspace()
    assert q.id == W.QUARANTINE_WS_ID and not q.is_process
    assert q.root != W.process_workspace().root and not q.root.exists()


def test_alice_cannot_read_bobs_workspace_die(env):
    a, b = env["login"](ALICE), env["login"](BOB)
    _write_die(env["ws_of"](BOB) / "dies")
    ok = client.get(f"/api/family/duty_results/{DIE}/{CFG}", headers=b)
    assert ok.status_code == 200, ok.text
    r = client.get(f"/api/family/duty_results/{DIE}/{CFG}", headers=a)
    assert r.status_code in (403, 404)
    assert "secret-of-bob" not in r.text


# ── path traversal ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,query", [
    ("/api/family/duty_results/..%2F..%2Fidentity/L40", ""),
    ("/api/family/duty_results/%2e%2e/L40", ""),
    ("/api/family/duty_results/%252e%252e%252fusers/L40", ""),      # double-encoded
    ("/api/family/duty_results/a%5C..%5Cb/L40", ""),               # backslash
    ("/api/family/duty_results/x%00y/L40", ""),                    # NUL
    ("/api/family/history/x", "config=../../users"),
    ("/api/family/history/x", "config=%2e%2e%2fusers"),
    ("/api/catalog/cards/device/card", "id=../../identity/users"),
    ("/api/catalog/cards/device/card", "id=/etc/passwd"),
    ("/api/catalog/cards/device/card", "id=C:%5CWindows%5Cwin.ini"),
    ("/api/controller/devices/..%2F..%2Fusers", ""),
    ("/api/family/report/x/y", "duty=..%2F..%2Fsecret"),
])
def test_traversal_variants_are_refused_before_routing(env, path, query):
    h = env["login"](ALICE)
    url = path + (("?" + query) if query else "")
    r = client.get(url, headers=h)
    assert r.status_code == 400, (url, r.status_code, r.text)
    assert "rejected" in r.json()["detail"]


def test_request_problem_unit_cases():
    from motor_ai_sim.safe_paths import request_problem
    assert request_problem("/api/family/duty_results/CIANO 40/L40", "") is None
    assert request_problem("/api/x", "geo=%7B%22a%22%3A1%7D") is None
    assert request_problem("/api/x", "name=L180.v2") is None
    assert request_problem("/api/a/%2E%2E/b", "")
    assert request_problem("/api/a/b", "file=..%5Cx")
    assert request_problem("/api/a/b", "path=~root")


def test_check_segment_and_safe_join(tmp_path):
    from motor_ai_sim.safe_paths import PathRejected, check_segment, safe_join
    for bad in ("..", ".", "a/b", "a\\b", "C:x", "~", "%2e%2e", "%252e%252e", "x\x00"):
        with pytest.raises(PathRejected):
            check_segment(bad)
    assert safe_join(tmp_path, "ok.yaml") == tmp_path / "ok.yaml"
    with pytest.raises(PathRejected):
        safe_join(tmp_path, "..", "x")


def test_device_card_reader_cannot_leave_its_folder(tmp_path, monkeypatch):
    from motor_ai_sim.inverter import devices as D
    monkeypatch.setattr(D, "devices_dir", lambda: tmp_path / "devices")
    (tmp_path / "devices").mkdir()
    (tmp_path / "users.yaml").write_text("secret: 1\n", encoding="utf-8")
    with pytest.raises(D.CardError):
        D.get_device("../users")
    from motor_ai_sim.catalog import devices as CD
    assert CD.device_envelope("../users") is None


def _can_symlink(tmp_path) -> bool:
    try:
        (tmp_path / "l").symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / "l").unlink()
        return True
    except (OSError, NotImplementedError):
        return False


def test_symlinked_die_into_another_workspace_is_not_followed(env, tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("symlinks not permitted on this host")
    a = env["login"](ALICE)
    env["login"](BOB)
    bob_die = _write_die(env["ws_of"](BOB) / "dies")
    alice_dies = env["ws_of"](ALICE) / "dies"
    alice_dies.mkdir(parents=True, exist_ok=True)
    (alice_dies / DIE).symlink_to(bob_die, target_is_directory=True)
    r = client.get(f"/api/family/duty_results/{DIE}/{CFG}", headers=a)
    assert r.status_code in (403, 404, 422), r.text
    assert "secret-of-bob" not in r.text


@pytest.mark.parametrize("method,url", [
    ("get", "/api/admin/audit"),
    ("get", "/api/admin/accounts/deletions"),
    ("delete", f"/api/admin/accounts/{BOB}"),
    ("post", f"/api/admin/accounts/{BOB}/export"),
    ("delete", f"/api/auth/users/{BOB}"),
])
def test_admin_only_paths(env, method, url):
    a = env["login"](ALICE)
    assert getattr(client, method)(url, headers=a).status_code == 403
    assert getattr(client, method)(url).status_code == 401
    from motor_ai_sim import users as U
    assert U.get_user(BOB) is not None


# ═════════════════════════════════════════════════════════════════════════════
# 2. deletion + export
# ═════════════════════════════════════════════════════════════════════════════

def _seed_person(env, email: str) -> dict:
    """Give `email` something in every store purge must reach."""
    from motor_ai_sim import agent_keys as K
    from motor_ai_sim import newsletter as N
    from motor_ai_sim import support_store as SS
    from motor_ai_sim import usage_stats as US
    h = env["login"](email)
    client.get("/api/config", headers=h)                 # provisions the workspace
    _write_die(env["ws_of"](email) / "dies", marker="mine")
    pub = env["published"] / env["ws_of"](email).name
    pub.mkdir(parents=True, exist_ok=True)
    (pub / "x.txt").write_text("published", encoding="utf-8")
    K.create_key(email, "k")
    K.audit(principal=K.Principal(email=email, credential_id="c1"), method="tools/list")
    N.record_pending(email, source="settings", ip="203.0.113.9")
    SS.file_access_request(email=email, name="N", ip="203.0.113.9")
    US.note(email, "catalog_view", "x")
    return h


def test_self_deletion_needs_reauth_then_grace_then_purge(env):
    from motor_ai_sim import account_lifecycle as AL
    from motor_ai_sim import users as U
    h = _seed_person(env, ALICE)
    assert client.post("/api/account/deletion", headers=h, json={}).status_code == 403
    assert client.post("/api/account/deletion", headers=h,
                       json={"password": "wrong password!!"}).status_code == 403
    r = client.post("/api/account/deletion", headers=h, json={"password": PW})
    assert r.status_code == 200, r.text
    due = r.json()["pending"]["due_at"]
    assert due - time.time() == pytest.approx(7 * 86400, abs=120)
    # cancel, then again
    assert client.delete("/api/account/deletion", headers=h).json()["cancelled"] is True
    client.post("/api/account/deletion", headers=h, json={"password": PW})
    assert AL.run_due(time.time() + 86400) == []           # still in grace
    assert U.get_user(ALICE) is not None
    purged = AL.run_due(time.time() + 8 * 86400)
    assert purged == [AL.subject_hash(ALICE)]
    _assert_gone(env, ALICE)


def _assert_gone(env, email):
    from motor_ai_sim import account_lifecycle as AL
    from motor_ai_sim import agent_keys as K
    from motor_ai_sim import newsletter as N
    from motor_ai_sim import sessions as S
    from motor_ai_sim import support_store as SS
    from motor_ai_sim import users as U
    subj = AL.subject_hash(email)
    assert U.get_user(email) is None
    assert not env["ws_of"](email).exists()
    assert not (env["published"] / env["ws_of"](email).name).exists()
    assert S.list_all(email) == []
    assert K.list_keys(email) == []
    assert email not in N._load()["subscribers"]
    assert not [r for r in SS.list_access_requests() if r["email"] == email]
    for p in (S._EVENTS_FILE, K.audit_path()):
        if p.is_file():
            txt = p.read_text(encoding="utf-8")
            assert email not in txt, p
    assert any(r["subject"] == subj for r in AL.deleted_subjects())
    # bob untouched
    assert U.get_user(BOB) is not None


def test_admin_delete_purges_immediately_and_is_audited(env):
    from motor_ai_sim import admin_audit as AA
    _seed_person(env, ALICE)
    adm = env["login"](ADMIN)
    r = client.delete(f"/api/auth/users/{ALICE}", headers=adm)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    _assert_gone(env, ALICE)
    rows = AA.read(50, action="user.delete")
    assert rows and all(ALICE not in json.dumps(x) for x in rows)


def test_reapply_after_restore_purges_again(env):
    import shutil
    from motor_ai_sim import account_lifecycle as AL
    from motor_ai_sim import users as U
    _seed_person(env, ALICE)
    users_backup = env["ident"].joinpath("users.json").read_bytes()
    ws_backup = env["tree"] / "ws_backup"
    shutil.copytree(env["ws_of"](ALICE), ws_backup)
    AL.purge(ALICE, actor="admin@example.com", reason="test")
    # "restore" the old snapshot
    env["ident"].joinpath("users.json").write_bytes(users_backup)
    shutil.copytree(ws_backup, env["ws_of"](ALICE))
    assert U.get_user(ALICE) is not None
    rep = AL.reapply_deletions()
    assert rep["accounts_purged"] == 1
    assert U.get_user(ALICE) is None and not env["ws_of"](ALICE).exists()
    assert "alice" not in AL.register_file().read_text(encoding="utf-8")


def test_export_is_a_signed_single_use_link(env):
    h = _seed_person(env, ALICE)
    r = client.post("/api/account/export", headers=h)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    got = client.get(url)                                  # no session needed
    assert got.status_code == 200, got.text
    z = zipfile.ZipFile(io.BytesIO(got.content))
    names = set(z.namelist())
    assert {"account.json", "consents.json", "sessions.json", "agent_keys.json",
            "support_requests.json", "admin_access.json"} <= names
    assert any(n.startswith("workspace/dies/") for n in names)
    acct = json.loads(z.read("account.json"))
    assert acct["email"] == ALICE and not any(k.startswith("pw_") for k in acct)
    assert client.get(url).status_code == 403              # single use
    assert client.get(url[:-3] + "abc").status_code == 403  # tampered


# ═════════════════════════════════════════════════════════════════════════════
# 3. admin audit + break glass
# ═════════════════════════════════════════════════════════════════════════════

def test_admin_actions_are_logged_and_break_glass_is_visible_to_the_subject(env):
    a = env["login"](ALICE)
    adm = env["login"](ADMIN)
    client.get("/api/admin/sessions", headers=adm)
    client.post(f"/api/admin/users/{ALICE}/revoke_all", headers=adm)
    a = env["login"](ALICE)
    r = client.post(f"/api/admin/accounts/{ALICE}/export", headers=adm)
    assert r.status_code == 200
    log_rows = client.get("/api/admin/audit", headers=adm).json()["entries"]
    actions = [x["action"] for x in log_rows]
    assert "session.list" in actions and "session.revoke_all" in actions
    assert "workspace.read" in actions
    assert all(x["actor"] == ADMIN for x in log_rows)
    blob = json.dumps(log_rows)
    assert "Bearer" not in blob and "password" not in blob
    mine = client.get("/api/account/admin_access", headers=a).json()["entries"]
    assert any(x["break_glass"] and x["actor"] == ADMIN for x in mine)


def test_admin_audit_scrubs_secrets(tmp_path, monkeypatch):
    from motor_ai_sim import admin_audit as AA
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "a.jsonl"))
    rec = AA.record("x@y", "user.update", "t", details={"password": "p", "api_key": "k",
                                                        "role": "user"})
    assert rec["details"] == {"role": "user"}
    assert "unknown:" in AA.record("x", "made.up")["action"]


# ═════════════════════════════════════════════════════════════════════════════
# 4. retention
# ═════════════════════════════════════════════════════════════════════════════

def test_prune_jsonl_drops_old_keeps_new_and_undated(tmp_path):
    from motor_ai_sim.retention import prune_jsonl
    p = tmp_path / "e.jsonl"
    now = time.time()
    p.write_text("\n".join([json.dumps({"t": now - 200 * 86400, "e": "old"}),
                            json.dumps({"ts": "2020-01-01T00:00:00", "e": "old-iso"}),
                            json.dumps({"t": now, "e": "new"}),
                            json.dumps({"e": "undated"}), "not json"]) + "\n",
                 encoding="utf-8")
    assert prune_jsonl(p, now - 90 * 86400, dry_run=True) == 2
    assert prune_jsonl(p, now - 90 * 86400) == 2
    body = p.read_text(encoding="utf-8")
    assert "old" not in body and "new" in body and "undated" in body and "not json" in body


def test_retention_run_prunes_sessions_and_events(env, monkeypatch):
    from motor_ai_sim import retention as R
    from motor_ai_sim import sessions as S
    env["login"](ALICE)
    S.record_event("login", email=ALICE, ip="198.51.100.7")
    later = time.time() + 400 * 86400
    rep = R.run(later, dry_run=True)
    assert rep["steps"]["sessions"] >= 1 and rep["steps"]["auth_events"] >= 1
    assert S.list_all(ALICE)                               # dry run changed nothing
    rep = R.run(later)
    assert S.list_all(ALICE) == []
    assert not S.read_events(email=ALICE)


# ═════════════════════════════════════════════════════════════════════════════
# 5. file modes
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX modes")
def test_identity_files_are_0600_and_dirs_0700(env):
    import stat
    env["login"](ALICE)
    for p in (env["ident"] / "users.json", env["ident"] / ".sessions.json"):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p
    assert stat.S_IMODE(env["ws_of"](ALICE).stat().st_mode) == 0o700


def test_private_helpers_request_owner_only_modes(tmp_path, monkeypatch):
    from motor_ai_sim import private_files as PF
    seen = []
    real = os.chmod
    monkeypatch.setattr(PF.os, "chmod", lambda p, m: (seen.append((Path(p).name, m)),
                                                      real(p, m))[1])
    with PF.open_private(tmp_path / "f.json", "w") as f:
        f.write("{}")
    PF.ensure_private_dir(tmp_path / "d")
    assert ("f.json", 0o600) in seen and ("d", 0o700) in seen


# ═════════════════════════════════════════════════════════════════════════════
# 6. log redaction
# ═════════════════════════════════════════════════════════════════════════════

def test_redact_removes_credentials_and_hashes_ips(monkeypatch):
    from motor_ai_sim import log_redaction as LR
    monkeypatch.setenv("LOG_HASH_SALT", "s")
    LR.reset_salt_cache()
    jwt_ = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhQGIuYyJ9.c2lnbmF0dXJlLXZhbHVl"
    msg = (f"Authorization: Bearer abcdef123456 link https://x/?reset={jwt_} "
           "key emk_ab12_SECRETSECRET password=hunter22 "
           '{"password": "pw1", "tier": "pro"} from 203.0.113.9 and 2001:db8::1 '
           "at 12:34:56 local 127.0.0.1")
    out = LR.redact(msg)
    for leak in ("abcdef123456", jwt_, "SECRETSECRET", "hunter22", '"pw1"',
                 "203.0.113.9", "2001:db8::1"):
        assert leak not in out, leak
    assert "12:34:56" in out and "127.0.0.1" in out and '"tier": "pro"' in out
    assert LR.hash_ip("203.0.113.9") in out
    assert LR.hash_ip("203.0.113.9") == LR.hash_ip("203.0.113.9")
    LR.reset_salt_cache()


def test_redacting_filter_on_a_real_handler():
    from motor_ai_sim.log_redaction import RedactingFilter
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(RedactingFilter())
    lg = logging.getLogger("test.redaction")
    lg.addHandler(h)
    lg.propagate = False
    try:
        lg.warning("login for %s from %s token=%s", "a@b.c", "198.51.100.23", "tok123456")
    finally:
        lg.removeHandler(h)
    line = buf.getvalue()
    assert "198.51.100.23" not in line and "tok123456" not in line and "a@b.c" in line


def test_api_installs_the_filter_on_root_handlers():
    from motor_ai_sim import log_redaction as LR
    LR.install()
    for h in logging.getLogger().handlers:
        assert any(isinstance(f, LR.RedactingFilter) for f in h.filters)


def test_sessions_and_auth_events_store_hashed_ips(env):
    from motor_ai_sim import sessions as S
    S.create(ALICE, expires=time.time() + 60, ip="198.51.100.77")
    S.record_event("login", email=ALICE, ip="198.51.100.77")
    blob = (S._SESSIONS_FILE.read_text(encoding="utf-8")
            + S._EVENTS_FILE.read_text(encoding="utf-8"))
    assert "198.51.100.77" not in blob and "ip:" in blob

"""Mirror job, timers and US backups (docs/regions/DESIGN.md sections 7, 12; docs/regions/INSTALL_US_MIRROR.md):
the command-line round trip against a fake peer, the directory sync, the backup exclude list, and the shape of the files the
coordinator installs (units, timers, rsync module, restic lists, B2 env template).  No server, no network, no restic.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from motor_ai_sim import motor_access as MA
from motor_ai_sim.regions import catalog_manifest as CM
from motor_ai_sim.regions import catalog_mirror as MI
from motor_ai_sim.regions import directory_sync as DSY
from motor_ai_sim.regions import peer as P

ROOT = Path(__file__).resolve().parents[1]
SECRET = "s" * 40


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── the command line against a fake peer ─────────────────────────────────────

@pytest.fixture()
def world(tmp_path, monkeypatch):
    """EU tree + a US server whose peer calls are answered from the EU tree."""
    for k in ("MOTRES_REGION", "MOTRES_PEER_URL", "MOTRES_PEER_HMAC_SECRET", "MOTRES_PEER_REGION"):
        monkeypatch.delenv(k, raising=False)
    eu_access = tmp_path / "eu_die_access.json"
    eu_access.write_text(json.dumps({"PUB": {"visibility": "public"}}), encoding="utf-8")
    eu = tmp_path / "eu"
    for rel, text in {"dies/PUB/die.yaml": "name: PUB\n", "dies/PUB/L1.yaml": "name: L1\n", "dies/PRIV/die.yaml": "name: PRIV\n",
                      "dies/PRIV/C1.yaml": "name: C1\n", "materials_library.yaml": "steel: {}\n"}.items():
        (eu / rel).parent.mkdir(parents=True, exist_ok=True)
        (eu / rel).write_text(text, encoding="utf-8")
    (eu / ".duty_results.json").write_text(json.dumps({"results": {"PUB": {"L1": {"d": {"t": {"geometry_fingerprint": "fp1"}}}},
                                                                   "PRIV": {"C1": {"d": {"t": {}}}}}}), encoding="utf-8")
    (eu / "end_effect_passports.json").write_text(json.dumps({"fp1": {"k_flux": 0.9}, "fp2": {"k_flux": 0.8}}), encoding="utf-8")
    us = tmp_path / "us_shared"
    us.mkdir()
    work = tmp_path / "mirror"
    monkeypatch.setenv(MI.ENV_DIR, str(work))
    monkeypatch.setenv(MI.ENV_STATE, str(work / "state.json"))
    monkeypatch.setattr(MI, "shared_dir", lambda: us)
    monkeypatch.setenv("MOTRES_REGION", "us")
    monkeypatch.setenv("MOTRES_PEER_URL", "http://eu.invalid:8001")
    monkeypatch.setenv("MOTRES_PEER_HMAC_SECRET", SECRET)
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "us_die_access.json")

    def build():
        saved = MA._DIE_ACCESS_FILE
        MA._DIE_ACCESS_FILE = eu_access
        MA._CFG_VIS_CACHE.clear()
        try:
            return CM.build_all(eu, catalog_path=tmp_path / "nocatalog.json")
        finally:
            MA._DIE_ACCESS_FILE = saved
            MA._CFG_VIS_CACHE.clear()

    calls = []

    def fake_get(path, **kw):
        calls.append(path)
        man, ext = build()
        return json.loads(json.dumps(CM.public_manifest(man) if path == "/catalog/manifest" else ext))

    monkeypatch.setattr(P, "get", fake_get)
    return {"eu": eu, "us": us, "work": work, "calls": calls, "tmp": tmp_path}


def _rsync(w):
    for rel in (w["work"] / "fetch.txt").read_text(encoding="utf-8").split():
        dst = w["us"] / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(w["eu"] / rel, dst)


def test_plan_apply_round_trip_and_fetch_list(world, capsys):
    assert MI.main(["plan"]) == MI.EXIT_INIT                              # first run refuses
    assert world["calls"] == ["/catalog/manifest", "/catalog/extracts"]
    assert (world["work"] / "fetch.txt").read_text(encoding="utf-8") == ""
    assert MI.main(["plan", "--init"]) == MI.EXIT_OK
    fetch = (world["work"] / "fetch.txt").read_text(encoding="utf-8").split()
    assert fetch == ["dies/PUB/L1.yaml", "dies/PUB/die.yaml", "materials_library.yaml"]      # nothing of PRIV
    _rsync(world)
    assert MI.main(["apply"]) == MI.EXIT_OK
    assert json.loads((world["us"] / ".duty_results.json").read_text(encoding="utf-8"))["results"].keys() == {"PUB"}
    assert json.loads((world["us"] / "end_effect_passports.json").read_text(encoding="utf-8")).keys() == {"fp1"}
    assert MA.get_die_access("PUB")["visibility"] == "public"
    assert MI.main(["plan"]) == MI.EXIT_OK and (world["work"] / "fetch.txt").read_text(encoding="utf-8") == ""
    assert MI.main(["status"]) == MI.EXIT_OK
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["files"] == 3


def test_apply_without_a_plan_or_after_a_refused_plan_does_nothing(world):
    assert MI.main(["apply"]) == MI.EXIT_PEER
    MI.main(["plan"])                                                     # refused: first run
    assert MI.main(["apply"]) == MI.EXIT_INIT
    assert not (world["us"] / "dies").exists()


def test_local_edit_stops_the_whole_run(world):
    MI.main(["plan", "--init"])
    _rsync(world)
    MI.main(["apply"])
    (world["us"] / "materials_library.yaml").write_text("hacked\n", encoding="utf-8")
    (world["eu"] / "materials_library.yaml").write_text("steel: {x: 1}\n", encoding="utf-8")
    assert MI.main(["plan"]) == MI.EXIT_EDITS
    assert (world["work"] / "fetch.txt").read_text(encoding="utf-8") == ""
    assert MI.main(["apply"]) == MI.EXIT_EDITS
    assert (world["us"] / "materials_library.yaml").read_text(encoding="utf-8") == "hacked\n"


def test_peer_silence_is_exit_4_and_changes_nothing(world, monkeypatch):
    monkeypatch.setattr(P, "get", lambda path, **kw: None)
    assert MI.main(["plan", "--init"]) == MI.EXIT_PEER
    assert not (world["work"] / "plan.json").exists()


def test_extracts_unreachable_still_mirrors_the_files(world, monkeypatch):
    real = P.get
    monkeypatch.setattr(P, "get", lambda path, **kw: None if path == "/catalog/extracts" else real(path, **kw))
    assert MI.main(["plan", "--init"]) == MI.EXIT_OK
    _rsync(world)
    assert MI.main(["apply"]) == MI.EXIT_OK
    assert (world["us"] / "dies/PUB/die.yaml").is_file() and not (world["us"] / ".duty_results.json").exists()


def test_backup_exclude_lists_exactly_the_mirrored_files(world, tmp_path):
    MI.main(["plan", "--init"])
    _rsync(world)
    MI.main(["apply"])
    (world["us"] / "dies/CUSTOMER").mkdir(parents=True)
    (world["us"] / "dies/CUSTOMER/die.yaml").write_text("name: closed\n", encoding="utf-8")
    out = tmp_path / "excl.txt"
    assert MI.main(["backup-exclude", "--out", str(out)]) == MI.EXIT_OK
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    assert lines == sorted((world["us"] / r).as_posix() for r in ("dies/PUB/L1.yaml", "dies/PUB/die.yaml", "materials_library.yaml"))
    assert not any("CUSTOMER" in ln for ln in lines)                      # a closed motor of a US account stays in the backup
    assert MI._restic_escape("/a/b[1]*?.yaml") == "/a/b\\[1]\\*\\?.yaml"      # [ * ? and backslash are escaped


# ── directory sync ───────────────────────────────────────────────────────────

def test_directory_sync_stores_the_peer_entries_only(tmp_path, monkeypatch):
    from motor_ai_sim import users as U
    for k in ("MOTRES_REGION", "MOTRES_PEER_URL", "MOTRES_PEER_HMAC_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("MOTRES_REGION_DIRECTORY_FILE", str(tmp_path / "dir.json"))
    assert DSY.sync() == {"ok": False, "code": "single_region"} and DSY.main([]) == 6
    monkeypatch.setenv("MOTRES_REGION", "us")
    monkeypatch.setenv("MOTRES_PEER_URL", "http://eu.invalid:8001")
    monkeypatch.setenv("MOTRES_PEER_HMAC_SECRET", SECRET)
    monkeypatch.setattr(P, "get", lambda path, **kw: None)
    assert DSY.main([]) == 4 and not (tmp_path / "dir.json").exists()       # silent peer: the last sync stays
    monkeypatch.setattr(P, "get", lambda path, **kw: {"region": "eu", "entries": {
        "eu.person@example.com": {"id": "w1", "region": "eu", "tier": "user", "created": "2026-01-01", "name": "Not Stored"},
        "us.person@example.com": {"id": "w2", "region": "us", "tier": "user", "created": None}}})
    res = DSY.sync()
    assert res["ok"] and res["entries"] == 1                                # an entry of our own region is refused
    doc = json.loads((tmp_path / "dir.json").read_text(encoding="utf-8"))
    assert doc["entries"]["eu.person@example.com"].keys() == {"id", "region", "tier", "created"}
    assert DSY.main([]) == 0


# ── the files the coordinator installs ───────────────────────────────────────

def _unit(rel: str) -> dict:
    out, sect = {}, None
    for ln in _read(rel).splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = re.fullmatch(r"\[(\w+)\]", ln)
        if m:
            sect = m.group(1)
            continue
        k, _, v = ln.partition("=")
        out.setdefault((sect, k), []).append(v)
    return out


def test_timers_run_at_the_requested_intervals_and_point_at_their_service():
    mirror = _unit("deploy/systemd/motres-catalog-mirror.timer")
    assert mirror[("Timer", "OnCalendar")] == ["*:0/15"] and mirror[("Timer", "Unit")] == ["motres-catalog-mirror.service"]
    sync = _unit("deploy/systemd/motres-directory-sync.timer")
    assert sync[("Timer", "OnCalendar")] == ["*:0/5"] and sync[("Timer", "Unit")] == ["motres-directory-sync.service"]
    for t in (mirror, sync):
        assert t[("Timer", "Persistent")] == ["true"] and t[("Install", "WantedBy")] == ["timers.target"]
    svc = _unit("deploy/systemd/motres-catalog-mirror.service")
    assert svc[("Service", "ExecStart")] == ["/bin/bash /opt/motres/app/scripts/mirror_catalog_us.sh"]
    assert svc[("Service", "SuccessExitStatus")] == ["75"] and (ROOT / "scripts/mirror_catalog_us.sh").is_file()
    d = _unit("deploy/systemd/motres-directory-sync.service")
    assert "motor_ai_sim.regions.directory_sync" in d[("Service", "ExecStart")][0]
    for u in (svc, d):
        assert u[("Unit", "ConditionPathExists")] == ["/etc/motres/region-us"]     # never runs on the EU server by mistake


def test_mirror_script_is_a_safe_plan_rsync_apply_chain():
    sh = _read("scripts/mirror_catalog_us.sh")
    assert "ssh" not in re.sub(r"#.*", "", sh).replace("flock", "")                  # rsync daemon over WireGuard, no shell on EU
    assert "--files-from" in sh and "--delete" not in re.sub(r"#.*", "", sh) and "10.77.0.1" in sh
    code = re.sub(r"#.*", "", sh)
    assert code.index("}\" plan") < code.index("--files-from") < code.index("}\" apply")
    assert "flock -n" in sh and "exit 2" in sh and "exit 3" in sh
    bash = shutil.which("bash")
    if bash:
        r = subprocess.run([bash, "-n", str(ROOT / "scripts/mirror_catalog_us.sh")], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_rsync_module_is_read_only_unprivileged_wireguard_only_and_filtered_by_the_generated_list():
    conf = _read("deploy/regions/rsyncd-motres.conf")
    assert "read only = yes" in conf and "address = 10.77.0.1" in conf and "hosts allow = 10.77.0.2" in conf
    assert "hosts deny = *" in conf and "exclude = *" in conf and "delete" in conf
    assert re.search(r"^uid = 10001$", conf, re.M) and re.search(r"^gid = 10001$", conf, re.M)     # B2: not root
    assert not re.search(r"^uid = (root|0)$", conf, re.M)
    assert "include from = /srv/motres/identity/mirror/rsync-library.include" in conf                 # generated, not a static tree
    assert not (ROOT / "deploy/regions/rsyncd-motres.include").exists()                              # no static allow-list of whole trees
    svc = _read("deploy/systemd/motres-rsyncd-library.service")
    assert "--config=/etc/motres/rsyncd-motres.conf" in svc


def _rsync_visible(rules, path, is_dir):
    """What rsync's daemon filter does with ``include from`` followed by ``exclude = *``: the first matching anchored rule
    wins; a rule ending in / matches directories only; a path nothing includes is excluded."""
    for r in rules:
        want_dir = r.endswith("/")
        pat = re.sub(r"\\(.)", lambda m: "\x00" + m.group(1), r.rstrip("/"))      # escaped characters are literal
        pat = pat.lstrip("/")
        if want_dir and not is_dir:
            continue
        if pat.replace("\x00", "") == path:
            return True
    return False


def test_the_generated_filter_shows_exactly_the_manifest_files_and_hides_closed_dies(tmp_path):
    """B2: with the generated include list + ``exclude = *`` a closed die's folder is not even listable."""
    eu = tmp_path / "eu"
    for rel in ("dies/PUB/die.yaml", "dies/PUB/L1.yaml", "dies/PRIV/die.yaml", "dies/PRIV/C1.yaml", "passports/PRIV/C1.json",
                "cad/PRIV/C1/manifest.json", "passports/PUB/L1.json", "materials_library.yaml", "projects/p1/project.json"):
        (eu / rel).parent.mkdir(parents=True, exist_ok=True)
        (eu / rel).write_text("x: 1\n", encoding="utf-8")
    (eu / "passports/PUB/L1.json").write_text("{}", encoding="utf-8")
    eu_access = tmp_path / "eu_access.json"
    eu_access.write_text(json.dumps({"PUB": {"visibility": "public"}}), encoding="utf-8")
    saved = MA._DIE_ACCESS_FILE
    MA._DIE_ACCESS_FILE = eu_access
    MA._CFG_VIS_CACHE.clear()
    try:
        man = CM.build(eu, catalog_path=tmp_path / "none.json")
    finally:
        MA._DIE_ACCESS_FILE = saved
        MA._CFG_VIS_CACHE.clear()
    out = tmp_path / "mirror" / "rsync-library.include"
    n = CM.write_rsync_filter(man, out)
    rules = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")]
    assert n == len(rules) and (out.stat().st_mode & 0o044) in (0o044, 0)                 # readable by the unprivileged daemon
    on_disk = {p.relative_to(eu).as_posix(): p.is_dir() for p in eu.rglob("*")}
    shown = {p for p, d in on_disk.items() if _rsync_visible(rules, p, d)}
    files = {p for p in shown if not on_disk[p]}
    assert files == set(man["files"]) and "dies/PUB/die.yaml" in files and "materials_library.yaml" in files
    assert not any(p.startswith(("dies/PRIV", "passports/PRIV", "cad/PRIV", "projects")) for p in shown)
    assert {p for p in shown if on_disk[p]} == {"dies", "dies/PUB", "passports", "passports/PUB"}      # only parents of shown files
    assert CM.rsync_filter_rules({"files": {"dies/a[1]/x*.yaml": {}}}) == ["/dies/", "/dies/a\\[1]/", "/dies/a\\[1]/x\\*.yaml"]
    if shutil.which("rsync"):                                                           # the real thing, where it is installed
        r = subprocess.run(["rsync", "-r", "--list-only", "--include-from=" + str(out), "--exclude=*", str(eu) + "/"],
                           capture_output=True, text=True)
        listed = r.stdout
        assert "materials_library.yaml" in listed and "PRIV" not in listed and "projects" not in listed


def test_manifest_route_serves_counts_only_and_writes_the_filter(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    c = TestClient(app)
    P.reset_nonces()
    for k in ("MOTRES_REGION", "MOTRES_PEER_URL", "MOTRES_PEER_HMAC_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MOTRES_REGION", "eu")
    monkeypatch.setenv("MOTRES_PEER_URL", "http://us.invalid:8001")
    monkeypatch.setenv("MOTRES_PEER_HMAC_SECRET", SECRET)
    flt = tmp_path / "mirror" / "rsync-library.include"
    monkeypatch.setenv("MOTRES_RSYNC_FILTER_FILE", str(flt))
    full = {"root_id": "r", "files": {"dies/PUB/die.yaml": {"sha256": "a" * 64, "bytes": 1}}, "count": 1, "bytes": 1,
            "excluded_dies": [{"die": "SECRET_CLOSED_DIE", "why": "marked as customer data"}],
            "excluded_files": [{"path": "dies/PUB/SECRET_CLOSED_CFG.yaml", "why": "x"}], "excluded_files_total": 1,
            "skipped_unreadable": 1, "skipped_unreadable_paths": ["dies/PUB/secret_name.bak"], "exhibit_source": "workspace", "ts": 1.0}
    monkeypatch.setattr(CM, "build", lambda *a, **k: full)
    path = P.PREFIX + "/catalog/manifest"
    h = P.sign_headers("GET", path, b"", secret=SECRET, region="us")
    r = c.get(path, headers=h)
    assert r.status_code == 200
    blob = r.text
    assert "SECRET_CLOSED" not in blob and "secret_name" not in blob
    body = r.json()
    assert body["excluded_dies_count"] == 1 and body["excluded_die_hmacs"] == [CM.die_hmac("SECRET_CLOSED_DIE", SECRET)]
    assert "excluded_dies" not in body and "excluded_files" not in body
    assert flt.read_text(encoding="utf-8").splitlines()[1:] == ["/dies/", "/dies/PUB/", "/dies/PUB/die.yaml"]
    # a filter that cannot be written is never served around: no list the module cannot enforce
    P.reset_nonces()
    (tmp_path / "blocker").write_text("a file, not a folder", encoding="utf-8")
    monkeypatch.setenv("MOTRES_RSYNC_FILTER_FILE", str(tmp_path / "blocker" / "x" / "rsync-library.include"))
    h = P.sign_headers("GET", path, b"", secret=SECRET, region="us")
    assert c.get(path, headers=h).status_code == 503


def test_us_backup_targets_b2_with_credentials_only_in_the_root_env_file():
    env = _read("deploy/backup/backup-us.env.example")
    vals = dict(ln.split("=", 1) for ln in env.splitlines() if re.match(r"[A-Z0-9_]+=", ln))
    assert vals["RESTIC_REPOSITORY"].startswith("b2:")
    for secret in ("B2_ACCOUNT_ID", "B2_ACCOUNT_KEY", "RESTIC_PASSWORD"):
        assert vals[secret] == ""                                                # template: nothing filled in
    assert vals["LIMIT_UPLOAD_KIB"].isdigit() and "RESTIC_LIMIT_UPLOAD" not in env     # L9: restic reads no such variable
    for name in ("motres-backup-us", "motres-backup-us-prune"):
        svc = _unit(f"deploy/systemd/{name}.service")
        assert svc[("Service", "EnvironmentFile")] == ["/etc/motres/backup-us.env"]
        text = re.sub(r"#.*", "", _read(f"deploy/systemd/{name}.service"))
        assert "B2_ACCOUNT" not in text and "RESTIC_PASSWORD" not in text
        assert "--limit-upload ${LIMIT_UPLOAD_KIB}" in text                      # the flag restic really has
    text = _read("deploy/systemd/motres-backup-us.service")
    assert "--host motres-us" in text and "restic-include-us.txt" in text and "motres-box" not in text
    assert "0600" in env and "/etc/motres/backup-us.env" in env
    assert _unit("deploy/systemd/motres-backup-us.timer")[("Timer", "OnCalendar")] == ["hourly"]


def test_prune_and_check_run_daily_not_hourly():
    hourly = re.sub(r"#.*", "", _read("deploy/systemd/motres-backup-us.service"))
    assert "forget" not in hourly and "prune" not in hourly and "restic check" not in hourly
    daily = _unit("deploy/systemd/motres-backup-us-prune.service")
    cmds = daily[("Service", "ExecStart")]
    assert any(c.startswith("/usr/bin/restic forget --prune") and "--keep-daily 30" in c for c in cmds)
    assert any(c.startswith("/usr/bin/restic check") for c in cmds)
    t = _unit("deploy/systemd/motres-backup-us-prune.timer")
    assert t[("Timer", "OnCalendar")] == ["*-*-* 03:30:00 UTC"] and t[("Timer", "Unit")] == ["motres-backup-us-prune.service"]


def test_backup_unit_never_creates_the_mirror_directory_as_root():
    text = re.sub(r"#.*", "", _read("deploy/systemd/motres-backup-us.service"))
    assert "install -d -o 10001 -g 10001 -m 0750 /srv/motres/identity/mirror" in text
    assert "touch " not in text and "install -d -m" not in text                        # the old root-owned forms
    assert "-o 10001 -g 10001 -m 0644 /dev/null" in text                                # the fallback file is the app user's


def test_us_backup_lists_cut_out_the_library_and_foreign_jobs():
    inc = [ln.strip() for ln in _read("deploy/backup/restic-include-us.txt").splitlines() if ln.strip() and not ln.startswith("#")]
    assert "/srv/motres/identity" in inc and "/srv/motres/workspaces" in inc and "/etc/motres" in inc
    assert not any("foreign" in i or i.rstrip("/").endswith("/run") for i in inc)
    exc = _read("deploy/backup/restic-exclude-us.txt") + _read("deploy/backup/restic-exclude.txt")
    assert "foreign_jobs/" in exc
    svc = _read("deploy/systemd/motres-backup-us.service")
    assert "restic-exclude-mirrored.txt" in svc and "backup-exclude" in svc          # the mirrored files are excluded
    assert "motres-box" not in svc                                                   # never the EU Storage Box


def _run_script(tmp_path, *, plan_rc=0, apply_rc=0, rsync_rc=0, fetch="dies/PUB/die.yaml\n"):
    """Run scripts/mirror_catalog_us.sh under bash with fake install / chown / flock / rsync / bluegreen: record what it does."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    bindir, work, log = tmp_path / "bin", tmp_path / "work", tmp_path / "calls.log"
    bindir.mkdir(parents=True)
    work.mkdir(parents=True)

    def tool(name, body):
        f = bindir / name
        f.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        f.chmod(0o755)
        return f
    tool("install", f'echo "install $*" >> "{log.as_posix()}"\n')
    tool("chown", f'echo "chown $*" >> "{log.as_posix()}"\n')
    tool("flock", "exit 0\n")
    rs = tool("rsync", f'echo "rsync $*" >> "{log.as_posix()}"\nexit {rsync_rc}\n')
    bg = tool("bg.sh", f'echo "bluegreen $*" >> "{log.as_posix()}"\n'
                       f'case "$*" in *" plan"*) printf "{fetch}" > "{work.as_posix()}/fetch.txt"; exit {plan_rc};; '
                       f'*" apply"*) exit {apply_rc};; esac\n')
    env = {**os.environ, "PATH": bindir.as_posix() + os.pathsep + os.environ["PATH"], "MOTRES_MIRROR_DIR": work.as_posix(),
           "MOTRES_SHARED": (tmp_path / "shared").as_posix(), "RSYNC": rs.as_posix(), "MOTRES_BLUEGREEN": bg.as_posix()}
    r = subprocess.run([bash, str(ROOT / "scripts/mirror_catalog_us.sh")], capture_output=True, text=True, env=env, timeout=60)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return r.returncode, calls, r.stdout + r.stderr


def test_script_fetches_as_the_app_user_and_never_creates_root_files(tmp_path):
    """B3: the work dir is created for 10001, rsync is told to chown / chmod, and nothing recursive or deleting is passed."""
    rc, calls, out = _run_script(tmp_path)
    assert rc == 0, out
    assert any(c.startswith("install -d -o 10001 -g 10001 -m 0750 ") and c.endswith("/work") for c in calls)
    assert any(c.startswith("chown 10001:10001 ") and c.endswith(".lock") for c in calls)
    (rs,) = [c for c in calls if c.startswith("rsync ")]
    assert "--chown=10001:10001" in rs and "--chmod=D755,F644" in rs and "--files-from=" in rs
    assert not re.search(r"(^| )-[a-zA-Z]*[ra]", rs.split("--")[0].replace("rsync", "", 1)) and "--delete" not in rs
    assert rs.index("rsync ") < rs.index("--chown")
    assert [c for c in calls if c.startswith("bluegreen")][-1].endswith(" apply")


def test_script_exit_codes_failed_delete_is_non_zero_and_refusals_stop_before_rsync(tmp_path):
    rc, calls, out = _run_script(tmp_path, apply_rc=7)
    assert rc == 7 and "FAILED" in out                                                   # a failed delete / corrupt store: the unit fails
    for plan_rc, want in ((2, 2), (3, 3), (4, 75), (6, 6)):
        rc, calls, out = _run_script(tmp_path / f"p{plan_rc}", plan_rc=plan_rc)
        assert rc == want and not any(c.startswith("rsync ") for c in calls) and not any(c.endswith(" apply") for c in calls)
    rc, calls, out = _run_script(tmp_path / "rs", rsync_rc=23)
    assert rc == 75 and not any(c.endswith(" apply") for c in calls)                    # rsync failed: apply never runs
    rc, calls, out = _run_script(tmp_path / "empty", fetch="")
    assert rc == 0 and not any(c.startswith("rsync ") for c in calls)                  # nothing to fetch: no rsync at all


def test_install_note_lists_the_owner_variables_and_every_step():
    note = _read("docs/regions/INSTALL_US_MIRROR.md")
    for needle in ("B2_ACCOUNT_ID", "B2_ACCOUNT_KEY", "RESTIC_PASSWORD", "RESTIC_REPOSITORY", "LIMIT_UPLOAD_KIB", "backup-us.env",
                   "--init", "motres-catalog-mirror.timer", "motres-directory-sync.timer", "motres-rsyncd-library",
                   "motres-backup-us-prune", "MOTRES_CROSS_REGION_COMPUTE", "/srv/motres/run/foreign_jobs", "region-us",
                   "Acceptance check", "10001"):
        assert needle in note, needle
    assert "sudo bash -c 'set -a; . /etc/motres/backup-us.env; set +a; restic init'" in note       # L9: sourced as root
    assert "sudo -E restic init" not in note and "rsyncd-motres.include" not in note

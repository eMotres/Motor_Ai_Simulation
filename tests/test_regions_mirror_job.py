"""Mirror job, timers and US backups (docs/regions/DESIGN.md sections 7, 12; docs/regions/INSTALL_US_MIRROR.md):
the command-line round trip against a fake peer, the directory sync, the backup exclude list, and the shape of the files the
coordinator installs (units, timers, rsync module, restic lists, B2 env template).  No server, no network, no restic.
"""
from __future__ import annotations

import json
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
        return json.loads(json.dumps(man if path == "/catalog/manifest" else ext))

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


def test_rsync_module_is_read_only_wireguard_only_and_matches_the_allow_list():
    conf = _read("deploy/regions/rsyncd-motres.conf")
    assert "read only = yes" in conf and "address = 10.77.0.1" in conf and "hosts allow = 10.77.0.2" in conf
    assert "hosts deny = *" in conf and "list = no" in conf and "exclude = *" in conf and "delete" in conf
    inc = {ln.strip() for ln in _read("deploy/regions/rsyncd-motres.include").splitlines() if ln.strip() and not ln.startswith("#")}
    assert inc == {f"/{d}/***" for d in CM.LIBRARY_DIRS} | {f"/{f}" for f in CM.LIBRARY_FILES}
    assert not any(r in " ".join(inc) for r in CM.REGION_OWNED)


def test_us_backup_targets_b2_with_credentials_only_in_the_root_env_file():
    env = _read("deploy/backup/backup-us.env.example")
    vals = dict(ln.split("=", 1) for ln in env.splitlines() if re.match(r"[A-Z0-9_]+=", ln))
    assert vals["RESTIC_REPOSITORY"].startswith("b2:")
    for secret in ("B2_ACCOUNT_ID", "B2_ACCOUNT_KEY", "RESTIC_PASSWORD"):
        assert vals[secret] == ""                                                # template: nothing filled in
    svc = _unit("deploy/systemd/motres-backup-us.service")
    assert svc[("Service", "EnvironmentFile")] == ["/etc/motres/backup-us.env"]
    text = _read("deploy/systemd/motres-backup-us.service")
    assert "B2_ACCOUNT" not in re.sub(r"#.*", "", text) and "RESTIC_PASSWORD" not in re.sub(r"#.*", "", text)
    assert "--host motres-us" in text and "restic-include-us.txt" in text and "hetzner" not in text.lower().replace("not the eu", "")
    assert "0600" in env and "/etc/motres/backup-us.env" in env
    assert _unit("deploy/systemd/motres-backup-us.timer")[("Timer", "OnCalendar")] == ["hourly"]


def test_us_backup_lists_cut_out_the_library_and_foreign_jobs():
    inc = [ln.strip() for ln in _read("deploy/backup/restic-include-us.txt").splitlines() if ln.strip() and not ln.startswith("#")]
    assert "/srv/motres/identity" in inc and "/srv/motres/workspaces" in inc and "/etc/motres" in inc
    assert not any("foreign" in i or i.rstrip("/").endswith("/run") for i in inc)
    exc = _read("deploy/backup/restic-exclude-us.txt") + _read("deploy/backup/restic-exclude.txt")
    assert "foreign_jobs/" in exc
    svc = _read("deploy/systemd/motres-backup-us.service")
    assert "restic-exclude-mirrored.txt" in svc and "backup-exclude" in svc          # the mirrored files are excluded
    assert "repository" not in svc.lower().replace("own repository", "")            # the repository comes from the env file
    assert "motres-box" not in svc                                                   # never the EU Storage Box


def test_install_note_lists_the_owner_variables_and_every_step():
    note = _read("docs/regions/INSTALL_US_MIRROR.md")
    for needle in ("B2_ACCOUNT_ID", "B2_ACCOUNT_KEY", "RESTIC_PASSWORD", "RESTIC_REPOSITORY", "backup-us.env", "--init",
                   "motres-catalog-mirror.timer", "motres-directory-sync.timer", "motres-rsyncd-library", "MOTRES_CROSS_REGION_COMPUTE",
                   "/srv/motres/run/foreign_jobs", "region-us"):
        assert needle in note, needle

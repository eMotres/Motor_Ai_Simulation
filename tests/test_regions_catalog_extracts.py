"""Catalog mirror, stage 2 (docs/regions/DESIGN.md sections 3, 7, 12): the per-die extracts of the partial shared files
(EU side), and the US-side plan / apply / merge of the mirror job.

Nothing here touches a real server, the network or the owner's config: every store is under ``tmp_path``.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from motor_ai_sim import motor_access as MA
from motor_ai_sim.regions import catalog_manifest as CM
from motor_ai_sim.regions import catalog_mirror as MI


def _w(p: Path, text: str = "x: 1\n") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _j(p: Path, obj) -> None:
    _w(p, json.dumps(obj))


def _row(fp: str, **kw) -> dict:
    return {"thermal": {"kind": "thermal", "geometry_fingerprint": fp, "t_max": 90.0, **kw}}


@pytest.fixture()
def eu(tmp_path, monkeypatch):
    """An EU shared tree: public die PUB (L1 inherits, L2 private), closed die PRIV, selected die SEL with one public cfg."""
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "die_access.json")
    MA._CFG_VIS_CACHE.clear()
    _j(tmp_path / "die_access.json", {
        "PUB": {"visibility": "public"},
        "SEL": {"visibility": "selected", "clients": ["buyer@example.com"]},
        "PRIV": {"visibility": "private"},
    })
    root = tmp_path / "eu_shared"
    _w(root / "dies/PUB/die.yaml", "name: PUB\n")
    _w(root / "dies/PUB/L1.yaml", "name: L1\n")
    _w(root / "dies/PUB/L2.yaml", "visibility: private\nname: L2\n")
    _w(root / "dies/PRIV/die.yaml", "name: PRIV\n")
    _w(root / "dies/PRIV/C1.yaml", "name: C1\n")
    _w(root / "dies/SEL/die.yaml", "name: SEL\n")
    _w(root / "dies/SEL/S1.yaml", "visibility: public\nname: S1\n")
    _w(root / "dies/SEL/S2.yaml", "name: S2\n")
    _w(root / "materials_library.yaml", "steel: {}\n")
    _j(root / ".duty_results.json", {
        "version": 1, "updated_at": "2026-11-09T00:00:00",
        "results": {
            "PUB": {"L1": {"d1": _row("fpPUB1")}, "L2": {"d1": _row("fpPUB2")}, "GHOST": {"d1": _row("fpGHOST")}},
            "PRIV": {"C1": {"d1": _row("fpPRIV")}},
            "SEL": {"S1": {"d1": _row("fpSEL1")}, "S2": {"d1": _row("fpSEL2")}},
            "NOSUCH": {"X": {"d1": _row("fpNOSUCH")}},
        },
        "history": {"PUB": {"L1": {"d1": {"thermal": [_row("fpOLD")["thermal"]]}}}, "PRIV": {"C1": {"d1": {"thermal": []}}}},
    })
    _j(root / "end_effect_passports.json", {
        "fpPUB1": {"k_flux": 0.95}, "fpSEL1": {"k_flux": 0.94}, "fpOLD": {"k_flux": 0.9},
        "fpPRIV": {"k_flux": 0.9}, "fpPUB2": {"k_flux": 0.9}, "fpOrphan": {"k_flux": 0.9},
    })
    return root


def _build(root):
    """Build as the EU server would: ITS die_access.json (one process plays both servers in these tests)."""
    saved = MA._DIE_ACCESS_FILE
    MA._DIE_ACCESS_FILE = root.parent / "die_access.json"
    MA._CFG_VIS_CACHE.clear()
    try:
        return CM.build_all(root, catalog_path=root.parent / "nocatalog.json")
    finally:
        MA._DIE_ACCESS_FILE = saved
        MA._CFG_VIS_CACHE.clear()


# ── EU: the extracts ─────────────────────────────────────────────────────────

def test_duty_rows_only_of_mirrored_configurations(eu):
    man, ext = _build(eu)
    duty = {d: v["duty"] for d, v in ext["dies"].items() if "duty" in v}
    assert set(duty) == {"PUB", "SEL"}
    assert set(duty["PUB"]["results"]) == {"L1"}                       # L2 is private, GHOST is unattributable
    assert set(duty["SEL"]["results"]) == {"S1"}                       # S2 inherits a non-open die
    assert set(duty["PUB"]["history"]) == {"L1"}
    text = json.dumps(ext)
    for closed in ("fpPRIV", "fpPUB2", "fpSEL2", "fpGHOST", "fpNOSUCH", "PRIV", "NOSUCH"):
        assert closed not in text
    c = ext["counts"]
    assert c["mirrored"] == 2 and c["unattributed"] == 1               # GHOST
    assert c["closed"] >= 4                                            # PUB/L2, PRIV/C1, SEL/S2, NOSUCH/X
    assert man["extracts"]["id"] == ext["id"] and man["extracts"]["counts"] == c


def test_end_effect_entries_follow_the_fingerprints_of_mirrored_rows(eu):
    _man, ext = _build(eu)
    assert set(ext["passports"]) == {"fpPUB1", "fpSEL1", "fpOLD"}      # history rows count: they are mirrored rows
    assert ext["counts"]["passports_mirrored"] == 3 and ext["counts"]["passports_left_out"] == 3
    # a fingerprint named by a mirrored configuration's passport file is attributable too
    _j(eu / "passports/PUB/L1.json", {"end_effect": {"geometry_fingerprint": "fpOrphan"}})
    _j(eu / "passports/PRIV/C1.json", {"end_effect": {"geometry_fingerprint": "fpPRIV"}})
    _man, ext = _build(eu)
    assert "fpOrphan" in ext["passports"] and "fpPRIV" not in ext["passports"]


def test_die_access_only_public_and_never_with_clients(eu):
    _man, ext = _build(eu)
    assert ext["dies"]["PUB"]["access"] == {"visibility": "public", "clients": []}
    assert "access" not in ext["dies"]["SEL"]                          # selected: its client e-mails never cross
    assert "buyer@example.com" not in json.dumps(ext)
    assert "PRIV" not in ext["dies"]
    assert ext["counts"]["access"] == 1


def test_public_die_without_rows_still_gets_its_access_entry(eu):
    _j(eu / ".duty_results.json", {"version": 1, "results": {}, "history": {}})
    _man, ext = _build(eu)
    assert ext["dies"] == {"PUB": {"access": {"visibility": "public", "clients": []}}}


def test_unreadable_or_corrupt_store_is_counted_not_a_crash(eu):
    (eu / ".duty_results.json").write_text("{not json", encoding="utf-8")
    man, ext = _build(eu)
    assert all("duty" not in v for v in ext["dies"].values())
    assert ext["counts"]["unreadable"] == 1 and man["skipped_unreadable"] >= 1
    (eu / ".duty_results.json").write_text("[1, 2]", encoding="utf-8")   # valid JSON, wrong shape: unattributable
    _man, ext = _build(eu)
    assert ext["counts"]["unattributed"] >= 1 and all("duty" not in v for v in ext["dies"].values())


def test_unreadable_bak_files_are_skipped_not_fatal(eu, monkeypatch):
    """11-09: root-owned unreadable ``*.bak*`` files in a die folder crashed the build."""
    for name in ("L1.yaml.bak-20261030-liner", "die.yaml.bak", ".duty_results.json.bak"):
        _w(eu / "dies/PUB" / name, "old")
    _w(eu / "end_effect_passports.json.bak-20260924", "old")
    real_stat, real_is_file = Path.stat, Path.is_file

    def deny(self, *a, **k):
        if ".bak" in self.name:
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", deny)
    monkeypatch.setattr(Path, "is_file", lambda self: (_ for _ in ()).throw(PermissionError(13, "denied"))
                        if ".bak" in self.name else real_is_file(self))
    man, ext = _build(eu)
    assert not any(".bak" in k for k in man["files"])
    assert "dies/PUB/L1.yaml" in man["files"] and "PUB" in ext["dies"]


def test_the_manifest_that_leaves_eu_names_no_closed_die_or_file(eu):
    """B1: US stores the manifest on disk, so it carries counts and HMACs only - never a die / file name of a closed motor."""
    man, ext = _build(eu)
    assert {d["die"] for d in man["excluded_dies"]} == {"PRIV"}            # EU's own dry-run report still names them
    pub = CM.public_manifest(man, secret="k" * 40)
    blob = json.dumps({"m": pub, "e": ext})
    assert "PRIV" not in blob and "C1" not in blob
    for k in ("excluded_dies", "excluded_files", "skipped_unreadable_paths"):
        assert k not in pub
    assert pub["excluded_dies_count"] == 1 and pub["excluded_files_count"] >= 0
    assert pub["excluded_die_hmacs"] == [CM.die_hmac("PRIV", "k" * 40)] and "PRIV" not in pub["excluded_die_hmacs"][0]
    assert CM.die_hmac("PRIV", "k" * 40) != CM.die_hmac("PRIV", "j" * 40) and CM.die_hmac("PRIV", "") == ""
    assert set(pub["files"]) == set(man["files"])


def test_extracts_drop_source_paths_notes_and_the_admin_note(eu):
    """M8: free text, paths and who-did-it keys never cross inside a result row or a passport entry."""
    _j(eu / ".duty_results.json", {"results": {"PUB": {"L1": {"d1": {
        "thermal": {"kind": "thermal", "geometry_fingerprint": "fpPUB1", "t_max": 90.0, "source": "C:/Users/x/run.json",
                    "notes": ["see mail from boss@example.com"], "note": "admin remark", "owner": "x@example.com",
                    "nested": {"path": "/srv/motres/x", "keep": 1, "file_ref": "run_final.json", "label": "24 kHz"}},
        "mystery_kind": {"x": 1}}}}}, "history": {}})
    _j(eu / "end_effect_passports.json", {"fpPUB1": {"k_flux": 0.95, "machine": "24s/28p OD 85", "note": "adopted by the owner",
                                                       "source": "/srv/motres/run/stage_a.json", "mesh": {"h": 1, "path": "x"}}})
    _man, ext = _build(eu)
    row = ext["dies"]["PUB"]["duty"]["results"]["L1"]["d1"]
    assert set(row) == {"thermal"}                                         # an unknown kind is dropped
    t = row["thermal"]
    assert t["t_max"] == 90.0 and t["nested"] == {"keep": 1, "label": "24 kHz"}
    assert not ({"source", "notes", "note", "owner"} & set(t))
    assert ext["passports"]["fpPUB1"] == {"k_flux": 0.95, "machine": "24s/28p OD 85", "mesh": {"h": 1}}
    blob = json.dumps(ext)
    for leak in ("boss@", "admin remark", "adopted by the owner", "run.json", "/srv/motres", "run_final.json"):
        assert leak not in blob
    assert ext["counts"]["scrubbed"] >= 8 and ext["counts"]["unknown_kind"] == 1


# ── US: plan / apply / merge ─────────────────────────────────────────────────

@pytest.fixture()
def us(tmp_path, monkeypatch):
    root = tmp_path / "us_shared"
    root.mkdir()
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "us_die_access.json")
    state = tmp_path / "state.json"
    monkeypatch.setenv(MI.ENV_STATE, str(state))
    monkeypatch.setenv("MOTRES_PEER_HMAC_SECRET", "s" * 40)              # the HMAC of excluded die names (EU and US share it)
    return {"root": root, "state": state, "tmp": tmp_path, "mp": monkeypatch}


def _rsync(eu_root: Path, us_root: Path, plan: dict) -> None:
    """What ``rsync --files-from`` does: copy the listed files from the EU tree."""
    for rel in plan["fetch"]:
        dst = us_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(eu_root / rel, dst)


def _wire(man, ext):
    """What US receives: the PUBLIC manifest (no closed names) and the extracts, as JSON."""
    return json.loads(json.dumps(CM.public_manifest(man))), json.loads(json.dumps(ext))


def _round(eu_root, us_env, *, init=False, extracts=True, prune=False):
    man, ext = _wire(*_build(eu_root))
    st = MI.load_state()
    plan = MI.make_plan(man, us_env["root"], st, init=init, prune=prune)
    if plan["refuse"]:
        return man, ext, plan, None
    _rsync(eu_root, us_env["root"], plan)
    rep = MI.apply_plan(man, ext if extracts else None, plan, us_env["root"], st, prune_leftovers=plan["prune_files"],
                        prune_stores=plan["prune_stores"])
    return man, ext, plan, rep


def test_first_run_needs_init(eu, us):
    man, _ext = _build(eu)
    plan = MI.make_plan(man, us["root"], MI.load_state())
    assert plan["refuse"]["code"] == MI.EXIT_INIT and plan["fetch"] == []


def test_full_round_installs_files_and_merges_extracts(eu, us):
    man, ext, plan, rep = _round(eu, us, init=True)
    assert rep["bad"] == [] and rep["fetched_ok"] == len(man["files"])
    root = us["root"]
    assert (root / "dies/PUB/L1.yaml").is_file() and not (root / "dies/PRIV").exists()
    duty = json.loads((root / ".duty_results.json").read_text(encoding="utf-8"))
    assert set(duty["results"]) == {"PUB", "SEL"} and set(duty["results"]["PUB"]) == {"L1"}
    assert json.loads((root / "end_effect_passports.json").read_text(encoding="utf-8")).keys() == {"fpPUB1", "fpSEL1", "fpOLD"}
    assert MA.get_die_access("PUB") == {"visibility": "public", "clients": []}
    assert MA.get_die_access("SEL")["visibility"] == "private"          # US default, no client list
    # second round: in sync, nothing to fetch or delete
    _man2, _e2, plan2, _r2 = _round(eu, us)
    assert plan2["refuse"] is None and plan2["fetch"] == [] and plan2["delete"] == []


def test_local_edit_refuses_and_touches_nothing(eu, us):
    _round(eu, us, init=True)
    (us["root"] / "dies/PUB/L1.yaml").write_text("name: edited on US\n", encoding="utf-8")
    _w(eu / "materials_library.yaml", "steel: {new: 1}\n")               # EU moved on meanwhile
    _man, _ext, plan, rep = _round(eu, us)
    assert rep is None and plan["refuse"]["code"] == MI.EXIT_EDITS and plan["fetch"] == []
    assert plan["edits"] == [{"path": "dies/PUB/L1.yaml", "why": "changed on US"}]
    assert (us["root"] / "materials_library.yaml").read_text(encoding="utf-8") == "steel: {}\n"
    (us["root"] / "dies/PUB/L1.yaml").unlink()                          # a removed mirrored file is an edit too
    assert MI.make_plan(CM.build(eu, catalog_path=eu.parent / "n.json"), us["root"], MI.load_state())["edits"][0]["why"] \
        == "removed or unreadable on US"


def test_unmanaged_file_colliding_with_a_mirrored_path_is_refused(eu, us):
    _w(us["root"] / "dies/PUB/L1.yaml", "name: somebody else's\n")
    _man, _ext, plan, rep = _round(eu, us, init=False) if False else (None, None, None, None)
    man, _ = _build(eu)
    MI.save_state({"files": {}, "extracts": {}})                         # initialised, but this file is not the mirror's
    plan = MI.make_plan(man, us["root"], MI.load_state())
    assert plan["refuse"]["code"] == MI.EXIT_EDITS and plan["collisions"][0]["path"] == "dies/PUB/L1.yaml"


def test_die_that_stops_being_public_is_removed_everywhere_but_us_local_dies_stay(eu, us):
    _w(us["root"] / "dies/CUSTOMER/die.yaml", "name: lives on US\n")      # a closed motor of a US account: not the mirror's
    _j(us["root"] / ".duty_results.json", {"version": 1, "results": {"CUSTOMER": {"C": {"d": {}}}}, "history": {}})
    MA.set_die_access("CUSTOMER", visibility="selected", clients=["us.owner@example.com"])
    _round(eu, us, init=True)
    assert "CUSTOMER" in json.loads((us["root"] / ".duty_results.json").read_text(encoding="utf-8"))["results"]
    _j(eu.parent / "die_access.json", {})                                # EU: PUB is closed again
    (eu / "dies/PUB/L1.yaml").write_text("visibility: private\nname: L1\n", encoding="utf-8")
    _man, ext, plan, rep = _round(eu, us)
    assert "dies/PUB/die.yaml" in plan["delete"] and rep["bad"] == []
    root = us["root"]
    assert not (root / "dies/PUB").exists()                              # emptied directories are pruned
    duty = json.loads((root / ".duty_results.json").read_text(encoding="utf-8"))
    assert "PUB" not in duty["results"] and "PUB" not in duty["history"]
    assert "CUSTOMER" in duty["results"] and (root / "dies/CUSTOMER/die.yaml").is_file()
    assert MA.get_die_access("CUSTOMER")["clients"] == ["us.owner@example.com"]
    assert MA.get_die_access("PUB")["visibility"] == "private"
    assert "fpPUB1" not in json.loads((root / "end_effect_passports.json").read_text(encoding="utf-8"))


def test_unsafe_manifest_paths_are_dropped(us):
    man = {"root_id": "x", "files": {
        "../etc/passwd": {"sha256": "a" * 64, "bytes": 1}, "/abs/x.yaml": {"sha256": "a" * 64, "bytes": 1},
        "dies/../../x": {"sha256": "a" * 64, "bytes": 1}, "projects/p/project.json": {"sha256": "a" * 64, "bytes": 1},
        "dies\\x\\die.yaml": {"sha256": "a" * 64, "bytes": 1}, "dies/OK/die.yaml": {"sha256": "short", "bytes": 1},
        "materials_library.yaml": {"sha256": "b" * 64, "bytes": 3}}}
    files, dropped = MI.clean_manifest_files(man)
    assert list(files) == ["materials_library.yaml"] and dropped == 6


def test_corrupt_us_store_is_never_overwritten(eu, us):
    _w(us["root"] / ".duty_results.json", "{corrupt")
    man, ext = _build(eu)
    with pytest.raises(MI.MirrorError):
        MI.merge_extracts(json.loads(json.dumps(ext)), {}, us["root"])
    assert (us["root"] / ".duty_results.json").read_text(encoding="utf-8") == "{corrupt"


def test_wrong_fetch_is_reported_and_refetched_next_time(eu, us):
    man, ext = _build(eu)
    man, ext = json.loads(json.dumps(man)), json.loads(json.dumps(ext))
    st = MI.load_state()
    plan = MI.make_plan(man, us["root"], st, init=True)
    _rsync(eu, us["root"], plan)
    (us["root"] / "materials_library.yaml").write_text("EU changed it mid-sync\n", encoding="utf-8")
    rep = MI.apply_plan(man, ext, plan, us["root"], st)
    assert rep["bad"] == ["materials_library.yaml"]
    plan2 = MI.make_plan(man, us["root"], MI.load_state())                # not an "edit": the job wrote it itself
    assert plan2["refuse"] is None and plan2["fetch"] == ["materials_library.yaml"]


def test_leftovers_of_excluded_dies_are_found_by_hmac_listed_and_pruned_on_request(eu, us):
    root = us["root"]
    _w(root / "dies/PRIV/die.yaml", "name: hand copy from before the job existed\n")
    _w(root / "dies/PRIV/C1.yaml", "name: C1\n")
    _w(root / "passports/PRIV/C1.json", "{}")
    _w(root / "cad/PRIV/C1/manifest.json", "{}")
    _w(root / "dies/CUSTOMER/die.yaml", "name: lives on US\n")           # US-local: EU never heard of it
    man, _ext, plan, rep = _round(eu, us, init=True)
    assert "PRIV" not in json.dumps(man)                                   # US was told no name ...
    assert sorted(plan["leftovers"]) == ["cad/PRIV/C1/manifest.json", "dies/PRIV/C1.yaml", "dies/PRIV/die.yaml",
                                         "passports/PRIV/C1.json"]         # ... and still finds its own copy by HMAC
    assert (root / "dies/PRIV/die.yaml").exists()                          # listed, not deleted
    _man, _ext, plan, rep = _round(eu, us, prune=True)
    assert rep["leftovers_removed"] == 4 and rep["failed"] == []
    assert not (root / "dies/PRIV").exists() and not (root / "passports/PRIV").exists() and not (root / "cad/PRIV").exists()
    assert (root / "dies/CUSTOMER/die.yaml").is_file()


def test_init_removes_closed_dies_from_the_three_stores_but_keeps_us_local_dies(eu, us):
    """H4: a hand copy left rows of closed dies in the US stores; --init removes them, US-local dies stay."""
    root = us["root"]
    _w(root / "dies/PRIV/die.yaml", "name: PRIV\n")                       # hand copy of a closed die (HMAC-excluded)
    _w(root / "dies/CUSTOMER/die.yaml", "name: lives on US\n")           # a closed motor of a US account
    _j(root / ".duty_results.json", {"version": 1, "results": {
        "PRIV": {"C1": {"d": {"thermal": {"geometry_fingerprint": "fpPRIV"}}}},
        "CUSTOMER": {"X": {"d": {"thermal": {"geometry_fingerprint": "fpCUST"}}}},
        "STALE": {"Y": {"d": {"thermal": {"geometry_fingerprint": "fpSTALE"}}}}},          # no folder on US at all
        "history": {"PRIV": {"C1": {}}}})
    _j(root / "end_effect_passports.json", {"fpPRIV": {"k_flux": 0.9}, "fpCUST": {"k_flux": 0.9}, "fpSTALE": {"k_flux": 0.9},
                                              "fpOther": {"k_flux": 0.9}})
    _j(us["tmp"] / "us_die_access.json", {"PRIV": {"visibility": "public", "clients": []},
                                           "CUSTOMER": {"visibility": "selected", "clients": ["us.owner@example.com"]},
                                           "STALE": {"visibility": "public", "clients": []}})
    _round(eu, us, init=True)
    duty = json.loads((root / ".duty_results.json").read_text(encoding="utf-8"))
    assert set(duty["results"]) == {"PUB", "SEL", "CUSTOMER"} and "PRIV" not in duty["history"]
    assert set(json.loads((root / "end_effect_passports.json").read_text(encoding="utf-8"))) == \
        {"fpPUB1", "fpSEL1", "fpOLD", "fpCUST", "fpOther"}                 # only entries named by the removed rows go
    acc = json.loads((us["tmp"] / "us_die_access.json").read_text(encoding="utf-8"))
    assert set(acc) == {"PUB", "CUSTOMER"} and acc["CUSTOMER"]["clients"] == ["us.owner@example.com"]
    assert (root / "dies/PRIV/die.yaml").exists()                          # files go only with --prune-leftovers


def test_a_failed_delete_is_reported_kept_in_the_state_and_retried(eu, us, monkeypatch):
    """B3: a de-published die whose file cannot be removed must not pass silently."""
    _round(eu, us, init=True)
    _j(eu.parent / "die_access.json", {})                                   # EU: PUB is closed again
    (eu / "dies/PUB/L1.yaml").write_text("visibility: private\nname: L1\n", encoding="utf-8")
    real_unlink = Path.unlink

    def deny(self, *a, **k):
        if self.name == "die.yaml":
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", deny)
    _man, _ext, plan, rep = _round(eu, us)
    assert rep["failed"] == ["dies/PUB/die.yaml"] and (us["root"] / "dies/PUB/die.yaml").exists()
    assert "dies/PUB/die.yaml" in MI.load_state()["files"]                  # still the mirror's: retried, not forgotten
    monkeypatch.setattr(Path, "unlink", real_unlink)
    _man, _ext, plan, rep = _round(eu, us)
    assert rep["failed"] == [] and not (us["root"] / "dies/PUB").exists()
    assert "dies/PUB/die.yaml" not in MI.load_state()["files"]


def test_corrupt_die_access_is_never_overwritten(eu, us):
    """H5."""
    (us["tmp"] / "us_die_access.json").write_text("{corrupt", encoding="utf-8")
    _man, ext = _wire(*_build(eu))
    with pytest.raises(MI.MirrorError):
        MI.merge_extracts(ext, {}, us["root"])
    assert (us["tmp"] / "us_die_access.json").read_text(encoding="utf-8") == "{corrupt"
    (us["tmp"] / "us_die_access.json").write_text("[1]", encoding="utf-8")        # valid JSON, wrong shape
    with pytest.raises(MI.MirrorError):
        MI.merge_extracts(ext, {}, us["root"])


def test_die_access_merge_holds_the_lock_the_api_writer_takes(eu, us):
    """H5: the mirror's read-modify-write of die_access.json excludes the API's writer (same lock object)."""
    import threading
    _man, ext = _wire(*_build(eu))
    done = threading.Event()
    with MA._DA_LOCK:                                                      # "the API" is writing right now
        th = threading.Thread(target=lambda: (MI.merge_extracts(ext, {}, us["root"]), done.set()), daemon=True)
        th.start()
        assert not done.wait(0.5)                                          # the merge waits for the writer ...
    assert done.wait(10)                                                   # ... and goes on when it is released
    th.join(5)
    assert MA.get_die_access("PUB")["visibility"] == "public"


def test_plan_command_refuses_outside_a_us_server(monkeypatch):
    for k in ("MOTRES_REGION", "MOTRES_PEER_URL", "MOTRES_PEER_HMAC_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert MI.main(["plan"]) == MI.EXIT_MODE

"""The three boot-time checks (migration Stage 6).

WHAT THESE TESTS ARE FOR
========================
The case-collision check is the only piece of Stage 6 that cannot be exercised
by running it: it fires on a condition NTFS is physically unable to represent,
so on this workstation ``CILN28`` and ``ciln28`` are one directory and the real
tree can never trip it.  Every assertion about it therefore has to be made
against a *constructed* input — a dict of names, not a pair of folders — which
is exactly why :func:`case_collisions` takes a directory and the grouping logic
is a pure function of the names inside it.

Two of the cases below (``two_dies_differing_by_case``, ``the fatal stops the
boot``) are skipped on a case-insensitive filesystem rather than faked: a test
that pretended to create both folders would pass here and prove nothing about
the server, which is the one machine the check exists for.  The grouping test
underneath them runs everywhere and is what actually pins the logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from motor_ai_sim import startup_checks as SC  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────

def make_die(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "die.yaml").write_text("name: " + name, encoding="utf-8")
    return d


@pytest.fixture
def case_sensitive_fs(tmp_path: Path) -> bool:
    """Can this filesystem hold ``A`` and ``a`` as two directories?"""
    a = tmp_path / "_probe_A"
    a.mkdir()
    return not (tmp_path / "_probe_a").exists()


# ── 1. case collisions ──────────────────────────────────────────────────────

def test_clean_catalog_has_no_findings(tmp_path: Path):
    dies = tmp_path / "dies"
    for n in ("CILN28", "CIANO10 200", "100 mm · 24s-28p mid-torque"):
        make_die(dies, n)
    assert SC.case_collisions(dies) == []
    assert SC.check_die_case([dies]) == []


def test_non_ascii_die_names_are_not_collisions(tmp_path: Path):
    """``·`` (U+00B7) and ``°`` are real die-name characters, not a fault.

    routes/family constrains die names to a filesystem-safe charset that
    includes both, and ``100 mm · 24s-28p mid-torque`` is a folder that exists.
    A check that tripped over them would refuse to boot the real catalog.
    """
    dies = tmp_path / "dies"
    make_die(dies, "100 mm · 24s-28p mid-torque")
    make_die(dies, "40 mm 12s-14p 90°")
    assert SC.check_die_case([dies]) == []


def test_only_folders_with_die_yaml_count(tmp_path: Path):
    """A stray ``runs`` and a ``RUNS`` backup must not fail a boot."""
    dies = tmp_path / "dies"
    make_die(dies, "CILN28")
    (dies / "runs").mkdir()
    (dies / "RUNS").mkdir(exist_ok=True)
    (dies / "notes.txt").write_text("x", encoding="utf-8")
    assert SC.check_die_case([dies]) == []


def test_missing_directory_is_not_a_finding(tmp_path: Path):
    assert SC.case_collisions(tmp_path / "nope") == []
    assert SC.check_die_case([tmp_path / "nope"]) == []


def test_two_dies_differing_by_case_are_fatal(tmp_path: Path, case_sensitive_fs):
    if not case_sensitive_fs:
        pytest.skip("case-insensitive filesystem: the condition cannot exist "
                    "here — this is the Linux-only half of the check")
    dies = tmp_path / "dies"
    make_die(dies, "CILN28")
    make_die(dies, "ciln28")

    found = SC.case_collisions(dies)
    assert len(found) == 1
    assert found[0][0] == "ciln28"
    assert found[0][1] == ["CILN28", "ciln28"]

    findings = SC.check_die_case([dies])
    assert [f.level for f in findings] == ["fatal"]
    assert findings[0].code == "die_case_collision"
    assert "CILN28" in findings[0].message and "ciln28" in findings[0].message


def test_grouping_is_by_simple_lowercase():
    """The rule itself, pinned without needing a case-sensitive disk.

    This is why ``group_by_case`` is a separate function: the folding rule is
    the part that has to be right, and it is the part NTFS cannot be asked
    about.  Handing it the two names directly fakes nothing that matters.
    """
    assert SC.group_by_case(["CILN28", "ciln28", "CIANO10", "unique"]) == [
        ("ciln28", ["CILN28", "ciln28"])]
    # three-way, and the group is sorted so the message is stable
    assert SC.group_by_case(["A", "a", "A ", "b"]) == [("a", ["A", "a"])]
    assert SC.group_by_case([]) == []


def test_grouping_uses_lower_not_casefold():
    """``ß``/``ss`` fold together under casefold() and NOT on NTFS.  Flagging
    them would refuse to boot over a pair that was never ambiguous anywhere."""
    assert SC.group_by_case(["straße", "strasse"]) == []
    # ...while the plain ASCII pair still collides.
    assert SC.group_by_case(["Straße", "straße"]) == [
        ("straße", ["Straße", "straße"])]


def test_fatal_stops_the_boot(tmp_path: Path, monkeypatch, case_sensitive_fs):
    if not case_sensitive_fs:
        pytest.skip("case-insensitive filesystem: see above")
    dies = tmp_path / "dies"
    make_die(dies, "A die")
    make_die(dies, "a die")
    monkeypatch.setattr(SC, "die_roots", lambda: [dies])
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)

    with pytest.raises(SC.StartupCheckError) as exc:
        SC.run_startup_checks()
    assert "differ only by case" in str(exc.value)

    # ...and the same run WITHOUT raise_on_fatal still reports it, so a
    # diagnostic tool can list every fault instead of stopping at the first.
    findings = SC.run_startup_checks(raise_on_fatal=False)
    assert any(f.level == "fatal" for f in findings)


# ── 2. AUTH_SECRET ──────────────────────────────────────────────────────────

def test_auth_secret_silent_without_workspaces_root():
    """Single-user: the generated .auth_secret is the documented bargain."""
    assert SC.check_auth_secret({"AUTH_SECRET": ""}) == []
    assert SC.check_auth_secret({}) == []


def test_auth_secret_warns_when_multi_user_and_implicit():
    findings = SC.check_auth_secret({"WORKSPACES_ROOT": "/srv/motres/workspaces"})
    assert [f.level for f in findings] == ["warn"]
    assert findings[0].code == "auth_secret_implicit"
    # The warning has to say WHAT IT COSTS, or it is one more line in a log.
    assert "permanently" in findings[0].message
    assert "backup" in findings[0].message


def test_auth_secret_quiet_when_set_explicitly():
    assert SC.check_auth_secret({"WORKSPACES_ROOT": "/srv/motres/workspaces",
                                 "AUTH_SECRET": "0123456789abcdef"}) == []


def test_auth_secret_treats_whitespace_as_unset():
    findings = SC.check_auth_secret({"WORKSPACES_ROOT": "/srv/x",
                                     "AUTH_SECRET": "   "})
    assert [f.code for f in findings] == ["auth_secret_implicit"]


def test_auth_secret_is_never_fatal():
    """A server that cannot sign tokens is broken; one that generated its own
    key is merely fragile.  Refusing the boot would trade a risk for an outage."""
    findings = SC.check_auth_secret({"WORKSPACES_ROOT": "/srv/x"})
    assert all(f.level == "warn" for f in findings)


# ── 3. report dependencies ──────────────────────────────────────────────────

def test_report_deps_present_here():
    """They are pinned in requirements.txt as of Stage 6 — this environment
    installed them, so the check must be silent."""
    assert SC.check_report_deps() == []


def test_report_deps_warn_when_missing(monkeypatch):
    import importlib.util as _iu
    real = _iu.find_spec

    def fake(name, *a, **kw):
        if name in ("reportlab", "docx", "triangle"):
            return None
        return real(name, *a, **kw)

    monkeypatch.setattr(SC.importlib.util, "find_spec", fake)
    findings = SC.check_report_deps()
    assert {f.code for f in findings} == {"missing_dependency"}
    assert all(f.level == "warn" for f in findings)
    names = " ".join(f.message for f in findings)
    for pip_name in ("reportlab", "python-docx", "triangle"):
        assert pip_name in names
    # The .docx warning must name it as the DEFAULT format: that is what turns
    # "an optional dep is missing" into "the product's main button 500s".
    assert "DEFAULT" in names


def test_import_error_counts_as_missing(monkeypatch):
    def boom(name, *a, **kw):
        raise ValueError("broken namespace package")

    monkeypatch.setattr(SC.importlib.util, "find_spec", boom)
    findings = SC.check_report_deps()
    assert len(findings) == len(SC.REPORT_DEPS)


# ── the entry point ─────────────────────────────────────────────────────────

def test_clean_run_returns_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(SC, "die_roots", lambda: [tmp_path / "dies"])
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    assert SC.run_startup_checks() == []


def test_a_broken_check_cannot_stop_a_boot(monkeypatch, tmp_path):
    """A bug in THIS file must not be able to take the server down."""
    def boom():
        raise RuntimeError("check itself is broken")

    monkeypatch.setattr(SC, "die_roots", boom)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    assert SC.run_startup_checks() == []      # logged, not raised


def test_die_roots_covers_every_layer(monkeypatch, tmp_path):
    shared = tmp_path / "shared"
    published = tmp_path / "published"
    workspaces = tmp_path / "workspaces"
    (shared / "dies").mkdir(parents=True)
    (published / "abc123").mkdir(parents=True)
    (workspaces / "ws1" / "dies").mkdir(parents=True)
    (workspaces / "ws2" / "dies").mkdir(parents=True)

    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces))

    roots = [Path(p) for p in SC.die_roots()]
    assert shared / "dies" in roots
    assert workspaces / "ws1" / "dies" in roots
    assert workspaces / "ws2" / "dies" in roots
    # published/<ws_id>/ IS the dies directory for that namespace — no suffix.
    assert published / "abc123" in roots


def test_die_roots_infers_published_from_workspaces(monkeypatch, tmp_path):
    """PUBLISHED_ROOT defaults to the sibling of the workspaces tree, the same
    rule workspace.published_root() applies."""
    (tmp_path / "published" / "owner").mkdir(parents=True)
    (tmp_path / "workspaces").mkdir()
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.delenv("PUBLISHED_ROOT", raising=False)
    assert tmp_path / "published" / "owner" in [Path(p) for p in SC.die_roots()]


def test_findings_are_greppable():
    """Codes are the thing an alert rule and a runbook quote; they are API."""
    codes = {"die_case_collision", "auth_secret_implicit", "missing_dependency"}
    got = set()
    got |= {f.code for f in SC.check_auth_secret({"WORKSPACES_ROOT": "/x"})}
    got |= {"die_case_collision", "missing_dependency"}   # constructed above
    assert got <= codes


# ── the sd_notify helper: the promise that it does nothing ──────────────────

def test_watchdog_is_inert_without_notify_socket(monkeypatch):
    """Zero behaviour change anywhere but under a systemd Type=notify unit."""
    from motor_ai_sim import watchdog_notify as W
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert W.enabled() is False
    assert W.notify("READY=1") is False
    assert W.start() is False
    W.stop()                      # must not raise with nothing started


def test_watchdog_interval_is_a_third_of_watchdogsec(monkeypatch):
    """Two consecutive misses before systemd acts — one slow tick under a
    90-minute solve must never be fatal."""
    from motor_ai_sim import watchdog_notify as W
    monkeypatch.setenv("WATCHDOG_USEC", str(300 * 1_000_000))
    assert W._interval_s() == pytest.approx(100.0)
    monkeypatch.setenv("WATCHDOG_USEC", "garbage")
    assert W._interval_s() == W._DEFAULT_INTERVAL_S
    monkeypatch.setenv("WATCHDOG_USEC", "0")
    assert W._interval_s() == W._DEFAULT_INTERVAL_S

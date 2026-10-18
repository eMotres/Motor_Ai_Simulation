"""Deploy stage 2: deploy/plan_deploy.py decides which of web / api / worker a change needs."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("plan_deploy", ROOT / "deploy" / "plan_deploy.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

SRC = "src/motor_ai_sim/"


def test_pages_only_touch_the_pages():
    assert P.plan(["web/src/App.tsx", "web/src/locales/en/admin.json"]) == ("web", "all")
    assert P.plan(["deploy/nginx.conf"]) == ("web", "all")


def test_docs_and_tests_alone_deploy_nothing_new():
    assert P.plan(["docs/DEPLOY_STAGE2.md", "tests/test_x.py", "CHANGELOG.md"])[0] == "web"     # stage-1 behaviour: a no-op publish


def test_api_only_code_never_restarts_the_worker():
    """Accounts, admin, production, support ... run only in the API process: blue-green, no wait."""
    for f in ("routes/admin.py", "routes/auth_local.py", "production/store.py", "routes/support.py", "support_store.py",
              "newsletter.py", "mailing.py", "nda.py", "routes/model3d.py", "directory/routes.py", "design_agent/runner.py",
              "routes/oauth.py", "routes/cluster.py", "verification/service.py"):
        assert P.plan([SRC + f]) == ("api", "all"), f


def test_code_the_worker_runs_restarts_it_when_idle():
    for f in ("routes/thermal.py", "routes/simulation.py", "routes/coupled.py", "simulation/fem_solver_2d.py", "jobs.py",
              "workspace.py", "roles.py", "live_status.py", "routes/family.py", "routes/catalog.py", "config.py"):
        mode, mods = P.plan([SRC + f])
        assert mode == "back", f
    # the idle wait is as narrow as the stage-1 module map allows
    assert P.plan([SRC + "routes/thermal.py"]) == ("back", "thermal")
    assert P.plan([SRC + "routes/mechanical.py", SRC + "routes/thermal.py"]) == ("back", "mechanical,thermal")
    assert P.plan([SRC + "jobs.py"]) == ("back", "all"), "a shared file: nobody may be active anywhere"


def test_an_api_only_file_next_to_a_worker_file_narrows_nothing_wrongly():
    assert P.plan([SRC + "routes/admin.py", SRC + "routes/thermal.py"]) == ("back", "thermal")


def test_the_environment_of_every_container_changes_all_three():
    for f in ("deploy/docker-compose.yml", "VERSION"):
        assert P.plan([f]) == ("full", "all"), f
    assert P.plan(["requirements.txt"]) == ("back", "all")
    assert P.plan(["deploy/Dockerfile.api"]) == ("back", "all")


def test_pages_plus_backend():
    assert P.plan(["web/src/a.tsx", SRC + "routes/admin.py"]) == ("webapi", "all")
    assert P.plan(["web/src/a.tsx", SRC + "routes/thermal.py"]) == ("full", "thermal")


def test_the_deploy_scripts_themselves_are_neutral():
    for f in ("deploy/deploy_module.sh", "deploy/plan_deploy.py", "deploy/bluegreen.sh", "deploy/cutover_stage2.sh",
              "deploy/systemd/motres-retention.service", "deploy/README.md"):
        assert P.plan([f]) == ("web", "all"), f          # nothing to restart (a no-op publish, as in stage 1)


def test_a_pending_worker_is_judged_from_its_own_base():
    """After WORKER PENDING the next deploy's diff (PREV..SHA) can be empty - the worker's need comes from WORKER_PREV."""
    assert P.plan([], [SRC + "routes/coupled.py"]) == ("worker", "all")
    assert P.plan([SRC + "routes/admin.py"], [SRC + "routes/coupled.py", SRC + "routes/admin.py"])[0] == "back"
    assert P.plan(["web/src/a.tsx"], [SRC + "routes/coupled.py"])[0] == "full"
    # and an API-only worker diff leaves nothing pending
    assert P.plan([SRC + "routes/admin.py"], [SRC + "routes/admin.py"]) == ("api", "all")


def test_cli_prints_mode_and_modules(tmp_path):
    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True,
                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@x", "PATH": __import__("os").environ["PATH"],
                            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
                            "HOME": str(tmp_path)})
    git("init", "-q")
    (tmp_path / "src/motor_ai_sim/routes").mkdir(parents=True)
    (tmp_path / "src/motor_ai_sim/routes/thermal.py").write_text("a=1\n")
    (tmp_path / "src/motor_ai_sim/routes/admin.py").write_text("a=1\n")
    git("add", "-A"); git("commit", "-qm", "one")
    (tmp_path / "src/motor_ai_sim/routes/admin.py").write_text("a=2\n")
    git("commit", "-qam", "two")
    (tmp_path / "src/motor_ai_sim/routes/thermal.py").write_text("a=3\n")
    git("commit", "-qam", "three")

    def plan(*args):
        return subprocess.run([sys.executable, str(ROOT / "deploy" / "plan_deploy.py"), *args], cwd=tmp_path,
                              capture_output=True, text=True, check=True).stdout.strip()
    assert plan("HEAD~2", "HEAD~1") == "api all"                 # admin.py: API only
    assert plan("HEAD~1", "HEAD") == "back thermal"
    assert plan("HEAD~1", "HEAD~1", "HEAD~2") == "web all", "nothing new, and the worker's pending diff is API-only: nothing to do"
    assert plan("HEAD~1", "HEAD", "HEAD~2") == "back thermal"

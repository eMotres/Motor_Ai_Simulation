"""Which deploy a change needs (deploy/deploy_module.sh): web | api | webapi | worker | back | full, and the modules the
WORKER part touches.

    python deploy/plan_deploy.py <PREV> <SHA> [<WORKER_PREV>]      ->  prints "<MODE> <MODULES>"

Three deployable parts since stage 2 (docs/DEPLOY_STAGE2.md):

web     = web/ and the nginx setup changed (pages; the API and the users' jobs are untouched)
api     = backend files changed -> the API is redeployed blue-green.  That is free (no wait, no restart in place, no 502),
          so ANY backend change redeploys it
worker  = backend files the compute worker EXECUTES changed -> the worker restarts, but only when it is idle (it may wait
          for hours) and never while a job runs.  Files only the API process runs (accounts, admin, production, support,
          mail ...: :data:`API_ONLY`) never trigger it.  When in doubt a file counts for the worker too: a worker running
          stale code that a changed API now talks to is the worse mistake, a needless wait is only a wait.

MODE    = the parts that changed: web | api | webapi | worker | back (api+worker) | full (web+api+worker)
MODULES = live_status module names of the touched WORKER files; "all" when a shared file changed (auth, api.py, jobs,
workspace, requirements, Dockerfile ...), so the restart waits until nobody is active anywhere.

``WORKER_PREV`` is the commit the worker really runs (``.worker_commit`` on the server).  It differs from ``PREV`` after a
deploy ended with WORKER PENDING; the worker's need is then judged from ITS base, so a pending update is never forgotten.
"""
import subprocess
import sys

#: backend path prefix -> module (the rest is "all")
BACKEND_MODULES = (
    ("src/motor_ai_sim/production/", "production"), ("src/motor_ai_sim/design_agent/", "designer"),
    ("src/motor_ai_sim/routes/design_agent.py", "designer"), ("src/motor_ai_sim/directory/", "admin"),
    ("src/motor_ai_sim/routes/admin", "admin"), ("src/motor_ai_sim/erp_export.py", "admin"),
    ("src/motor_ai_sim/routes/erp_export.py", "admin"), ("src/motor_ai_sim/routes/propellers.py", "compare"),
    ("src/motor_ai_sim/propeller", "compare"), ("src/motor_ai_sim/routes/model3d.py", "model3d"),
    ("src/motor_ai_sim/model3d.py", "model3d"), ("src/motor_ai_sim/routes/support.py", "support"),
    ("src/motor_ai_sim/support_", "support"), ("src/motor_ai_sim/routes/mechanical.py", "mechanical"),
    ("src/motor_ai_sim/routes/thermal.py", "thermal"), ("src/motor_ai_sim/routes/controller.py", "controller"),
    ("src/motor_ai_sim/routes/system_graph.py", "schemas"),
)
NEUTRAL = ("tests/", "docs/", "README", "CHANGELOG", ".github/", ".gitattributes",
           "deploy/deploy_module.sh", "deploy/plan_deploy.py", "deploy/bluegreen.sh", "deploy/cutover_stage2.sh",
           "deploy/systemd/", "deploy/README.md", "deploy/CONTAINER_TEST.md")
#: files that only change the web container (pages and their nginx)
WEB_FILES = ("web/", "deploy/nginx.conf", "deploy/Dockerfile.web", "deploy/nginx-")
#: files that change every part (the compose file decides the fences / services of all of them)
BOTH_FILES = ("deploy/docker-compose.yml", "VERSION")
#: backend files that only the API process executes for the paths nginx sends it (roles.WORKER_PREFIXES decides the paths).
#: A change here never needs the worker restarted.  Keep this list to code the worker really never runs: a module the
#: worker imports for a route it serves (family, catalog, presets, the solvers' helpers ...) is NOT here.
#: SHARED, deliberately absent: ``oauth.py`` and ``mcp_discovery.py`` (the modules: ``mcp_app`` - the worker's /mcp - imports
#: both; ``agent_keys`` / ``agent_designs`` / ``mcp_tools`` are not listed either), ``auth_email.py`` (imported at module
#: level by route modules every process loads, and by the mail paths) and ``account_lifecycle.py`` (the retention job).
#: Only the ROUTERS (``routes/oauth.py``, ``routes/mcp_discovery.py``) are API-only.
API_ONLY = (
    "src/motor_ai_sim/production/", "src/motor_ai_sim/directory/", "src/motor_ai_sim/design_agent/",
    "src/motor_ai_sim/routes/design_agent.py", "src/motor_ai_sim/routes/admin", "src/motor_ai_sim/erp_export.py",
    "src/motor_ai_sim/routes/erp_export.py", "src/motor_ai_sim/routes/support.py", "src/motor_ai_sim/support_",
    "src/motor_ai_sim/routes/newsletter.py", "src/motor_ai_sim/newsletter.py", "src/motor_ai_sim/mailing.py",
    "src/motor_ai_sim/routes/comm_prefs.py", "src/motor_ai_sim/comm_prefs.py", "src/motor_ai_sim/routes/auth_local.py",
    "src/motor_ai_sim/routes/oauth.py",
    "src/motor_ai_sim/routes/agent_keys.py", "src/motor_ai_sim/routes/account.py", "src/motor_ai_sim/routes/account_data.py",
    "src/motor_ai_sim/routes/nda.py", "src/motor_ai_sim/nda.py",
    "src/motor_ai_sim/routes/verification.py", "src/motor_ai_sim/verification/", "src/motor_ai_sim/routes/cluster.py",
    "src/motor_ai_sim/routes/activity.py", "src/motor_ai_sim/routes/data_sharing.py", "src/motor_ai_sim/data_sharing.py",
    "src/motor_ai_sim/routes/compute_nodes.py", "src/motor_ai_sim/routes/live.py", "src/motor_ai_sim/routes/model3d.py",
    "src/motor_ai_sim/routes/construction.py", "src/motor_ai_sim/model3d.py", "src/motor_ai_sim/cad/",
    "src/motor_ai_sim/routes/mcp_discovery.py",
)


def _web(files):
    both = [f for f in files if f.startswith(BOTH_FILES)]
    return [f for f in files if f.startswith(WEB_FILES)] + both


def _back(files):
    return [f for f in files if not f.startswith(WEB_FILES) and not f.startswith(NEUTRAL)]


def _modules(back):
    mods = set()
    for f in back:
        mods.add(next((mod for pfx, mod in BACKEND_MODULES if f.startswith(pfx)), "all"))
    if "all" in mods or not mods:
        mods = {"all"}
    return ",".join(sorted(mods))


def plan(files, worker_files=None):
    """-> (mode, worker_modules).  ``worker_files``: the diff from the commit the worker runs (default: ``files``)."""
    worker_files = files if worker_files is None else worker_files
    web = bool(_web(files))
    # the compose file / VERSION change what every container runs
    api = bool(_back(files)) or any(f.startswith(BOTH_FILES) for f in files)
    wback = [f for f in _back(worker_files) if not f.startswith(API_ONLY)]
    wboth = [f for f in worker_files if f.startswith(BOTH_FILES)]
    worker = bool(wback) or bool(wboth)
    mode = {(True, False, False): "web", (False, True, False): "api", (True, True, False): "webapi",
            (False, True, True): "back", (True, True, True): "full",
            (True, False, True): "full",            # web + a pending worker: the API part is a harmless no-op redeploy
            (False, False, True): "worker",         # the follow-up of a deploy that ended WORKER PENDING
            (False, False, False): "web"}[(web, api, worker)]
    return mode, (_modules(wback + wboth) if worker else "all")


if __name__ == "__main__":
    prev, sha = sys.argv[1], sys.argv[2]
    wprev = sys.argv[3] if len(sys.argv) > 3 else prev

    def _diff(a, b):
        return subprocess.run(["git", "diff", "--name-only", a, b], capture_output=True, text=True,
                              check=True).stdout.split()

    files = _diff(prev, sha)
    wfiles = files if wprev == prev else _diff(wprev, sha)
    print(*plan(files, wfiles))

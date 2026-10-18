"""Which deploy a change needs (deploy/deploy_module.sh): web | api | full, and the modules the API part touches.

    python deploy/plan_deploy.py <PREV> <SHA>      ->  prints "<MODE> <MODULES>"

web  = only web/ changed (pages; the API and the users' jobs are untouched)
api  = only backend files changed
full = both
MODULES = live_status module names of the touched backend files; "all" when a shared file changed (auth, api.py,
jobs, workspace, requirements, Dockerfile ...), so the restart waits until nobody is active anywhere.
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
           "deploy/deploy_module.sh", "deploy/plan_deploy.py")
#: files that only change the web container (pages and their nginx)
WEB_FILES = ("web/", "deploy/nginx.conf", "deploy/Dockerfile.web", "deploy/nginx-")
#: files that change both containers
BOTH_FILES = ("deploy/docker-compose.yml", "VERSION")


def plan(files):
    both = [f for f in files if f.startswith(BOTH_FILES)]
    web = [f for f in files if f.startswith(WEB_FILES)] + both
    back = [f for f in files if not f.startswith(WEB_FILES) and not f.startswith(NEUTRAL)]
    mode = "full" if web and back else ("web" if web else ("api" if back else "web"))
    mods = set()
    for f in back:
        m = next((mod for pfx, mod in BACKEND_MODULES if f.startswith(pfx)), "all")
        mods.add(m)
    if "all" in mods or not mods:
        mods = {"all"}
    return mode, ",".join(sorted(mods))


if __name__ == "__main__":
    prev, sha = sys.argv[1], sys.argv[2]
    files = subprocess.run(["git", "diff", "--name-only", prev, sha], capture_output=True, text=True,
                           check=True).stdout.split()
    print(*plan(files))

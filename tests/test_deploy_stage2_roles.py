"""Deploy stage 2 (docs/DEPLOY_STAGE2.md): which process serves which path.

``motor_ai_sim.roles`` holds the table of compute-worker paths; ``deploy/nginx.conf`` holds the regex that sends them
there.  These tests keep the two - and the application's real routes - from drifting apart, and pin the compose file's
fences.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from motor_ai_sim import roles

ROOT = Path(__file__).resolve().parents[1]
CONF = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))


# ── the role switch ──────────────────────────────────────────────────────────

def test_role_defaults_to_all_and_only_api_refuses_compute(monkeypatch):
    monkeypatch.delenv(roles.ENV_ROLE, raising=False)
    assert roles.role() == "all" and roles.serves_compute() and not roles.multi_process()
    monkeypatch.setenv(roles.ENV_ROLE, "API ")
    assert roles.role() == "api" and roles.is_api() and not roles.serves_compute() and roles.multi_process()
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    assert roles.is_worker() and roles.serves_compute() and roles.multi_process()
    monkeypatch.setenv(roles.ENV_ROLE, "nonsense")
    assert roles.role() == "all"


# ── the path table ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/simulation/physics/fem_transient", "/api/simulation/run", "/api/coupled/run", "/api/thermal/coupled",
    "/api/mechanical/rotor_stress", "/api/static3d/solve", "/api/optimization/scan", "/api/jobs",
    "/api/jobs/run-1/cancel", "/api/geometry", "/api/geometry/mesh2d", "/api/config", "/api/materials", "/api/parts",
    "/api/winding/config", "/api/mesh/config", "/api/family/report/CIANO28/L155", "/api/family/report/progress",
    "/mcp", "/api/sims/saved", "/api/history", "/api/controller/solve", "/api/system/run", "/api/kernel/run",
    "/api/catalog/CIANO28/passport",          # minutes of FEM inside the request
])
def test_worker_paths(path):
    assert roles.is_worker_path(path)


@pytest.mark.parametrize("path", [
    "/api/health", "/api/me", "/api/version", "/api/notice", "/api/admin/live", "/api/admin/users",
    "/api/auth/login", "/api/family/tree", "/api/family/datasheet/CIANO28/L155", "/api/catalog", "/api/presets",
    "/api/materials/library", "/api/materials/library/steel/M270", "/api/materials/global", "/api/production/x",
    "/api/design-agent/conversations", "/api/support/chat", "/api/configure/limits", "/api/geometryx", "/api/simulations",
    "/oauth/token", "/.well-known/oauth-authorization-server", "/mcpx", "/api/worker/state", "/api/nodes/jobs/lease",
    "/api/activity/beat", "/api/model3d/models", "/api/propellers", "/llms-full.txt",
    "/api/catalog/CIANO28/load", "/api/catalog/CIANO28/passport/x", "/api/catalog/cards",
])
def test_api_paths(path):
    assert not roles.is_worker_path(path)


def test_the_nginx_regex_is_the_table():
    """deploy/nginx.conf carries exactly the regex roles.nginx_regex() builds."""
    assert "location ~ %s {" % roles.nginx_regex() in CONF


def test_the_nginx_regex_agrees_with_the_python_predicate():
    rx = re.compile(roles.nginx_regex())
    for path in ("/api/simulation", "/api/simulation/x", "/api/simulations", "/api/sims", "/api/simulationX",
                 "/api/materials", "/api/materials/library", "/api/config", "/api/configure", "/mcp", "/mcpx",
                 "/api/family/report/a/b", "/api/family/reports", "/api/family/tree", "/api/jobs/a", "/api/jobsx"):
        assert bool(rx.search(path)) == roles.is_worker_path(path), path


def test_nginx_has_no_direct_api_proxy_left():
    """Every API proxy goes through the blue-green upstream; the worker is reached by variable (re-resolved per request)."""
    assert "http://api:8001" not in CONF
    assert "upstream motres_api {" in CONF and "include /etc/nginx/api-upstream.conf;" in CONF
    assert CONF.count("proxy_pass http://motres_api") >= 4
    assert "proxy_pass $motres_worker;" in CONF and "set $motres_worker http://worker:8002;" in CONF
    assert "resolver 127.0.0.11" in CONF
    assert "error_page 502 = @worker_down;" in CONF and "location @worker_down" in CONF


def test_mcp_has_its_own_exact_location_with_short_timeouts():
    """The regex gives a worker path the FEM solves' hour-long timeouts and 20 MB bodies; `/mcp` is a short JSON-RPC exchange."""
    assert roles.is_worker_path("/mcp") and re.search(roles.nginx_regex(), "/mcp")
    block = CONF.split("location = /mcp {", 1)[1].split(chr(10) + "    }", 1)[0]
    assert "proxy_pass $motres_worker;" in block and "set $motres_worker http://worker:8002;" in block
    assert "proxy_read_timeout 120s;" in block and "proxy_send_timeout 120s;" in block
    assert "client_max_body_size 1m;" in block and "error_page 502 = @worker_down;" in block
    assert "3600s" not in block


# ── the real routes ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_routes():
    from motor_ai_sim.api import app
    return [r for r in app.routes if getattr(r, "path", None) and getattr(r, "methods", None)]


def test_every_route_that_queues_a_job_is_a_worker_path(app_routes):
    queued = [(r.path, r.endpoint.__job_kind__) for r in app_routes if hasattr(r.endpoint, "__job_kind__")]
    assert len(queued) >= 8, "the @queued routes were not found - did the decorator stop tagging them?"
    missing = [(p, k) for p, k in queued if not roles.is_worker_path(p)]
    assert not missing, "routes that queue a job but nginx would send to the API process: %s" % missing


def test_the_background_job_routes_are_worker_paths():
    """Routes that start a thread under jobs.admit / jobs.as_job (no @queued tag): static3d and the optimizer."""
    for p in ("/api/static3d/solve", "/api/static3d/solve/progress", "/api/static3d/solve/cancel",
              "/api/optimization/run", "/api/optimization/scan", "/api/optimization/scan/progress",
              "/api/optimization/scan/cancel", "/api/optimization/descent/start", "/api/optimization/descent/progress",
              "/api/optimization/doe/start", "/api/optimization/doe/progress", "/api/optimization/auto",
              "/api/optimization/auto/status", "/api/optimization/refine", "/api/optimization/refine/progress",
              "/api/optimization/refine/cancel", "/api/jobs", "/api/jobs/x/cancel", "/api/coupled/cancel",
              "/api/coupled/progress", "/api/simulation/physics/fem_transient/progress",
              "/api/simulation/physics/fem_transient/cancel", "/api/thermal/progress", "/api/mechanical/progress"):
        assert roles.is_worker_path(p), p


def test_every_worker_prefix_names_real_routes(app_routes):
    paths = [r.path for r in app_routes]
    paths.append("/mcp")                       # mounted (a Starlette sub-app), not a FastAPI route
    for pfx in roles.WORKER_PREFIXES:
        assert any(p == pfx or p.startswith(pfx + "/") for p in paths), "dead prefix %s" % pfx
    for ex in roles.WORKER_EXACT:
        assert ex in paths


def test_no_route_of_the_account_and_admin_surface_is_a_worker_path(app_routes):
    """The sign-in, admin, production, support and OAuth surface stays on the API colours (blue-green, no restart wait)."""
    for r in app_routes:
        p = r.path
        if p.startswith(("/api/auth", "/api/admin", "/api/production", "/api/support", "/oauth", "/api/account",
                         "/api/newsletter", "/api/verification", "/api/nda", "/api/design-agent")) or p == "/api/me":
            assert not roles.is_worker_path(p), p


# ── the compose file ─────────────────────────────────────────────────────────

def _mem_gb(s: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([gGmM])", str(s))
    assert m, s
    v = float(m.group(1))
    return v if m.group(2).lower() == "g" else v / 1024.0


def _cpus(spec: str) -> set:
    out = set()
    for part in str(spec).split(","):
        a, _, b = part.partition("-")
        out |= set(range(int(a), int(b or a) + 1))
    return out


def test_compose_services_and_roles():
    s = COMPOSE["services"]
    assert {"web", "api-blue", "api-green", "worker", "api-image", "motres-seed"} <= set(s)
    assert "api" not in s, "the single api service is gone (the cutover replaces it)"
    for name, inst in (("api-blue", "blue"), ("api-green", "green")):
        env = s[name]["environment"]
        assert env["MOTRES_ROLE"] == "api" and env["MOTRES_INSTANCE"] == inst
        assert env["MOTRES_WORKER_URL"] == "http://worker:8002"
        assert s[name]["profiles"] == ["stack"]
        assert s[name]["env_file"] == ["/etc/motres/api.env"]
    w = s["worker"]
    assert w["environment"]["MOTRES_ROLE"] == "worker" and w["profiles"] == ["stack"]
    assert w["command"] == ["python", "-m", "motor_ai_sim.worker"]
    assert "8002" in " ".join(w["healthcheck"]["test"])
    # a bare `docker compose up -d` must start nothing but the pages
    assert "profiles" not in s["web"]
    assert "depends_on" not in s["web"]
    assert "/srv/motres/web:/srv/web:ro" in s["web"]["volumes"]


def test_compose_one_image_three_services_tags_from_state():
    s = COMPOSE["services"]
    assert s["api-blue"]["image"] == "deploy-api:${BLUE_SHA:-latest}"
    assert s["api-green"]["image"] == "deploy-api:${GREEN_SHA:-latest}"
    assert s["worker"]["image"] == "deploy-api:${WORKER_SHA:-latest}"
    assert s["api-image"]["image"] == "deploy-api:${BUILD_SHA:-latest}" and "build" in s["api-image"]
    for n in ("api-blue", "api-green", "worker"):
        assert "build" not in s[n] and s[n]["pull_policy"] == "never"


def test_compose_resource_budget_on_the_ax42():
    """16 threads / 64 GB shared with the ERP: the worker keeps cpuset 0-11, the API colours live on 12-15 and never on
    the solver's cores; the memory ceilings stay clear of the ERP's 24 GB."""
    s = COMPOSE["services"]
    w, b, g, web = s["worker"], s["api-blue"], s["api-green"], s["web"]
    assert _cpus(w["cpuset"]) == set(range(0, 12)) and w["cpus"] == 12 and w["cpu_shares"] == 512
    assert not (_cpus(b["cpuset"]) & _cpus(w["cpuset"])) and b["cpuset"] == g["cpuset"]
    assert _cpus(b["cpuset"]) <= set(range(12, 16)) and b["cpus"] <= 3
    assert b["cpu_shares"] == g["cpu_shares"] == 512
    steady = _mem_gb(w["mem_limit"]) + _mem_gb(b["mem_limit"]) + _mem_gb(web["mem_limit"])
    worst = _mem_gb(w["mem_limit"]) + _mem_gb(b["mem_limit"]) + _mem_gb(g["mem_limit"]) + _mem_gb(web["mem_limit"])
    assert steady <= 41.5, "steady state must not exceed stage 1's 40 GB + the pages"
    assert worst <= 64 - 17, "both colours + worker leave the ERP and the host at least 17 GB"
    assert w["stop_grace_period"] == "120s"
    # the worker is the only service allowed to hold the 2 GB /dev/shm PARDISO wants
    assert w["shm_size"] == "2g"


def test_compose_mounts_identical_state_for_api_and_worker():
    s = COMPOSE["services"]
    vol = lambda n: sorted(s[n]["volumes"])  # noqa: E731
    assert vol("api-blue") == vol("api-green") == vol("worker")
    assert "/srv/motres/identity:/srv/motres/identity" in vol("worker")


def test_cluster_monitor_knows_the_new_containers_and_compose_names_the_job_one():
    from motor_ai_sim import cluster_monitor as CM
    for c in ("deploy-worker-1", "deploy-api-blue-1", "deploy-api-green-1", "deploy-web-1", "deploy-api-1"):
        assert c in CM.APP_CONTAINERS
    for n in ("api-blue", "api-green"):                     # the admin route runs in the API colours: they say which one is the queue
        env = COMPOSE["services"][n]["environment"]
        assert env["CLUSTER_JOB_CONTAINER"] == "deploy-worker-1"
        assert set(env["CLUSTER_APP_CONTAINERS"].split(",")) == {
            "deploy-api-blue-1", "deploy-api-green-1", "deploy-worker-1", "deploy-web-1"}


def test_instance_header_middleware(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.add_middleware(roles.InstanceHeaderMiddleware)

    @app.get("/x")
    def x():
        return {"ok": True}
    c = TestClient(app)
    monkeypatch.delenv(roles.ENV_INSTANCE, raising=False)
    assert "x-motres-instance" not in c.get("/x").headers
    monkeypatch.setenv(roles.ENV_INSTANCE, "green")
    assert c.get("/x").headers["x-motres-instance"] == "green"

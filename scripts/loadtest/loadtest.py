import asyncio
import argparse
import json
import random
import time
from collections import defaultdict
from typing import List, Dict, Any

import httpx

# Endpoints:
# 1. landing: GET /
# 2. user info: GET /api/me
# 3. catalog list: GET /api/catalog
# 4. open one motor: GET /api/catalog/{motor}/configure_context
# 5. Configure evaluate 3 points: GET /api/propellers/APC_10x4.5MR/point?rpm={rpm}&temp_c=25
# 6. 3-D model list: GET /api/model3d/models
# 7. one model's GLB: GET /api/model3d/D1/L12/model.glb with Range header

class LoadTestMetrics:
    def __init__(self):
        self.requests = []

    def record(self, endpoint: str, method: str, status: int, latency: float, error: str = None):
        self.requests.append({
            "endpoint": endpoint,
            "method": method,
            "status": status,
            "latency": latency,
            "error": error
        })

async def user_scenario(user_id: int, base_url: str, token: str, duration: int, 
                        use_fem: bool, metrics: LoadTestMetrics, dry_run: bool, target: dict = None):
    target = target or {"motor": "MOTOR_ID", "die": "DIE", "config": "CONFIG"}
    start_time = time.time()
    
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0)
    
    async def make_request(method: str, endpoint: str, label: str, **kwargs) -> httpx.Response:
        if dry_run:
            print(f"[User {user_id}] Plan: {method} {endpoint}")
            return None
        
        req_start = time.time()
        error = None
        status = 0
        resp = None
        try:
            resp = await client.request(method, endpoint, **kwargs)
            status = resp.status_code
            if status >= 400:
                error = f"HTTP {status}"
        except Exception as e:
            error = str(e)
            
        latency = time.time() - req_start
        metrics.record(label, method, status, latency, error)
        return resp
        
    async def think():
        if not dry_run:
            await asyncio.sleep(random.uniform(1.0, 5.0))
            
    while time.time() - start_time < duration or dry_run:
        # 1. landing
        await make_request("GET", "/", "landing")
        await think()
        
        # 2. api/me
        await make_request("GET", "/api/me", "api_me")
        await think()
        
        # 3. catalog list
        await make_request("GET", "/api/catalog", "catalog_list")
        await think()
        
        # 4. open one motor (catalog entry)
        await make_request("GET", f"/api/catalog/{target['motor']}/configure_context", "catalog_motor")
        await think()
        
        # 5. Configure evaluate at 3 operating points
        for rpm in [2000, 3000, 4000]:
            await make_request("GET", f"/api/propellers/APC_10x4.5MR/point?rpm={rpm}&temp_c=25", "evaluate_point")
            if not dry_run:
                await asyncio.sleep(0.5)
        await think()
        
        # 6. 3-D model list
        await make_request("GET", "/api/model3d/models", "model_list")
        await think()
        
        # 7. one model's GLB, as ranged GET
        await make_request("GET", f"/api/model3d/{target['die']}/{target['config']}/model.glb", "model_glb", headers={"Range": "bytes=0-1024"})
        await think()
        
        if use_fem:
            # Poll one FEM job
            await make_request("GET", "/api/simulation/physics/fem_transient?rpm=1000&current_a=10&steps=10", "fem_job")
            if not dry_run:
                for _ in range(3):
                    await asyncio.sleep(1.0)
                    await make_request("GET", "/api/simulation/physics/fem_transient/progress", "fem_poll")
            await think()

        if dry_run:
            break

    if not dry_run:
        await client.aclose()


def compute_stats(latencies: List[float]) -> dict:
    if not latencies:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
    latencies.sort()
    n = len(latencies)
    return {
        "count": n,
        "p50": latencies[int(n * 0.50)],
        "p95": latencies[int(n * 0.95)],
        "p99": latencies[int(n * 0.99)],
    }

async def discover_target(args, token) -> dict:
    """The motor and 3-D model the virtual users open: the --motor / --die / --config arguments, else the first
    catalogue entry and the first available model the server lists (placeholders in a dry run)."""
    target = {"motor": args.motor or "MOTOR_ID", "die": args.die or "DIE", "config": args.config or "CONFIG"}
    if args.dry_run or (args.motor and args.die and args.config):
        return target
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=30.0) as c:
        if not args.motor:
            r = await c.get("/api/catalog")
            items = r.json() if r.status_code == 200 else []
            items = items.get("items", items.get("motors", [])) if isinstance(items, dict) else items
            if items:
                target["motor"] = str(items[0].get("id"))
        if not (args.die and args.config):
            r = await c.get("/api/model3d/models")
            models = r.json() if r.status_code == 200 else []
            models = models.get("models", []) if isinstance(models, dict) else models
            first = next((m for m in models if m.get("available")), None)
            if first:
                target["die"], target["config"] = first["die"], first["config"]
    print("target:", target)
    return target


async def run_loadtest(args):
    tokens = [None] * args.users
    if args.token_file:
        try:
            with open(args.token_file, "r") as f:
                lines = [line.strip() for line in f if line.strip()]
                tokens = (lines * (args.users // len(lines) + 1))[:args.users]
        except Exception as e:
            print(f"Failed to read token file: {e}")
            return
            
    metrics = LoadTestMetrics()
    target = await discover_target(args, tokens[0])

    tasks = []
    ramp_delay = args.ramp / args.users if args.users > 0 else 0
    
    for i in range(args.users):
        async def delayed_start(user_idx, token):
            if not args.dry_run:
                await asyncio.sleep(user_idx * ramp_delay)
            await user_scenario(user_idx, args.base_url, token, args.duration, 
                                args.fem, metrics, args.dry_run, target)
        
        tasks.append(asyncio.create_task(delayed_start(i, tokens[i])))
        
    start_wall = time.time()
    await asyncio.gather(*tasks)
    total_time = max(time.time() - start_wall, 0.001)

    if args.dry_run:
        return

    # Process metrics
    endpoint_latencies = defaultdict(list)
    endpoint_errors = defaultdict(int)
    status_histogram = defaultdict(int)
    total_requests = len(metrics.requests)
    
    for req in metrics.requests:
        endpoint_latencies[req["endpoint"]].append(req["latency"])
        status_histogram[req["status"]] += 1
        if req["error"] or req["status"] >= 400:
            endpoint_errors[req["endpoint"]] += 1
            
    results = {
        "total_requests": total_requests,
        "requests_per_second": total_requests / total_time,
        "status_codes": dict(status_histogram),
        "endpoints": {}
    }
    
    print("\n=== Load Test Results ===")
    print(f"Total time: {total_time:.2f} s")
    print(f"Total requests: {total_requests} ({results['requests_per_second']:.1f} req/s)")
    print(f"Status codes: {dict(status_histogram)}\n")
    print(f"{'Endpoint':<15} | {'Count':<6} | {'Errors':<6} | {'p50 (ms)':<8} | {'p95 (ms)':<8} | {'p99 (ms)':<8}")
    print("-" * 65)
    
    for ep, lats in endpoint_latencies.items():
        stats = compute_stats(lats)
        errs = endpoint_errors[ep]
        results["endpoints"][ep] = {
            "count": stats["count"],
            "errors": errs,
            "p50_ms": round(stats["p50"] * 1000, 2),
            "p95_ms": round(stats["p95"] * 1000, 2),
            "p99_ms": round(stats["p99"] * 1000, 2),
        }
        print(f"{ep:<15} | {stats['count']:<6} | {errs:<6} | {stats['p50']*1000:<8.1f} | {stats['p95']*1000:<8.1f} | {stats['p99']*1000:<8.1f}")
        
    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {args.out}")

def main():
    parser = argparse.ArgumentParser(description="AeroStator Load Test")
    parser.add_argument("--base-url", required=True, help="Base URL of the API")
    parser.add_argument("--users", type=int, default=1, help="Number of concurrent users")
    parser.add_argument("--duration", type=int, default=30, help="Test duration in seconds")
    parser.add_argument("--ramp", type=int, default=0, help="Ramp up time in seconds")
    parser.add_argument("--token-file", help="File with one Bearer token per line")
    parser.add_argument("--fem", action="store_true", help="Enable FEM job submission")
    parser.add_argument("--out", default="results.json", help="Output JSON file path")
    parser.add_argument("--motor", help="Catalog id for Configure (default: the first entry of /api/catalog)")
    parser.add_argument("--die", help="Die of the 3-D model (default: the first available model)")
    parser.add_argument("--config", help="Configuration of the 3-D model (default: the first available model)")
    parser.add_argument("--dry-run", action="store_true", help="List planned requests without sending")
    
    args = parser.parse_args()
    asyncio.run(run_loadtest(args))

if __name__ == "__main__":
    main()

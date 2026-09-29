#!/usr/bin/env python3
"""eMotres node agent -- one host's load sample every INTERVAL s to the API.

Stdlib only (reads /proc; docker CLI if present).  Config from the environment
(the systemd unit loads /etc/motres-node-agent.env, mode 0600):

  MOTRES_MONITOR_URL    e.g. http://127.0.0.1:8080  or  https://eu1.emotres.com
  MOTRES_NODE_TOKEN     mnode_... (minted in Admin -> Servers)
  MOTRES_INTERVAL_S     default 15

Never sends command lines or environments: process NAMES only (comm).
Run with --once to print one sample as JSON and exit (no POST).
"""
from __future__ import annotations

import calendar
import json
import os
import pwd
import re
import subprocess
import sys
import time
import urllib.request

HZ = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
TOP_N = 15
_SKIP_FS = {"proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2",
            "overlay", "squashfs", "securityfs", "pstore", "bpf", "tracefs",
            "debugfs", "mqueue", "hugetlbfs", "fusectl", "configfs", "autofs",
            "nsfs", "ramfs", "binfmt_misc", "efivarfs", "fuse.lxcfs"}
_CID = re.compile(r"(?:docker[-/]|containerd[-/]|/)([0-9a-f]{64})")


def _read(p: str) -> str:
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def cpu_times():
    out = []
    for line in _read("/proc/stat").splitlines():
        if not line.startswith("cpu"):
            break
        v = [int(x) for x in line.split()[1:]]
        idle = v[3] + (v[4] if len(v) > 4 else 0)
        out.append((sum(v[:8]), idle))
    return out  # [total, cpu0, cpu1, ...]


def physical_cores() -> int:
    """Distinct (physical id, core id) pairs from /proc/cpuinfo -- the host's
    PHYSICAL core count, as opposed to ``cores`` in sample() below (the
    logical/thread count from /proc/stat, i.e. threads with SMT/hyperthreading
    counted separately). Falls back to the thread count when /proc/cpuinfo
    lacks physical/core ids (some VMs, some ARM hosts): we then assume no SMT
    rather than guessing at a ratio. Returns 0 (unknown) if /proc/cpuinfo is
    unreadable -- callers treat that as "don't show a physical-core count"."""
    pairs = set()
    threads = 0
    phys = core = None
    try:
        for line in _read("/proc/cpuinfo").splitlines():
            if line.startswith("processor"):
                threads += 1
                phys = core = None
            elif line.startswith("physical id"):
                phys = line.split(":", 1)[1].strip()
            elif line.startswith("core id"):
                core = line.split(":", 1)[1].strip()
                if phys is not None:
                    pairs.add((phys, core))
    except OSError:
        return 0
    return len(pairs) or threads


def meminfo():
    m = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, rest = line.partition(":")
        m[k] = int(rest.split()[0]) * 1024
    total, avail = m.get("MemTotal", 0), m.get("MemAvailable", m.get("MemFree", 0))
    st, sf = m.get("SwapTotal", 0), m.get("SwapFree", 0)
    return {"used": total - avail, "total": total}, {"used": st - sf, "total": st}


def disks():
    out, seen = [], set()
    for line in _read("/proc/mounts").splitlines():
        dev, mnt, fs = line.split()[:3]
        if fs in _SKIP_FS or mnt.startswith(("/proc", "/sys", "/run", "/snap", "/var/lib/docker")):
            continue
        if dev in seen:
            continue
        seen.add(dev)
        try:
            st = os.statvfs(mnt)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total <= 0:
            continue
        out.append({"mount": mnt, "used": (st.f_blocks - st.f_bfree) * st.f_frsize,
                    "total": total})
    return out


def net_bytes():
    rx = tx = 0
    for line in _read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if name == "lo" or name.startswith(("veth", "docker", "br-")):
            continue
        f = rest.split()
        rx += int(f[0])
        tx += int(f[8])
    return rx, tx


_USERS: dict = {}


def _user(uid: int) -> str:
    if uid not in _USERS:
        try:
            _USERS[uid] = pwd.getpwuid(uid).pw_name
        except KeyError:
            _USERS[uid] = str(uid)
    return _USERS[uid]


def proc_table():
    """{pid: (comm, uid, nice, ticks, rss_bytes, container_id)}"""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = _read(f"/proc/{d}/stat")
            comm = stat[stat.index("(") + 1: stat.rindex(")")]
            f = stat[stat.rindex(")") + 2:].split()
            ticks = int(f[11]) + int(f[12])
            nice = int(f[16])
            rss = int(f[21]) * PAGE
            uid = os.stat(f"/proc/{d}").st_uid
            m = _CID.search(_read(f"/proc/{d}/cgroup"))
            out[int(d)] = (comm, uid, nice, ticks, rss, m.group(1) if m else "")
        except (OSError, ValueError, IndexError):
            continue
    return out


def _docker(args):
    try:
        r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=20)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


_DOCKER_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def _parse_docker_time(s: str) -> float:
    """``docker ps`` prints CreatedAt in the daemon's local zone (e.g.
    ``2026-09-15 08:00:00 +0000 UTC``); we parse the naive timestamp and treat
    it as UTC, which is right on every host we run (UTC) and off by at most a
    few hours elsewhere -- good enough for an approximate "uptime" display."""
    m = _DOCKER_TS.match(s.strip())
    if not m:
        return 0.0
    try:
        t = time.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        return float(calendar.timegm(t))
    except (ValueError, OverflowError):
        return 0.0


def container_meta():
    """cid -> (name, created_ts).  One ``docker ps`` call, reused for both the
    per-process container name (proc_table's cgroup id) and the per-container
    "created" timestamp (containers(), for an approximate uptime)."""
    meta = {}
    for line in _docker(["ps", "--no-trunc", "--format",
                         "{{.ID}}\t{{.Names}}\t{{.CreatedAt}}"]).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        cid, name = parts[0], parts[1]
        created = _parse_docker_time(parts[2]) if len(parts) > 2 else 0.0
        meta[cid] = (name, created)
    return meta


def container_names(meta):
    return {cid: name for cid, (name, _created) in meta.items()}


def _size(s: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", s.strip())
    if not m:
        return 0.0
    mult = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4,
            "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}.get(m.group(2), 1)
    return float(m.group(1)) * mult


def containers(meta):
    """docker stats, one row per running container: name, CPU % (one core =
    100 %, so a busy multi-thread container reads > 100), RAM bytes, and an
    approximate "created" unix ts (0 if unknown) used for a Now-table uptime."""
    created_by_name = {name: created for _cid, (name, created) in meta.items()}
    out = []
    for line in _docker(["stats", "--no-stream", "--format", "{{json .}}"]).splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        name = d.get("Name", "")
        out.append({"name": name,
                    "cpu": float(str(d.get("CPUPerc", "0")).rstrip("%") or 0),
                    "mem": _size(str(d.get("MemUsage", "0B")).split("/")[0]),
                    "created": created_by_name.get(name, 0.0)})
    return out


class Sampler:
    def __init__(self):
        self.t = time.monotonic()
        self.cpu = cpu_times()
        self.net = net_bytes()
        self.procs = proc_table()

    def sample(self):
        t = time.monotonic()
        dt = max(t - self.t, 1e-3)
        cpu, net, procs = cpu_times(), net_bytes(), proc_table()
        pct = []
        for (a, ai), (b, bi) in zip(self.cpu, cpu):
            dtot = b - a
            pct.append(round(100.0 * (1 - (bi - ai) / dtot), 1) if dtot > 0 else 0.0)
        meta = container_meta()
        names = container_names(meta)
        rows = []
        for pid, (comm, uid, nice, ticks, rss, cid) in procs.items():
            prev = self.procs.get(pid)
            d = ticks - prev[3] if prev and prev[0] == comm else 0
            rows.append({"pid": pid, "name": comm, "user": _user(uid), "nice": nice,
                         "cpu": round(100.0 * d / HZ / dt, 1), "rss": rss,
                         "container": names.get(cid, cid[:12]) if cid else ""})
        top = {r["pid"]: r for r in sorted(rows, key=lambda r: -r["cpu"])[:TOP_N]}
        top.update({r["pid"]: r for r in sorted(rows, key=lambda r: -r["rss"])[:TOP_N // 2]})
        mem, swap = meminfo()
        s = {"hostname": os.uname().nodename, "ts": time.time(),
             "uptime_s": float(_read("/proc/uptime").split()[0]),
             "cores": len(pct) - 1, "cores_physical": physical_cores(),
             "cpu": {"total": pct[0] if pct else 0.0, "per_core": pct[1:]},
             "load": [float(x) for x in _read("/proc/loadavg").split()[:3]],
             "mem": mem, "swap": swap, "disks": disks(),
             "net": {"rx_bps": round((net[0] - self.net[0]) / dt),
                     "tx_bps": round((net[1] - self.net[1]) / dt)},
             "procs": list(top.values()), "containers": containers(meta)}
        self.t, self.cpu, self.net, self.procs = t, cpu, net, procs
        return s


def post(url: str, token: str, sample: dict) -> int:
    req = urllib.request.Request(
        url.rstrip("/") + "/api/admin/nodes/metrics", method="POST",
        data=json.dumps(sample).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def main() -> int:
    try:
        os.nice(10)
    except OSError:
        pass
    s = Sampler()
    if "--once" in sys.argv:
        time.sleep(1)
        print(json.dumps(s.sample(), indent=1))
        return 0
    url = os.environ.get("MOTRES_MONITOR_URL", "").strip()
    token = os.environ.get("MOTRES_NODE_TOKEN", "").strip()
    interval = float(os.environ.get("MOTRES_INTERVAL_S", "15") or 15)
    if not url or not token:
        print("MOTRES_MONITOR_URL and MOTRES_NODE_TOKEN are required", file=sys.stderr)
        return 2
    while True:
        t0 = time.monotonic()
        try:
            post(url, token, s.sample())
        except Exception as e:  # noqa: BLE001 -- keep going, log without secrets
            print(f"post failed: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        time.sleep(max(1.0, interval - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(main())

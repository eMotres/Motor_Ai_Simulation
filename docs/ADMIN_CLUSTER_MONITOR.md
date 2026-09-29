# Admin → Servers (cluster load monitor)

The Admin tab's **Servers** panel shows the load of every host in the project
and what the API is doing. Multi-node from day one: each host runs the same
small agent and reports to the API with its own token.

## Pieces

| piece | where | what |
|---|---|---|
| node agent | `deploy/node-agent/motres_node_agent.py` | stdlib Python, reads `/proc` + `docker ps/stats`; POSTs a sample every 15 s |
| systemd unit | `deploy/node-agent/motres-node-agent.service` | `Nice=10`, `CPUQuota=5%`, `MemoryMax=64M` |
| installer | `deploy/node-agent/install.sh` | copies the agent to `/opt/motres-node-agent`, token to `/etc/motres-node-agent.env` (0600), enables the unit |
| store | `src/motor_ai_sim/cluster_monitor.py` | node registry (sha256 token hashes), latest sample in memory, SQLite history |
| routes | `src/motor_ai_sim/routes/cluster.py` | `/api/admin/nodes*`, `/api/admin/cluster/*` |
| panel | `web/src/components/admin/ServersPanel.tsx` | node cards, 24 h / 7 d charts, top processes, jobs with Stop |

Data lives in `<identity dir>/cluster/` (the folder of `users.json`, i.e.
`/srv/motres/identity/cluster/` on the server); override with
`CLUSTER_MONITOR_DIR`. History: 1-min averages kept 24 h, 15-min averages kept
7 d. A node with no sample for 60 s is **offline**.

## Sample contents

CPU total and per core, load average, RAM, swap, disk per mount, network
rx/tx rate (physical interfaces), uptime, top processes (name, user, nice,
CPU %, RSS, container name via cgroup), docker containers with CPU/RAM.
The agent sends process **names only** — never command lines or environments.
The API additionally drops any `env`/`cmdline`/`args` keys and redacts
token-, password- and key-like strings before storing.

## API

| route | auth |
|---|---|
| `POST /api/admin/nodes/metrics` | node bearer token (`mnode_…`) |
| `GET /api/admin/nodes` | admin — nodes, latest sample, status, cluster totals |
| `POST /api/admin/nodes` `{name}` | admin — mint token (plaintext returned once; same name = re-key) |
| `POST /api/admin/nodes/{id}/revoke`, `DELETE /api/admin/nodes/{id}` | admin |
| `GET /api/admin/nodes/{id}/history?range=24h\|7d` | admin |
| `GET /api/admin/cluster/app` | admin — API p50/p95 (5 min), MCP calls/min + 429s, job queue |
| `POST /api/admin/cluster/jobs/{run_id}/stop` | admin |

## Install on a server

1. Admin tab → Servers → **Add server** → name (e.g. `eu1`) → **Create token**.
   Copy the token; it is shown once.
2. Copy `deploy/node-agent/` to the host and run the installer as root:

   ```bash
   scp -r deploy/node-agent root@<host>:/tmp/
   ssh root@<host> 'bash /tmp/node-agent/install.sh --url <API base> --token <mnode_…>'
   ```

   `<API base>`: on the host that runs the app stack use
   `http://127.0.0.1:8080` (the web container, which proxies `/api`); on any
   other host use the public origin, e.g. `https://eu1.emotres.com`.
3. The card turns green within ~15 s. Check with
   `systemctl status motres-node-agent` / `journalctl -u motres-node-agent`.

Requirements: `python3` (3.8+); `docker` CLI optional (for container names).
Rotate a token: Add server with the same name, re-run the installer.
Remove: `systemctl disable --now motres-node-agent`, then Revoke in the panel.

Dry run (prints one sample, sends nothing):
`python3 /opt/motres-node-agent/motres_node_agent.py --once`

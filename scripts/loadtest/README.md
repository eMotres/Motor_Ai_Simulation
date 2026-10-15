# Load Testing AeroStator

This directory contains a pure-Python, `httpx`-based load test script designed to simulate a realistic mix of user scenarios against the AeroStator API.

## Safety Note

**WARNING: NEVER point this script at the live API while the owner is solving or working.**

This test applies significant concurrent traffic that includes API calls and optionally small FEM jobs. Running it against the production instance while heavy physics tasks are running will cause contention and could interfere with actual engineering work or degrade performance for real users.

## Setup

No new dependencies are required. It uses `httpx`, which is already in the project's `requirements.txt`.

## Running Against a Sandbox API on the Server

To test the application safely, you should run it against a dedicated sandbox deployment.

1. Ensure your sandbox API server is up and accessible.
2. Run the `loadtest.py` script, passing the base URL to your sandbox instance.

Example for a 20-user test running for 60 seconds with a 5-second ramp-up:

```bash
python scripts/loadtest/loadtest.py --base-url http://sandbox.aerostator.local:8000 --users 20 --duration 60 --ramp 5
```

If you want to simulate authenticated users, create a text file with one Bearer token per line and pass it via `--token-file`:

```bash
python scripts/loadtest/loadtest.py --base-url http://sandbox.aerostator.local:8000 --users 20 --duration 60 --token-file tokens.txt
```

To include one small FEM job (and polling) per virtual user, add the `--fem` flag:

```bash
python scripts/loadtest/loadtest.py --base-url http://sandbox.aerostator.local:8000 --users 20 --duration 60 --fem
```

To preview the planned requests without sending any real traffic, use the `--dry-run` flag:

```bash
python scripts/loadtest/loadtest.py --base-url http://sandbox.aerostator.local:8000 --dry-run
```

## Monitoring Server Resources During the Run

While the load test is running, you should monitor the sandbox server's memory and CPU usage. You can use this bash one-liner on the server to sample `docker stats` and system memory (`free -m`) every 5 seconds:

```bash
while true; do date; docker stats --no-stream; free -m; echo "---"; sleep 5; done
```

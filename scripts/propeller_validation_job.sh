#!/usr/bin/env bash
# Scheduled validation of the propeller blade-element model against the UIUC measurement (follow-up 1 of PR #865).
#
# Runs ONE job through the us1 agent launcher (payload-only container of the deployed image, no network):
#   tests/test_propeller_validation_uiuc.py with PROPELLER_UIUC_REQUIRED=1, so missing data FAILS instead of skipping.
# The UIUC data files are not in the repository (no redistribution licence); they are read from $UIUC_SRC on the host.
#
# Loud failure: exit code != 0, the pytest tail in $ALERT_DIR/propeller_validation_FAILED_<UTC stamp>.md, and one line appended to
# $REPORT (the owning session's mail report).  A pass appends one line to $REPORT and removes nothing else.
#
# Usage (us1, user vadim):  scripts/propeller_validation_job.sh [git ref, default origin/pre-migration-freeze-2026-09-15]
# Weekly from cron:         17 4 * * 1  $HOME/work/motor_ai_sim/scripts/propeller_validation_job.sh >> $HOME/jobs/propval/cron.log 2>&1
set -euo pipefail

REF="${1:-origin/pre-migration-freeze-2026-09-15}"
REPO="${REPO:-$HOME/work/motor_ai_sim}"
UIUC_SRC="${UIUC_SRC:-$HOME/data/uiuc_propellers}"
PYLIB="${PYLIB:-$HOME/data/pytest_pylib}"          # pytest is not in the application image
TAG="${TAG:-propval}"
JOB="$HOME/jobs/$TAG"
ALERT_DIR="${ALERT_DIR:-$HOME/jobs/ALERTS}"
REPORT="${REPORT:-$HOME/jobs/mail_reports/US_propeller.md}"
STAMP="$(date -u +%Y-%m-%dT%H%MZ)"
FILES="apce_11x5.5_geom.txt apce_11x5.5_kt0471_6002.txt apce_11x5.5_kt0472_6000.txt apce_11x5.5_static_kt0467.txt"

fail() {
    mkdir -p "$ALERT_DIR"
    local f="$ALERT_DIR/propeller_validation_FAILED_$STAMP.md"
    { echo "# Propeller validation FAILED ($STAMP, ref $REF)"; echo; echo "$1"; echo;
      [ -f "$JOB/pytest.txt" ] && { echo '```'; tail -n 40 "$JOB/pytest.txt"; echo '```'; } ; } > "$f"
    echo "- $STAMP propeller validation FAILED (ref $REF): $1 - see $f" >> "$REPORT"
    echo "FAILED: $1 ($f)" >&2
    exit 1
}

for f in $FILES; do [ -f "$UIUC_SRC/$f" ] || fail "UIUC data file missing on the host: $UIUC_SRC/$f"; done
[ -d "$PYLIB/pytest" ] || [ -d "$PYLIB/_pytest" ] || fail "pytest bundle missing: $PYLIB"

git -C "$REPO" fetch -q origin || fail "git fetch failed in $REPO"
SHA="$(git -C "$REPO" rev-parse --short "$REF")" || fail "unknown ref $REF"

rm -rf "$JOB/repo" "$JOB/uiuc" "$JOB/pylib" "$JOB/run.log" "$JOB/pytest.txt"
mkdir -p "$JOB/repo" "$JOB/uiuc"
git -C "$REPO" archive "$REF" src tests config | tar -x -C "$JOB/repo"
for f in $FILES; do cp "$UIUC_SRC/$f" "$JOB/uiuc/"; done
cp -r "$PYLIB" "$JOB/pylib"
cat > "$JOB/run.sh" <<'RUN'
set -e
unset MOTOR_AI_SIM_CONFIG
export AUTH_ENFORCE=0
cd /job/repo
PROPELLER_UIUC_DIR=/job/uiuc PROPELLER_UIUC_REQUIRED=1 PYTHONPATH=/job/repo/src:/job/repo/tests:/job/repo:/job/pylib \
  timeout 1200 nice python3 -m pytest -v -rs -p no:cacheprovider --tb=short tests/test_propeller_validation_uiuc.py > /job/pytest.txt 2>&1
RUN
cat > "$JOB/job.json" <<JSON
{"schema": 1, "die": null, "configuration": null, "project": "own propeller", "production": null, "kind": "other",
 "session": "US · propeller", "points_total": 3, "note": "scheduled UIUC validation of the propeller BEMT pin, ref $SHA"}
JSON

sudo motres-agent-job submit "$TAG" 1 2 app background >/dev/null || fail "motres-agent-job submit refused"
for _ in $(seq 1 360); do                                  # up to 6 h in the queue + run
    [ -f "$JOB/run.log" ] && grep -q '^exit' "$JOB/run.log" && break
    sleep 60
done
grep -q '^exit' "$JOB/run.log" 2>/dev/null || fail "no result after 6 h (job $TAG)"
grep -q '^exit 0$' "$JOB/run.log" || fail "pytest failed ($(tail -n 1 "$JOB/pytest.txt" 2>/dev/null))"
grep -qE '[0-9]+ passed' "$JOB/pytest.txt" && ! grep -qE 'skipped|SKIPPED' "$JOB/pytest.txt" || fail "tests skipped or not run"
echo "- $STAMP propeller validation passed (ref $SHA): $(grep -E 'passed' "$JOB/pytest.txt" | tail -n 1)" >> "$REPORT"
rm -rf "$JOB/repo" "$JOB/pylib"

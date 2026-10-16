#!/usr/bin/env bash
# Daily pricing-data job (host side): storage sample + monthly usage files.
# Writes /srv/motres/usage/usage_YYYY-MM.{json,csv}: the current month to date
# every day, and on the 1st the finished previous month. Aggregates only.
set -euo pipefail
OUT=/srv/motres/usage
cd /opt/motres/app/deploy
install -d -m 0750 "$OUT"
run() { docker compose exec -T api python -m motor_ai_sim.usage_stats "$@"; }
run daily >/dev/null
for M in "$(date -u +%Y-%m)" $( [ "$(date -u +%d)" = "01" ] && date -u -d "yesterday" +%Y-%m ); do
  run monthly "$M" > "$OUT/usage_$M.json.tmp" && mv "$OUT/usage_$M.json.tmp" "$OUT/usage_$M.json"
  run monthly "$M" --csv > "$OUT/usage_$M.csv.tmp" && mv "$OUT/usage_$M.csv.tmp" "$OUT/usage_$M.csv"
done

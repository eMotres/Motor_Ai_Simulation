#!/usr/bin/env bash
# US catalog mirror job (docs/regions/DESIGN.md sections 7 and 12).  Host side, run as root by motres-catalog-mirror.timer on
# the US server every 15 minutes.  EU authors the OPEN library; US mirrors it read-only.
#
#   1. plan   pull the manifest and the per-die extracts from EU over the signed peer channel (WireGuard), compare them with
#             what this job installed last time, REFUSE if the US copy has local edits, write plan.json + fetch.txt
#   2. rsync  fetch exactly the files of fetch.txt from EU's READ-ONLY rsync module over WireGuard
#   3. apply  delete the delete list, re-hash what was fetched against the manifest, merge the per-die extracts into the US
#             stores (.duty_results.json, end_effect_passports.json, die_access.json), record the state
#
# Usage:   bash mirror_catalog_us.sh [--init] [--prune-leftovers]
#   --init              first run only: adopt the library copy that is already on US (without it a first run refuses)
#   --prune-leftovers   also delete files of dies EU does not mirror that an earlier hand copy left on US
#
# Exit codes: 0 ok / in sync | 2 local edits or a colliding file (nothing touched) | 3 first run without --init |
#             5 a fetched file did not match the manifest (state is consistent, the next run refetches) |
#             6 not a US server in multi-region mode | 7 a file or row that must go could not be removed, or a store is corrupt
#             (the unit shows "failed": a de-published die must not linger on US) |
#             75 EU or rsync did not answer (temporary; the unit treats it as success)
# Files fetched by rsync are owned by the application user (10001:10001, D755/F644) so the worker can delete them later; the
# work directory is created for that user too.
# Overridable for a rehearsal: MOTRES_APP MOTRES_MIRROR_DIR MOTRES_SHARED MOTRES_MIRROR_RSYNC MOTRES_BLUEGREEN RSYNC
#                              MOTRES_APP_UID (default 10001).
set -uo pipefail

APP=${MOTRES_APP:-/opt/motres/app}
WORK=${MOTRES_MIRROR_DIR:-/srv/motres/identity/mirror}          # the same path inside the worker container
SHARED=${MOTRES_SHARED:-/srv/motres/shared}
SRC=${MOTRES_MIRROR_RSYNC:-rsync://10.77.0.1/motres-library/}   # EU's WireGuard address, read-only module
BG=${MOTRES_BLUEGREEN:-$APP/deploy/bluegreen.sh}
RSYNC=${RSYNC:-rsync}
APP_UID=${MOTRES_APP_UID:-10001}                                # the image's `motres` user
MIRROR=(bash "$BG" exec worker python -m motor_ai_sim.regions.catalog_mirror)

note() { echo "$(date -u +%T) mirror: $*"; }

INIT=() PRUNE=()
for a in "$@"; do
  case "$a" in
    --init) INIT=(--init);;
    --prune-leftovers) PRUNE=(--prune-leftovers);;
    *) echo "usage: $0 [--init] [--prune-leftovers]" >&2; exit 64;;
  esac
done

# The work directory belongs to the application user: the worker container writes plan.json, the state file and the exclude
# list there.  (This script runs as root; never leave root-owned files in it.)
install -d -o "$APP_UID" -g "$APP_UID" -m 0750 "$WORK" || { note "cannot create $WORK"; exit 1; }
exec 9>>"$WORK/.lock"
chown "$APP_UID:$APP_UID" "$WORK/.lock" 2>/dev/null || true
flock -n 9 || { note "another run is in progress"; exit 0; }

"${MIRROR[@]}" plan "${INIT[@]}" "${PRUNE[@]}"
rc=$?
case $rc in
  0) ;;
  2) note "REFUSED: the US copy has local edits or a colliding file; nothing was changed (US never authors the library)"; exit 2;;
  3) note "REFUSED: first run; check the copy on US, then run once with --init"; exit 3;;
  4) note "EU did not answer; will retry"; exit 75;;
  6) note "this is not a US server in multi-region mode"; exit 6;;
  *) note "plan failed ($rc)"; exit "$rc";;
esac

if [ -s "$WORK/fetch.txt" ]; then
  note "fetching $(wc -l < "$WORK/fetch.txt") file(s)"
  # --files-from: exactly the listed paths, nothing else; the module is read-only on EU.  No --delete here: deletions are
  # decided by the plan and only for files this job installed.
  # -lpt without -r: only the listed files, never a directory tree.  --chown/--chmod: the files must be the application
  # user's, not root's (rsync runs as root here), or the worker could never delete a de-published die.
  if ! "$RSYNC" -lpt --chown="$APP_UID:$APP_UID" --chmod=D755,F644 --omit-dir-times --timeout=120         --files-from="$WORK/fetch.txt" "$SRC" "$SHARED/"; then
    note "rsync failed; will retry"
    exit 75
  fi
fi

"${MIRROR[@]}" apply
rc=$?
case $rc in
  0) note "done";;
  5) note "a fetched file did not match the manifest (EU changed it mid-run?); the next run refetches";;
  7) note "FAILED: something that must be removed could not be (or a store is corrupt); nothing was lost, the next run retries";;
  *) note "apply failed ($rc)";;
esac
exit $rc

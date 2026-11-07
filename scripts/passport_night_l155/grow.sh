#!/bin/bash
# Host-side. The main container starts NOW on the third lock (.runq3.lock) with 2 cpus / 6 GB. This script queues on
# the d40 lanes' lock files (.runq.lock = 8 cpus/30 GB, .runq2.lock = 6 cpus/22 GB): as soon as the d40 container of a
# lane exits and the lock is free, it takes the lock, holds it for the life of the main container and raises the main
# container's cpu/memory limits (docker update) and /work/workers.  Memory per FEM worker: PER_WORKER_GB (measured).
B=/opt/motres/compute/passport-l155
PER=$(cat $B/PER_WORKER_GB 2>/dev/null || echo 4)
C=ppl155-main
upd() {  # $1 = lane1 granted, $2 = lane2 granted
  cpus=$((2 + 8*$1 + 6*$2)); mem=$((6 + 30*$1 + 22*$2))
  docker update --cpus $cpus --memory ${mem}g --memory-swap ${mem}g $C >> $B/logs/grow.log 2>&1
  w=$(( mem / PER )); [ $w -gt $cpus ] && w=$cpus
  echo $w > $B/work/workers
  echo "$(date '+%F %T') lane grants $1 $2 -> cpus $cpus mem ${mem}g workers $w" >> $B/logs/grow.log
}
hold() {  # $1 = lock file, $2 = grant file
  flock $1 bash -c "touch $B/$2; while docker ps --format '{{.Names}}' | grep -qx $C; do sleep 20; done"
}
( hold /opt/motres/compute/.runq.lock grant1 ) &
( hold /opt/motres/compute/.runq2.lock grant2 ) &
while docker ps --format '{{.Names}}' | grep -qx $C; do
  g1=0; g2=0; [ -e $B/grant1 ] && g1=1; [ -e $B/grant2 ] && g2=1
  if [ "$g1$g2" != "$last" ]; then upd $g1 $g2; last="$g1$g2"; fi
  sleep 20
done
rm -f $B/grant1 $B/grant2
echo "$(date '+%F %T') grow exit" >> $B/logs/grow.log

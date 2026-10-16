#!/bin/sh
# runq.sh: run jobs.txt lines ("ENV|TAG SCRIPT args") one at a time, starting a
# job only while the 1-min load is < 10 and at most one other agent's heavy
# container runs.  touch stopq to stop after the current job.
D=${D:-/opt/motres/compute/coulomb-20260930b}
cd $D
n=$(cat qpos 2>/dev/null || echo 0)
while [ ! -f stopq ]; do
  line=$(sed -n "$((n+1))p" jobs.txt)
  if [ -z "$line" ]; then sleep 20; continue; fi
  others=$(docker ps --format '{{.Names}}' | grep -vE '^(erp-|deploy-|coul_)' | wc -l)
  l1=$(cut -d' ' -f1 /proc/loadavg | cut -d. -f1)
  if [ "$l1" -ge 10 ] || [ "$others" -ge 2 ]; then sleep 20; continue; fi
  envs=${line%%|*}; rest=${line#*|}
  EXTRA_ENV="$envs" sh $D/job.sh $rest
  n=$((n+1)); echo $n > qpos
done

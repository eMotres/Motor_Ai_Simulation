#!/bin/bash
# Host-side: /work/pause exists while any user job is running or queued on the live API (owner rule: yield to his jobs).
B=/opt/motres/compute/passport-l155
F=$B/work/pause; L=$B/logs/pausewatch.log; was=0
while [ ! -e $B/work/out/DONE_ALL ] && [ ! -e $B/STOP ]; do
  if grep -hoE '"status": *"(running|queued)"' /srv/motres/workspaces/*/.jobs.json >/dev/null 2>&1; then
    touch $F; [ $was = 0 ] && echo "$(date '+%F %T') PAUSE on" >> $L; was=1
  else
    rm -f $F; [ $was = 1 ] && echo "$(date '+%F %T') PAUSE off" >> $L; was=0
  fi
  sleep 30
done
rm -f $F; echo "$(date '+%F %T') pausewatch exit" >> $L

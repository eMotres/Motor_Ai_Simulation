#!/bin/bash
# usage: run.sh <lane> <name> [cpus mem]  - run /work/jobs/<name>.sh in the deployed API image.
# lane 0 = no lock, 1 cpu (set-up / checks); lane 3 = small lane: flock .runq3.lock, 2 cpus, 6 GB (free capacity now);
# lane 1 / 2 = the d40 lanes' lock files (queue behind the d40 job): 8 cpus/30 GB, 6 cpus/22 GB.
# nice 19, idle IO (same pattern as passport-d40-gap025).
B=/opt/motres/compute/passport-l155
IMG=$(cat $B/IMAGE)
lane=$1; name=$2
case "$lane" in
  3) PRE="flock /opt/motres/compute/.runq3.lock"; C=${3:-2}; MEM=${4:-6g};;
  1) PRE="flock /opt/motres/compute/.runq.lock"; C=8; MEM=30g;;
  2) PRE="flock /opt/motres/compute/.runq2.lock"; C=6; MEM=22g;;
  *) PRE=""; C=1; MEM=4g;;
esac
exec $PRE nice -n 19 ionice -c3 docker run --rm --name ppl155-$name --cpus $C --memory $MEM --memory-swap $MEM --user 0 \
  -v $B/work:/work -v $B/work/code/src:/app/src:ro \
  -e SHARED_ROOT=/work/shared -e WORKSPACES_ROOT=/work/wsroot -e SB_NO_WARM_CACHE=1 \
  -e MKL_NUM_THREADS=1 -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 -e MKL_THREADING_LAYER=SEQUENTIAL \
  -e PASSPORT_PAUSE_FLAG=/work/pause -e PASSPORT_IMAGE_DIGEST=$IMG -e PASSPORT_HOST_WORK=$B/work \
  -e MPLBACKEND=Agg -e HOME=/tmp -w /work $IMG nice -n 19 sh /work/jobs/$name.sh

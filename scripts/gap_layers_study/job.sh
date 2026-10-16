#!/bin/sh
# job.sh TAG SCRIPT [args...]  -- one sandboxed solver-direct run (Coulomb study).
# Throwaway container from motres-api:test, nice 19, ionice idle, 4 threads.
# Never touches /srv/motres or the live API; inputs are read-only copies.
# EXTRA_ENV may carry "-e MATLIB=<file in in/>" to pick the materials library.
D=${D:-/opt/motres/compute/coulomb-20260930b}
TAG=$1; SCRIPT=$2; shift 2
TH=${TH:-4}
ML=$(echo "$EXTRA_ENV" | sed -n 's/.*MATLIB=\([^ ]*\).*/\1/p')
SD=$(echo "$EXTRA_ENV" | sed -n 's/.*SRCDIR=\([^ ]*\).*/\1/p')
mkdir -p $D/out/cfg_$TAG
cp $D/in/motor_config.yaml $D/in/wire_stock.yaml $D/in/end_effect_3d.json $D/out/cfg_$TAG/
cp $D/in/${ML:-materials_library.yaml} $D/out/cfg_$TAG/materials_library.yaml
chmod -R a+rwX $D/out
T0=$(date +%s)
docker run --rm --name coul_$TAG --cpus $TH --entrypoint nice \
  -e OMP_NUM_THREADS=$TH -e MKL_NUM_THREADS=$TH -e OPENBLAS_NUM_THREADS=$TH \
  -e NUMEXPR_MAX_THREADS=$TH -e MPLBACKEND=Agg -e SB_NO_WARM_CACHE=1 $EXTRA_ENV \
  -e PYTHONPATH=/work/src -e MOTOR_AI_SIM_CONFIG=/work/out/cfg_$TAG/motor_config.yaml \
  -v $D/${SD:-src}:/work/src:ro -v $D/in:/work/in:ro -v $D/code:/work/code:ro \
  -v $D/out:/work/out \
  motres-api:test -n 19 ionice -c3 python /work/code/$SCRIPT --inp /work/in \
  --out /work/out/$TAG "$@" > $D/out/$TAG.log 2> $D/out/$TAG.err
RC=$?
echo "$TAG exit $RC $(( $(date +%s) - T0 ))s load=$(cut -d' ' -f1 /proc/loadavg) matlib=${ML:-materials_library.yaml}" >> $D/out/done.txt

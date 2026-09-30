#!/bin/sh
# job.sh TAG THREADS [ripple_ref.py args...]
# One sandboxed solver-direct run: throwaway container from motres-api:test,
# nice 19 / ionice idle, THREADS BLAS threads.  Never touches /srv/motres or the
# live API.  Inputs are read-only copies under $D/in.
D=/opt/motres/compute/ripple-20260930
TAG=$1; TH=$2; shift 2
mkdir -p $D/out/cfg_$TAG
cp $D/in/motor_config.yaml $D/in/materials_library.yaml $D/out/cfg_$TAG/
chmod -R a+rwX $D/out
date +%s > $D/out/$TAG.t0
docker run --rm --name ripple_$TAG --cpus $TH --entrypoint nice \
  -e OMP_NUM_THREADS=$TH -e MKL_NUM_THREADS=$TH -e OPENBLAS_NUM_THREADS=$TH \
  -e NUMEXPR_MAX_THREADS=$TH -e MPLBACKEND=Agg -e SB_NO_WARM_CACHE=1 \
  -e PYTHONPATH=/work/src -e MOTOR_AI_SIM_CONFIG=/work/out/cfg_$TAG/motor_config.yaml \
  -v $D/src:/work/src:ro -v $D/in:/work/in:ro -v $D/code:/work/code:ro \
  -v $D/out:/work/out \
  motres-api:test -n 19 ionice -c3 python /work/code/ripple_ref.py --inp /work/in \
  --out /work/out/$TAG "$@" > $D/out/$TAG.log 2> $D/out/$TAG.err
echo "$TAG exit $? $(( $(date +%s) - $(cat $D/out/$TAG.t0) ))s" >> $D/out/done.txt

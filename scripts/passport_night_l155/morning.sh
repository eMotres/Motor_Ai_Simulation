#!/bin/bash
# usage: bash morning.sh check|assemble|card|test   (FEM-free, lane 0: 1 cpu, nice 19)
B=/opt/motres/compute/passport-l155; cd $B
cmd=$1
cat > work/jobs/m_$cmd.sh <<INNER
set -x
SPEC=/work/spec/CIANO10_200_opt.yaml
case "$cmd" in
 check)
  python3 - <<'PY'
import json, os
bad = []; n = 0
for l in open('/work/out/L155/results.jsonl'):
    r = json.loads(l); n += 1
    if not r.get('ok'): bad.append((r['id'], str(r.get('error'))[:160]))
print('records', n, 'failed', len(bad))
for b in bad: print('  FAILED', b)
for f in ('demagseq_L155_1.5','demagseq_L155_2','demagseq_L155_2.5','demagseq_L155_1','demagseq_L155_1.25','stage_a_L155'):
    print(f, 'ok' if os.path.exists('/work/out/full/%s.json' % f) else '-')
st = json.load(open('/work/out/L155/state.json'))
print('demag_limit_I_rms (winding A):', st.get('demag_limit_I_rms'), '|', str(st.get('demag_limit_source'))[:200])
print('checks_plan' in st, 'loss_plan3' in st)
PY
  ;;
 assemble)
  python -u /work/code/scripts/passport_pilot.py --spec \$SPEC assemble
  python -u /work/code/scripts/passport_card.py --spec \$SPEC --work /work --html-dir /work/out/html --records-dir /work/out/full
  ;;
 card)
  mkdir -p /work/out/card
  python -u /work/code/scripts/build_l155_catalog_card.py --full /work/out/full/passport_L155.json --snapshot /work/out/L155/snapshot.json > /work/out/card/cat_ciano10_200_opt_l155.json
  ls -la /work/out/card
  ;;
 test)
  cd /work/code && PYTHONPATH=/work/code/src python -c "import sys; sys.path.insert(0,'tests'); import test_passport_delta_basis as t; t.test_voltage_limit_follows_snapshot_basis(); t.test_spec_loads(); t.test_equivalent_star_transform(); print('3 tests passed')"
  ;;
esac
INNER
bash run.sh 0 m_$cmd 2>&1 | grep -v 'core loss\|sanitize' | tail -60

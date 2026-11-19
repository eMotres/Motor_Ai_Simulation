import test from 'node:test';
import assert from 'node:assert/strict';
import { pointTip } from '../simPoints.ts';

const cfg = { name: 'L12', build_sig: 'a', duties: [] };
const duty = { name: 'rated', mode: 'motor', rpm: 3000, result: { efficiency_pct: 90.7, build_sig: 'a' } };

test('a duty tooltip shows both efficiencies when the configuration states its leads and board', () => {
  const t = pointTip({ ...duty, terminal_loss: { P_leads_board_W: 23.8, efficiency_at_terminals: 0.886 } }, cfg, (x) => x);
  assert.match(t, /η 90\.7 %/);
  assert.match(t, /η at terminals 88\.6 % \(leads \+ board 23\.8 W\)/);
});

test('without terminal_loss the tooltip is exactly what it was', () => {
  const t = pointTip(duty, cfg, (x) => x);
  assert.ok(!t.includes('terminals'));
  assert.match(t, /η 90\.7 %/);
});

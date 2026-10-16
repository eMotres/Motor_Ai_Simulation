/**
 * The catalogue's "fits the selected board" filter, tested without a browser.
 *
 * `footprintFilter.ts` has no runtime imports (its one import is a type), so
 * Node's type stripping loads the SHIPPED module directly — no verbatim copy
 * to drift from it.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { ANY_BOARD, boardGroups, fitsBoard, boardWarning } from '../footprintFilter.ts';

const row = (part, group, warn = null) => ({
  part,
  footprint: group == null ? null : {
    package_outline_id: 'X', land_pattern_ref: null, body_height_mm: 2.35,
    top_tab_mm: null, compatibility_group: group, group_warning: warn,
  },
});

const ROWS = [
  row('IMCQ120R004M2H', 'qdpak_750_1200', 'Same land pattern, but top tab not published on 2 of 2'),
  row('IMDQ75R004M2H', 'qdpak_750_1200', 'Same land pattern, but top tab not published on 2 of 2'),
  row('IQE050N08NM5SC', 'whson8_tson8'),
  row('NO_FOOTPRINT', null),
];

test('groups: each present group once, sorted; a card without a block adds none', () => {
  assert.deepEqual(boardGroups(ROWS), ['qdpak_750_1200', 'whson8_tson8']);
});

test('any board shows every row, including one without a footprint', () => {
  assert.equal(fitsBoard(ROWS, ANY_BOARD).length, 4);
});

test('a board shows only its group — a card with no footprint never fits', () => {
  assert.deepEqual(fitsBoard(ROWS, 'qdpak_750_1200').map(r => r.part),
                   ['IMCQ120R004M2H', 'IMDQ75R004M2H']);
  assert.deepEqual(fitsBoard(ROWS, 'whson8_tson8').map(r => r.part), ['IQE050N08NM5SC']);
  assert.deepEqual(fitsBoard(ROWS, 'nobody'), []);
});

test('warning is the backend line for that group, null when it agrees or no board', () => {
  assert.match(boardWarning(ROWS, 'qdpak_750_1200'), /top tab not published/);
  assert.equal(boardWarning(ROWS, 'whson8_tson8'), null);
  assert.equal(boardWarning(ROWS, ANY_BOARD), null);
});

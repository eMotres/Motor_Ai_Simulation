# wire_omit: empty positions of the wire grid

`geometry.wire_omit` (opt-in, absent or 0 = the full `num_wires_per_slot` x `wire_split` grid) is ONE integer: the sum of `2**(row*8 + col)`
over the EMPTY positions. `row` 0 is the wire row under the yoke, `col` 0 is the strip next to the tooth.

| empty position | value |
|---|---|
| (0, 0) | 1 |
| (0, 1) | 2 |
| (0, 2) | 4 |
| (1, 0) | 256 |
| (1, 1) | 512 |
| (1, 2) | 1024 |

Several empty positions: add the values. The PUT accepts the integer only (a list such as `[[0, 0]]` is refused with a 422 naming the form);
the list form exists only as a `const` in the per-die Fusion map, where it is converted to this integer.

Refused with a 422 that names the grid it saw (`PUT /api/geometry`, and every mesh route for a `geo=` override): a position outside the
rows x strips grid, any empty position with `wire_parallel` > 1, a row with no strip left. `winding.wire_omit_mask([(row, col), ...])` builds the integer.

# Rotor audit: owner's Fusion Ø12 12s10p rotor vs our builder (2026-09-25)

Die `CIANO14 12_40 Ø12 12s10p` / L10, imported from the owner's OLD Fusion
model (`ExportedParameters.csv`). The server validation reported
"rotor core split into 31 disconnected pieces". Owner: «проверь геометрию, там
с ротором косяки», then: no fitting. Find why the same values do not give the
same rotor.

## Result

31 pieces = hub + 10 loose spokes + 20 lip islands at the OD. There are two
separate causes:

1. **Builder defect (c): 10 loose spokes.** The rotor_hole < 1 opening was a
   rectangle cut to a fixed depth of `magnet_up_gap + 2 mm`. On a 2 mm magnet
   it reached the magnet's inner end. The opening is 0.884 mm wide, so its
   corners stood 0.08 mm outside the 0.717 mm magnet end, inside the 0.128 mm
   iron web between pockets, and cut every spoke off the hub. Fixed in
   `_pocket_cut_depth` / `_pocket_cut_depth_limit`: the cut now stops where the
   magnet side becomes narrower than the opening. Every existing die is
   unchanged (pinned in `tests/test_pocket_opening_taper.py`).
2. **Mapping error (a): 20 lips.** Fusion's `mag_down_h` = 2 mm is **not** our
   `magnet_down_height`. In our model that parameter is the radial foot: the
   magnet side runs radially at the fill_down angle for that length and then
   slants to the top corner. With a 2 mm foot on a 2 mm magnet, the radial side
   reaches the OD, and the iron above the slanted face is cut off.

## Per-parameter comparison (measured on the owner's picture, 0.01253 mm/px)

| Fusion | value | ours | ours builds | picture | match |
|---|---|---|---|---|---|
| rotor_up_r = stator_down_r − gap | 3.358 | rotor_outer_radius | 3.358 | OD used as the scale | yes |
| rotor_down_r = rotor_up_r − magnet_h | 1.358 | magnet_r = rotor_ir + rotor_house_height | 1.358 | pocket bottom ≈ 1.37 | yes |
| r_housing_h | 0.3 | rotor_house_height | ring 1.058–1.358 | shaft r ≈ 1.09 | yes (3 %) |
| rotor_down_h | 0.5 | — | — | ring is 0.3, not 0.5 | orphan |
| magnet_fill_down | 0.85 | magnet_fill_down | half-width 0.358 at r 1.358 | 0.37 (line extrapolated) | yes |
| magnet_fill_up | 0.6 | magnet_fill_up | half-width 0.610 at r 3.258 | 0.605 max at r 3.1 | yes |
| mag_sh | 0.1 | magnet_up_gap | top r 3.258 | consistent | yes |
| mag_hole | 0.7 | rotor_hole (<1 = tabs) | opening half-width 0.442 | 0.445 at r 3.34 | yes |
| mag_r | 0.2 | magnet_fill_radius | top corners only | all 4 pocket corners rounded | (b) bottom fillet not expressible |
| **mag_down_h** | **2** | **magnet_down_height** | radial side to r 3.358: half-width 0.66 at r 2.5 | **0.528 at r 2.5: straight side, no foot** | **no** |

The pocket half-width in the picture grows linearly: 0.393 at r 1.5, 0.476 at
r 2.1, 0.528 at r 2.5, 0.587 at r 2.9, 0.605 at r 3.1 (slope 0.1325). Our
trapezoid with a 0 foot has slope 0.133. The picture's side is therefore the
straight line from the fill_down bottom corner to the fill_up top corner.

## Mapping decision

The value that gives the owner's rotor is `magnet_down_height = 0`. The
validator used to refuse 0, although the schema allows min 0; this is now
fixed. In `scripts/fusion_param_common.py`, the `magnet_down_height` ↔
`mag_down_h` entry must not be a plain rename. Two readings fit both the CSV
and the picture:

- The Fusion dimension is measured from the rotor OD: ours = magnet_h −
  mag_down_h = 0.
- `mag_down_h` is unused by the rotor sketch.

Owner to confirm by opening the dimension in the Fusion sketch.

## Rejected

- Fitting the values to the picture: owner said no.
- `rotor_down_h` → rotor_house_height: the picture ring is 0.3, not 0.5.
- `rotor_hole ≥ 1`: the picture has tabs, and the opening matches rotor_hole
  0.7 < 1.

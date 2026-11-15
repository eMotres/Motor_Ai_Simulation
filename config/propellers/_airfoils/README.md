# Airfoil library (stage P0 of docs/PROPELLER_MODULE_PLAN_2026-11-15.md)

Candidate airfoils for the own propeller (10 / 11 / 12 / 13 in, chord Reynolds about 2e4 - 3e5).

* `naca4-XXXX` ids are generated from the published NACA 4-digit formula in `src/motor_ai_sim/propeller_airfoil.py`; no data file, no data licence.
* A family of coordinate files goes in `<family>/<name>.dat` (Selig format: a name line, then x y from the upper trailing edge round the leading edge
  to the lower trailing edge) with `<family>/_terms.yaml`:

```yaml
terms_status: permitted | restricted | unknown   # unknown (or no file) = never offered for commercial use
source_url: ...        # where the coordinates were taken
accessed: '2026-11-15'
terms_quote: ...       # the licence / terms sentence that was read
confirmed_by: ...      # the person who confirmed that commercial use is allowed
```

**No coordinate file ships yet.** The UIUC Airfoil Data Site (https://m-selig.ae.illinois.edu/ads.html) states no licence we could find; a written
question was sent on 2026-11-15 together with the propeller database one. Each family that is added records its terms here first.

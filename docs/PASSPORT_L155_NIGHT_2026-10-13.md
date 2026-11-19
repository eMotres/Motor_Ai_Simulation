# Morning: L155 passport (CIANO10 200 opt / "L155 motor"), card `cat_ciano10_200_opt_l155` (PRIVATE, not published)

Job dir `/opt/motres/compute/passport-l155/` (server, root). Prepared by Claude Sonnet 5.5, 2026-10-13. Code = deployed
commit c913b3e3 + PATCH_vs_c913b3e3.diff (`work/code/`), image of the d40 job, inputs snapshot `work/inputs/` (sha256 of the
server files in `work/inputs/die200/SERVER_SHA256`). Nothing in /srv/motres is touched.

## How it runs (already started, unattended)
* One container `ppl155-main`, nice 19 / idle IO, started 23:11 with the free capacity (2 cpus, 6 GB, lock `.runq3.lock`).
* `grow.sh` queues on the d40 lock files `.runq.lock` (8 cpus/30 GB) and `.runq2.lock` (6 cpus/22 GB): when a d40 lane
  ends, it takes that lock and raises the container with `docker update` (up to 16 cpus / 58 GB) and `work/workers`.
* `pausewatch.sh`: `work/pause` while any user job runs/queues on the live API; no new FEM job starts meanwhile.
* `driver.py` (retry wrapper `work/jobs/main.sh`, resumable): calib, probe, hot (current x angle grid, rated + peak currents),
  loss (rated/peak/0.5 I0 x speeds), demag, cold, extra, checks, refine; then the demag-limit pairs (whole tasks, in
  parallel with cold..refine); then loss2, loss3 (up to the measured demag limit); lastly 3-D Stage A (own stack).
* Local PC: NOT used (no netgen mesher in the local Python, 7 GB RAM free) - a different mesher would be a different passport.

## Commands (server, root)
```
cd /opt/motres/compute/passport-l155
bash status.sh            # progress; work/out/DONE_ALL = everything incl. Stage A finished; STAGE1_DONE = through loss3
bash morning.sh check     # failed FEM jobs, missing task outputs, demag limit
bash morning.sh assemble  # stage-1 record + full card JSON/HTML -> work/out/full/passport_L155.json, work/out/html
bash morning.sh card      # -> work/out/card/cat_ciano10_200_opt_l155.json (owner = vadim@motresres.com, private)
bash morning.sh test     
touch STOP                # (emergency) stops pausewatch; docker stop ppl155-main stops the job (resumable)
```
Installing the card into `/srv/motres/shared/motor_catalog.json` and the store file is a separate, owner-approved step.

## Input findings and decisions (2026-10-13)
* Geometry guard (`family.duty_geometry_diff`): both duties of "L155 motor" match the die (0 blocking differences).
  The duties carry no `_geoSig` stamp. Default (first) duty = "rated 1x9 mm"; peak = "peak 1x9 mm".
* DELTA winding: the passport jobs call the solver directly, the route divides the terminal current by sqrt(3). The
  snapshot of a delta machine therefore holds the WINDING current (`current_basis`), the voltage limit is m*Vdc
  (`psimap.set_winding_basis`), route-based tasks (coupled, cooling, pwm, propanchor) refuse on this basis. The card
  builder converts to the catalogue's equivalent star (I x sqrt3, V and psi / sqrt3, R and L / 3).
* Hot map = the rated duty's coupled-loop temperatures (magnet 104.2, winding 97.8 C). The peak duty's own state
  (165.1 / 139.2 C) is not a second hot map.
* NOT computed (no inputs): coupled EM-thermal and cooling studies (no cooling settings are stored for this
  configuration; the Thermal tab holds an air-cooled Ø40 setup), PWM / controller anchors (24 kHz SiC bridge, no FEM
  anchor class), rotor stress / critical speeds (no Mechanical-tab inputs), propeller studies.
* Controller margin m = 0.95 (passport placeholder, default pending owner); the Ø40 controller's 0.89 is not carried
  over (`plan.m_margin` now also drives loss2).
* Iron-loss table (B10AHV900M) is extrapolated at the peak flux density of this machine (a few % of the watts).

# Archived motor torque closure checkpoint — 2026-09-23

`scripts/torque_offline_closure.py` reads only saved run04 waveform/P2 files, run04's capture summary, the run06 assembled materials/curves and energy review, and the run09 virtual-work review. It verifies actual file bytes against their saved SHA-256 records before comparison, including the run04 waveform hash recorded in `full_state_summary.json`. This binds the waveform to the archived capture summary, not independently to the solved P2 states; no per-frame waveform–field identity is certified. It uses the **actual saved frame 48** as the endpoint of frames 0–48 (one electrical period, `2π/7 = 0.8975979010` mechanical rad) and integrates the archived three-phase `Σ i dψ` by trapezoidal path segments. No endpoint is synthesized for period two because frame 96 was not saved. The separately reported cyclic value instead closes frames 0–47 to frame 0 and is explicitly an artificial closure comparison.

| Read-only diagnostic | Value |
|---|---:|
| Terminal path work per mechanical radian, saved endpoint | 0.4084804159 N·m |
| Cyclic first-period terminal work candidate | 0.4084789433 N·m |
| Cyclic minus saved-endpoint path | −1.47254e−6 N·m |
| Reconstructed whole-machine magnetic-potential change, frames 0→48 | +1.09020785e−6 J |
| Potential change divided by signed angle | +1.21458e−6 N·m |
| Path work minus that arithmetic correction | 0.4084792013 N·m |
| Raw Maxwell first-period mean | 0.4081997845 N·m |
| Hybrid first-period mean | 0.4096479170 N·m |
| Frozen-current one-/three-cell virtual-work secants at frame 0 | 0.4093778320 / 0.4081688826 N·m |

The path–Maxwell mean difference is +0.0002806314 N·m. This is a measured discrepancy, not an error bar. The frozen-current secants are local derivatives at one current/angle, so neither is an independent reference for the period mean. The run06 integral is the *magnetic potential* whose field derivative matches the P2 weak form, including the `−Hc·B` PM term. Subtracting its endpoint change from terminal work is shown as arithmetic only; a complete electromechanical port-energy identity with PM reference and mechanical work has **not** been established by this comparison.

**Status: uncertified.** Run04's saved `ψ` predates the corrected reciprocal no-eddy P2 linkage functional; run09 measured up to `5.65267315e−9 Wb` difference from its source pairing. The mapped rotor field differs across the period, and only endpoint pairs have full P2 state/energy reconstruction. Neither a strict periodic-state tolerance nor a converged angular/mesh error bound is available. The diagnostic therefore does not pass `True` into the eligibility-gated terminal-work helper and does not change the >1 A selector. The next production-data gate must capture the corrected phase linkage alongside the matching full P2/material states over a complete period, establish the discrete port-energy identity and mapped-state tolerance, then compare period work with an independently refined virtual-work or Maxwell reference.

Verification: `C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_torque_offline_closure.py --noconftest -o addopts= -p no:cacheprovider -q` passed (2 tests, synthetic temporary fixtures including tampered-material rejection). `C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe scripts/torque_offline_closure.py` reproduced the archived values in 0.29 s. No FEM solve, live API, config change, or production edit. Codex Sol performed the physics audit; parent handles journal/dataset logging.

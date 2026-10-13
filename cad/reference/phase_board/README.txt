CIANO14 40/12 phase (coil-connection) board rev C, 4 layers x 6 oz — coloured STEP, 2026-10-13
Source: frozen production release PCB_CIANO14_40\RELEASE_2026-10-11_FAB_4L_6OZ\kicad\ciano40_4L_revC.kicad_pcb (SHA256 b80d7b91..., unchanged).
Export from a SCRATCH COPY with: dielectric 3 x 0.25 mm (fab statement 10-12/13) -> copper-to-copper 1.59 mm, total 1.63 incl. mask (fab: 1.6 mm +-10 %);
stackup colours set (mask Green, silk White, FR4 natural, ENIG); kicad-cli 10 pcb export step --include-tracks --include-pads --include-zones
--include-inner-copper --include-silkscreen --include-soldermask --subst-models. The board has no silkscreen items.
Components: 3 phase pins CIANO40-PIN-C rev 7 (D2.0, collar D3.0 x 1.15 seated on the N face, tail 2.05 through to the A side, mating 2.70) and
the 6 selective 35 um U-notch wall models. Colours in the STEP: mask green, FR4 body, ENIG copper gold, pins/walls grey (4 COLOUR_RGB, 103 styled items).
Check: FreeCAD 1.0.2 Part.read -> 110 solids, 0 invalid. PNG previews: *_top.png, *_bottom.png, *_top_iso.png (kicad-cli pcb render).

FRAME / DATUM
STEP frame = KiCad absolute: board centre at X 100, Y -100 (KiCad Y flipped); +Z = F side = layer A (faces the motor); board body Z 0..1.17,
A copper top Z +1.38, N copper surface (B.Cu, bare pads) Z -0.21.
Motor model frame (CIANO14_40_motor_model, +Z toward the front/propeller, origin = stack centre): translate by
  L12: (-100, +100, -12.615)  -> N copper surface at Z = -12.825 (= coil_lead_end_z -15.35 + 2.525)
  L20: (-100, +100, -16.615)  -> N copper surface at Z = -16.825 (= coil_lead_end_z -19.35 + 2.525)
No rotation/mirror. Check: pin J13 -> (6.606, 12.964) = r 14.55 at 63 deg. Real thickness varies 1.44..1.76: keep N fixed (the board grows toward the motor).

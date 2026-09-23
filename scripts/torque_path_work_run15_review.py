"""Read-only nested-grid terminal-work sensitivity for saved run15."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from motor_ai_sim.simulation.torque_path_work import (  # noqa: E402
    spline_terminal_flux_work_per_mechanical_radian,
)
from torque_offline_closure import terminal_work_path  # noqa: E402

RUN = ROOT / "scratchpad" / "torque_corrected_state_20260923_run15"


def analyze(directory=RUN):
    directory = Path(directory)
    review = json.loads((directory / "capture_review.json").read_text())
    waveform = directory / "raw_waveforms.npz"
    if hashlib.sha256(waveform.read_bytes()).hexdigest() != review["raw_waveforms_sha256"]:
        raise ValueError("Saved waveform hash differs")
    with np.load(waveform, allow_pickle=False) as data:
        current = data["currents_a"][:, :49]
        psi = data["flux_linkages_wb"][:, :49]
        theta = np.deg2rad(data["rotor_angle_deg"][:49])
    if current.shape != (3, 49) or not np.isclose(theta[-1]-theta[0], 2*np.pi/7, atol=1e-12):
        raise ValueError("Saved first-period path differs")
    rows = []
    for stride in (1, 2, 4):
        subset = slice(None, None, stride)
        i, flux, angle = current[:, subset], psi[:, subset], theta[subset]
        rows.append({
            "intervals": int(angle.size-1),
            "trapezoid_Nm": terminal_work_path(i, flux, angle),
            "cubic_gauss_Nm": spline_terminal_flux_work_per_mechanical_radian(
                i, flux, angle).mean_work_per_mechanical_radian,
        })
    for row in rows[1:]:
        row["cubic_relative_to_48"] = abs(row["cubic_gauss_Nm"]-rows[0]["cubic_gauss_Nm"])/abs(rows[0]["cubic_gauss_Nm"])
        row["trapezoid_relative_to_48"] = abs(row["trapezoid_Nm"]-rows[0]["trapezoid_Nm"])/abs(rows[0]["trapezoid_Nm"])
    return {"status": "uncertified_angular_sampling_diagnostic",
            "waveform_sha256": review["raw_waveforms_sha256"],
            "source": "same saved 49 endpoint-inclusive samples from first run15 electrical period",
            "nested_grids": rows,
            "note": "Cubic interpolation retains supplied samples but supplies no new FEM field information; agreement does not certify mechanical torque or suppress aliasing."}


if __name__ == "__main__":
    print(json.dumps(analyze(), indent=2, sort_keys=True))

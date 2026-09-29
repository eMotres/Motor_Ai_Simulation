"""Analyze immutable raw JSON reports; no solver imports or FEM calls."""
import os
import sys
sys.dont_write_bytecode = True
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
    handle = kernel.GetCurrentProcess()
    assert kernel.SetPriorityClass(handle, 0x4000), ctypes.get_last_error()
    assert kernel.GetPriorityClass(handle) == 0x4000

import argparse
import hashlib
import json
from pathlib import Path
import numpy as np


def read(path):
    data = json.loads(path.read_text())
    values = np.asarray(data["torque"]["maxwell_raw_Nm"]["values"], float)
    mechanical = np.asarray([f["actual_angle_deg"] for f in data["actual_frames"]], float)
    poles = int(data["geometry"]["num_poles"])
    electrical = mechanical*np.pi/180*(poles/2)
    assert values.size == mechanical.size == 72 and np.isfinite(values).all()
    assert np.max(abs(np.diff(electrical)-2*np.pi/72)) < 1e-12
    orders = np.arange(values.size//2+1)
    coefficients = np.exp(-1j*orders[:, None]*electrical[None, :])@values/values.size
    stored = data["torque"]["maxwell_raw_Nm"]["spectrum"]
    assert stored["order"] == orders.tolist()
    stored_coeff = np.asarray(stored["coefficient_real"])+1j*np.asarray(stored["coefficient_imag"])
    assert np.max(abs(stored_coeff-coefficients)) < 1e-11
    amplitudes = abs(coefficients)*2
    amplitudes[0] = abs(coefficients[0])
    amplitudes[-1] = abs(coefficients[-1])  # Even72: real Nyquist undoubled.
    reported = np.asarray(data["torque"]["reported_raw_Nm"]["values"], float)
    assert reported.size == values.size and np.isfinite(reported).all()
    model_offset = float(np.mean(reported-values))
    return dict(data=data, values=values, electrical=electrical, mechanical=mechanical,
                coefficients=coefficients, amplitudes=amplitudes,
                summary=dict(path=str(path), report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    source_manifest_sha256=data["source_manifest_sha256"],
                    signed_DC_Nm=float(values.mean()), PP_Nm=float(np.ptp(values)),
                    reported_model_mean_Nm=float(reported.mean()),
                    reported_model_minus_Maxwell_mean_Nm=model_offset,
                    reported_minus_Maxwell_nonconstant_max_Nm=float(abs(reported-values-model_offset).max()),
                    AC_RMS_Nm=float(np.sqrt(np.mean((values-values.mean())**2))),
                    all_samples_Nm=values.tolist(), actual_mechanical_angles_deg=mechanical.tolist(),
                    all_orders=orders.tolist(), coefficient_real=coefficients.real.tolist(),
                    coefficient_imag=coefficients.imag.tolist(), all_amplitudes_Nm=amplitudes.tolist(),
                    stored_complex_validation_max_Nm=float(abs(stored_coeff-coefficients).max()),
                    cut_edges=data["projection"]["cut_edges"],
                    constraint_max=data["projection"]["constraint_max_error"],
                    convergence=data["convergence"]))


def compare(reference, actual):
    assert np.array_equal(reference["mechanical"], actual["mechanical"]), "No interpolation: actual angles must match"
    assert reference["data"]["geometry"] == actual["data"]["geometry"]
    assert reference["data"]["materials"] == actual["data"]["materials"]
    delta = actual["values"]-reference["values"]
    dc = actual["coefficients"]-reference["coefficients"]
    pp = reference["summary"]["PP_Nm"]
    rms, maximum = float(np.sqrt(np.mean(delta*delta))), float(abs(delta).max())
    return dict(actual_angles_equal=True, pointwise_RMS_Nm=rms, pointwise_max_abs_Nm=maximum,
                RMS_over_reference_PP=rms/pp if pp else None, max_over_reference_PP=maximum/pp if pp else None,
                signed_DC_delta_Nm=float(delta.mean()), PP_delta_Nm=actual["summary"]["PP_Nm"]-pp,
                PP_relative_delta=(actual["summary"]["PP_Nm"]-pp)/pp if pp else None,
                all_pointwise_deltas_Nm=delta.tolist(), all_coefficient_delta_real=dc.real.tolist(),
                all_coefficient_delta_imag=dc.imag.tolist(), all_coefficient_delta_abs=abs(dc).tolist(),
                max_complex_coefficient_delta_Nm=float(abs(dc).max()))


def main(base, output):
    output.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(output/"mpl-cache")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    records, missing = {}, []
    for mode in ("zero", "load"):
        for stage, folder, domains in (("original", "solver-ripple-symmetry", (1, 2, 4)),
                                      ("CAD-only", "solver-cad-symmetry-final", (2, 4)),
                                      ("facet-fixed", "solver-cut-facet-fix", (2, 4))):
            for ns in domains:
                name = f"{mode}/{stage}/n{ns}"
                path = base/folder/"results"/f"main-n{ns}-{mode}-s72-h1.4-g1-r144.json"
                if path.exists():
                    records[name] = read(path)
                else:
                    missing.append(name)
        folder = "solver-uniform-outer-field" if mode == "zero" else "solver-uniform-outer-load"
        name = f"{mode}/uniform-full/n1"
        path = base/folder/"results"/f"main-n1-{mode}-s72-h1.4-g1-r144.json"
        if path.exists():
            records[name] = read(path)
        else:
            missing.append(name)
    comparisons = {}
    for mode in ("zero", "load"):
        for stage, ns0, others in (("original", 1, (2, 4)), ("CAD-only", 2, (4,)), ("facet-fixed", 2, (4,))):
            key = f"{mode}/{stage}/n{ns0}"
            for ns in others:
                other = f"{mode}/{stage}/n{ns}"
                if key in records and other in records:
                    comparisons[f"{other} minus {key}"] = compare(records[key], records[other])
        key = f"{mode}/uniform-full/n1"
        for ns in (2, 4):
            other = f"{mode}/facet-fixed/n{ns}"
            if key in records and other in records:
                comparisons[f"{other} minus {key}"] = compare(records[key], records[other])
        for ns in (2, 4):
            for before, after in (("original", "CAD-only"), ("CAD-only", "facet-fixed")):
                key, other = f"{mode}/{before}/n{ns}", f"{mode}/{after}/n{ns}"
                if key in records and other in records:
                    comparisons[f"{other} minus {key}"] = compare(records[key], records[other])
    report = dict(complete=not missing, pending_reports=missing,
                  records={key:value["summary"] for key,value in records.items()}, comparisons=comparisons,
                  interpretation="RAW Maxwell, every72sample and every37complexbin retained. SignedDC separate; amplitudes are absolute, DC/Nyquist undoubled. No filtering/interpolation/renormalization. Original and CAD-only/facet stages separate. Uniformfull changes angular+radial outer grading, while full has no sector cut facets. Sector parity is not physical convergence.")
    (output/"raw-waveform-comparison.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    colors = {1:"#222222", 2:"#e17c05", 4:"#2874b2"}
    for mode in ("zero", "load"):
        figure, axes = plt.subplots(3, 2, figsize=(12, 10), constrained_layout=True)
        for row, stage in enumerate(("original", "CAD-only", "facet-fixed")):
            keys = [f"{mode}/{stage}/n{ns}" for ns in (1, 2, 4)]
            if stage == "facet-fixed":
                keys.insert(0, f"{mode}/uniform-full/n1")
            for key in keys:
                if key not in records:
                    continue
                record = records[key]
                ns = int(key.rsplit("n", 1)[1])
                label = {1:"full", 2:"half", 4:"quarter"}[ns]
                if "uniform-full" in key:
                    label += " (uniform outer)"
                axes[row, 0].plot(record["electrical"]*180/np.pi, record["values"], color=colors[ns], label=label)
                # All bins, including DC and Nyquist; no selected-order truncation.
                axes[row, 1].plot(np.arange(37), record["amplitudes"], ".-", color=colors[ns], label=label)
            axes[row, 0].set(title=f"{stage}: untouched waveform", xlabel="actual electrical angle (degrees)", ylabel="RAW Maxwell torque (Nm)")
            axes[row, 1].set(title=f"{stage}: all resolved bins", xlabel="electrical order", ylabel="amplitude magnitude (Nm), DC sign in JSON")
            for axis in axes[row]:
                axis.grid(alpha=.25)
                if axis.lines:
                    axis.legend(fontsize=8)
        figure.suptitle(f"{mode}: stages retain every sample and bin; sector parity does not establish convergence")
        figure.savefig(output/f"raw-{mode}-stages.png", dpi=170)
        figure.savefig(output/f"raw-{mode}-stages.svg")
        plt.close(figure)
    print(json.dumps({"complete":report["complete"], "pending":missing,
                      "summary":{key:{k:v for k,v in value["summary"].items() if k in ("signed_DC_Nm", "PP_Nm", "AC_RMS_Nm", "reported_model_mean_Nm", "reported_model_minus_Maxwell_mean_Nm", "reported_minus_Maxwell_nonconstant_max_Nm")} for key,value in records.items()},
                      "comparison_metrics":{key:{k:v for k,v in value.items() if not k.startswith("all_")} for key,value in comparisons.items()}}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.base.resolve(), args.output.resolve())

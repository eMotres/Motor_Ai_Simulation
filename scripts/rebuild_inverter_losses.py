"""Recompute the CONTROLLER side of stored passport drive variants with the
current inverter loss model — no FEM.

    python scripts/rebuild_inverter_losses.py [--die DIE] [--cfg CFG] [RECORD_OR_STORE ...]
                                              [--cards] [--write]

* Records: the store Configure reads (``passport_store.store_dir()``:
  ``<shared>/passports`` on the server) unless folders / files are given;
  ``--die`` / ``--cfg`` narrow it (exact names, e.g. ``--die "CIANO14 40 new"
  --cfg L12``).
* ``--cards`` first prints, for every device the selected variants use (the
  card as RESOLVED on this machine — shared/private overlays included), the
  SPICE static table's R = V_DS/I against the card's datasheet R_DS(on)(T_j)
  at 25 / 75 / 110 / 150 degC.
* Per drive variant the board model is rebuilt exactly as the passport build
  does it (:class:`motor_ai_sim.passport_v1.stage3.Board`: the record's 6S/12S
  rating, the Si calibration device and gate resistor read back from the
  record's own Si 48 kHz variant, the variant's devices per switch and board
  scale), then every computed point gets new ``inverter_loss_W
  {cond, sw, dead}``, ``tj_C``, ``t_heatsink_C``, ``board_ok`` / ``tj_ok`` /
  ``continuous_ok`` / ``tj_basis``, ``P_battery_W`` and ``eta_drive_pct``.  The
  motor side (shaft power, sine and PWM motor losses, mechanical loss) is the
  stored value and is not touched.  The variant's ``i_board_limit_A`` is
  recomputed; ``p_cont_max_W`` needs the machine's loss model (the full card
  build) — when the board limit moves by more than 0.1 A the variant says so
  in ``p_cont_max_note`` and the old power stays (re-run the full card).
* Without ``--write`` it is a dry run that prints old -> new per point.  With
  it, each changed record is written in place after a copy
  ``<config>.json.pre-inverter.bak`` (kept if it already exists).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.passport_v1 import stage3 as S3  # noqa: E402

LIMIT_MOVE_A = 0.1
POINT_KEYS = ("inverter_loss_W", "tj_C", "t_heatsink_C", "P_battery_W", "eta_drive_pct",
              "board_ok", "tj_ok", "continuous_ok", "tj_basis")


def _load(p: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def records(paths: List[str], die: Optional[str], cfg: Optional[str]) -> List[Path]:
    if not paths:
        from motor_ai_sim.passport_store import store_dir
        paths = [str(store_dir())]
    out: List[Path] = []
    for a in paths:
        p = Path(a)
        if p.is_dir():
            out += sorted(p.glob("*/*.json"))
        elif p.is_file():
            out.append(p)
        else:
            print(f"skip: {a} does not exist")
    sel = []
    for f in out:
        rec = _load(f)
        m = (rec or {}).get("machine") or {}
        d_ = str(m.get("die") or f.parent.name)
        c_ = str(m.get("configuration") or f.stem)
        if die and d_ != die:
            continue
        if cfg and c_ != cfg:
            continue
        sel.append(f)
    return sel


def card_table(part: str) -> List[str]:
    """The R table of one card: SPICE static V/I vs datasheet R_DS(on)(T_j)."""
    from motor_ai_sim.inverter.devices import get_device, resolve_card
    c = get_device(part)
    src, path, _ = resolve_card(part)
    lines = [f"{part}  (card: {src} {path})"]
    st = c.static_table()
    if st is None:
        return lines + ["  no SPICE static table — conduction on the datasheet curve"]
    lines.append(f"  static table basis: {st.get('basis')}  generated {st.get('generated')}")
    gs_tab = sorted({float(r[0]) for r in st.get("forward") or []})
    for vgs in gs_tab:
        lines.append(f"  V_GS {vgs:g} V   ({c.r_ds_on_note(vgs)})")
        lines.append("    T_j   R_datasheet   SPICE R=V/I at 10 / 30 / 47 / 70 A [mOhm]   dev@47A")
        for t in (25.0, 75.0, 110.0, 150.0):
            r_ds = c.r_ds_on_ohm(t, vgs) * 1e3
            rs = [c.v_ds_on_spice_V(i, t, vgs) / i * 1e3 for i in (10.0, 30.0, 47.0, 70.0)]
            lines.append("    %4.0f  %8.3f      %s      %+5.1f %%"
                         % (t, r_ds, " / ".join("%.3f" % x for x in rs),
                            (rs[2] / r_ds - 1.0) * 100.0))
    for t in (25.0, 110.0):
        chk = c.static_conduction_check(t, gs_tab[-1])
        lines.append(f"  check @ {t:g} degC: {'OK' if chk['ok'] else 'FAIL'} — {chk['line']}")
    q = c.static_third_quadrant_check(0.0)
    if q:
        lines.append(f"  third quadrant: {'OK' if q['ok'] else 'FAIL'} — {q['line']}")
    return lines


def _calibration(rec: Dict[str, Any]) -> Tuple[str, str, float]:
    """(build, Si calibration part, Si gate resistor) from the record itself."""
    build = str(((rec.get("machine") or {}).get("version")) or "")
    if build not in S3.RATINGS:
        raise ValueError(f"machine.version {build!r} is not a board rating ({list(S3.RATINGS)})")
    si = [v for v in rec.get("pwm_variants") or []
          if isinstance(v, dict) and v.get("technology") == "Si" and v.get("drive")
          and abs(float(v.get("carrier_hz") or 0) - 48e3) < 1]
    if not si:
        raise ValueError("no Si 48 kHz variant to read the board calibration from")
    r_g = float(si[0]["drive"]["r_g"]) - float(S3.SI_DRIVE["pull_up_ohm"])
    return build, str(si[0]["device"]), r_g


def rebuild_variant(v: Dict[str, Any], *, build: str, si_part: str, r_g_si: float,
                    board_scale: Optional[float] = None) -> Tuple[Dict[str, Any], List[str]]:
    """A copy of one variant with its controller side recomputed + report lines."""
    from motor_ai_sim.inverter.devices import get_device
    out = json.loads(json.dumps(v))
    rep: List[str] = []
    card = get_device(str(v["device"]))
    drv = S3.drive_for(card, r_g_si)
    stored = v.get("drive") or {}
    for k in ("v_gs_on", "v_gs_off", "r_g", "r_g_off", "l_sigma"):
        a, b = stored.get(k), drv.get(k)
        if (a is None) != (b is None) or (a is not None and abs(float(a) - float(b)) > 1e-9):
            rep.append(f"    drive {k}: stored {a} vs rebuilt {b} — rebuilt value used")
    n_par = int(v.get("n_parallel") or 1)
    if v.get("board_scale") is not None:
        scale = float(v["board_scale"])
    elif board_scale is not None:
        scale = float(board_scale)
    elif n_par == 1:
        scale = 1.0
    else:
        # the passport build scales the board with the devices (the 85 mm
        # build: x2) and the store record does not carry the factor — never guess
        raise ValueError(f"variant {v.get('id')}: {n_par} devices per switch and no stored "
                         "board_scale — give --board-scale (the full card's "
                         "controller.board_scale, e.g. 2 for the 85 mm build)")
    out["board_scale"] = scale
    si = get_device(si_part)
    board = S3.Board(si, S3.drive_for(si, r_g_si), build=build,
                     v_dc=float(v["bus_v"]["nom"]), f_sw=48e3, dead_s=100e-9,
                     n_par=n_par, scale=scale)
    f_sw, dead, v_dc = float(v["carrier_hz"]), float(v["dead_time_s"]), float(v["bus_v"]["nom"])
    rep.append("    conduction: " + S3.conduction_provenance(card, drv))
    for key, p in (out.get("points") or {}).items():
        if not isinstance(p, dict) or p.get("inverter_loss_W") is None:
            continue
        I = float(p["I_A"])
        st = board.state(card, drv, I_rms=I, f_sw=f_sw, dead_s=dead, v_dc=v_dc)
        inv = st["inv"]
        old = dict(p["inverter_loss_W"])
        P_board = S3.K_BOARD_CU * I ** 2
        P_sh = float(p.get("P_shaft_W") or 0.0)
        P_motor = (float(p.get("P_motor_sine_W") or 0.0) + float(p.get("P_mech_W") or 0.0)
                   + float(p.get("motor_pwm_loss_W") or 0.0))
        P_bat = P_sh + P_motor + inv["total"] + P_board
        new = {"inverter_loss_W": {"cond": inv["cond"], "sw": inv["sw"], "dead": inv["dead"]},
               "board_copper_W": P_board,
               "tj_C": st["t_j_C"], "t_heatsink_C": st["t_hs_C"],
               "P_battery_W": P_bat,
               "eta_drive_pct": 100.0 * P_sh / P_bat if P_bat > 0 and P_sh > 0 else None,
               "board_ok": st["board_ok"], "tj_ok": st["tj_ok"],
               "continuous_ok": st["continuous_ok"], "tj_basis": st["tj_basis"]}
        rep.append("    %-18s I %6.2f A: cond %7.3f -> %7.3f W  sw %6.3f -> %6.3f  dead %6.3f -> %6.3f"
                   "  Tj %s -> %.1f  eta_drive %s -> %s"
                   % (key, I, old.get("cond", float("nan")), inv["cond"],
                      old.get("sw", float("nan")), inv["sw"], old.get("dead", float("nan")),
                      inv["dead"], _f(p.get("tj_C")), st["t_j_C"], _f(p.get("eta_drive_pct")),
                      _f(new["eta_drive_pct"])))
        p.update(new)
    lim_old = dict(v.get("i_board_limit_A") or {})
    lim_new = {cls: board.i_limit(card, drv, f_sw=f_sw, dead_s=dead, v_dc=v_dc, cls=cls)
               for cls in board.classes}
    moved = {c: (lim_old.get(c), lim_new[c]) for c in lim_new
             if lim_old.get(c) is None or abs(float(lim_old[c]) - lim_new[c]) > LIMIT_MOVE_A}
    rep.append("    board limit [A]: " + ", ".join(
        "%s %s -> %.2f" % (c, _f(lim_old.get(c)), lim_new[c]) for c in lim_new))
    out["i_board_limit_A"] = lim_new
    if moved:
        out["p_cont_max_note"] = (
            "board-limit current moved by the inverter-loss rebuild of %s (%s); p_cont_max_W "
            "is still on the old limit — re-run the full card to refresh it"
            % (_dt.date.today().isoformat(),
               ", ".join("%s %s -> %.2f A" % (c, _f(a), b) for c, (a, b) in moved.items())))
        rep.append("    !! " + out["p_cont_max_note"])
    prov = dict(out.get("provenance") or {})
    prov["inverter_conduction"] = S3.conduction_provenance(card, drv)
    prov["inverter_rebuild"] = ("scripts/rebuild_inverter_losses.py %s: controller side "
                                "recomputed with the current inverter loss model"
                                % _dt.date.today().isoformat())
    out["provenance"] = prov
    return out, rep


def _f(x: Any) -> str:
    return "—" if x is None else "%.1f" % float(x)


def _same(a: Any, b: Any, tol: float = 1e-6) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k], tol) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)))
    return a == b


def rebuild_record(rec: Dict[str, Any], board_scale: Optional[float] = None
                   ) -> Tuple[Dict[str, Any], List[str]]:
    build, si_part, r_g = _calibration(rec)
    rep = [f"  board calibration: {build}, Si {si_part}, R_G {r_g:g} ohm"]
    new = json.loads(json.dumps(rec))
    vs = []
    for v in new.get("pwm_variants") or []:
        if not (isinstance(v, dict) and isinstance(v.get("points"), dict) and v["points"]):
            vs.append(v)
            continue
        rep.append(f"  variant {v.get('id')} ({v.get('device')}, {float(v['carrier_hz']) / 1e3:g} kHz, "
                   f"{int(v.get('n_parallel') or 1)} per switch):")
        nv, r = rebuild_variant(v, build=build, si_part=si_part, r_g_si=r_g,
                                board_scale=board_scale)
        vs.append(nv)
        rep += r
    new["pwm_variants"] = vs
    return new, rep


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("records", nargs="*")
    ap.add_argument("--die")
    ap.add_argument("--cfg")
    ap.add_argument("--cards", action="store_true", help="print the devices' R tables first")
    ap.add_argument("--board-scale", type=float, default=None,
                    help="board scale for variants with >1 device per switch that do not store it")
    ap.add_argument("--write", action="store_true", help="write the records (default: dry run)")
    a = ap.parse_args(argv)
    files = records(a.records, a.die, a.cfg)
    if not files:
        print("no record selected")
        return 1
    if a.cards:
        parts = sorted({str(v.get("device")) for f in files
                        for v in (_load(f) or {}).get("pwm_variants") or []
                        if isinstance(v, dict) and v.get("device")})
        for part in parts:
            print("\n".join(card_table(part)))
    n_changed = 0
    for f in files:
        rec = _load(f)
        if not isinstance(rec, dict) or not rec.get("pwm_variants"):
            continue
        print(f"{f}:")
        try:
            new, rep = rebuild_record(rec, a.board_scale)
        except Exception as exc:                                   # noqa: BLE001
            print(f"  LEFT AS IS — {exc}")
            continue
        print("\n".join(rep))
        def _bare(r: Dict[str, Any]) -> List[Any]:
            return [{k: x for k, x in v.items() if k != "provenance"} if isinstance(v, dict) else v
                    for v in r.get("pwm_variants") or []]

        if _same(_bare(new), _bare(rec)):
            print("  unchanged")
            continue
        n_changed += 1
        if a.write:
            bak = f.with_name(f.name + ".pre-inverter.bak")
            if not bak.exists():
                shutil.copy2(f, bak)
            f.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"  written (backup {bak.name})")
    print(f"{n_changed} record(s) {'written' if a.write else 'would change (dry run; --write to apply)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

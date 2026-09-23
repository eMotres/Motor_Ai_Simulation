"""Where a part's vendor SPICE model lives, and whether ngspice can run it.

One folder per part, ``config/devices/spice/<PART>/manifest.yaml``, written by
hand from what was actually downloaded (URL, date, sha256, licence summary).
The vendor files themselves sit in ``config/devices/spice/_vendor/`` and are
kept OUT of git: Infineon's Model Terms of Use (§3) make the model content
confidential, so only the manifest — which says where to fetch the exact same
file and how to check it — is committed.

A manifest names the library relative to its own folder, the subcircuit per
model level and the pin order.  :func:`model_for` checks the library's sha256
against the manifest on every call: a table built from one file must never
silently be re-used with another.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

__all__ = ["SpiceModel", "SpiceModelError", "model_for", "spice_dir",
           "read_manifest", "list_manifests"]


class SpiceModelError(RuntimeError):
    """The part has no usable SPICE model (missing, encrypted, mismatched)."""


_REPO = Path(__file__).resolve().parents[4]
_DIR = _REPO / "config" / "devices" / "spice"

_SHA_CACHE: Dict[Tuple[str, float], str] = {}


def spice_dir() -> Path:
    return _DIR


def _sha256(path: Path) -> str:
    key = (str(path), path.stat().st_mtime)
    hit = _SHA_CACHE.get(key)
    if hit:
        return hit
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    _SHA_CACHE[key] = h.hexdigest()
    return _SHA_CACHE[key]


def read_manifest(part: str) -> Dict[str, Any]:
    p = _DIR / part / "manifest.yaml"
    if not p.is_file():
        raise SpiceModelError(f"{part}: no SPICE manifest at {p}")
    with p.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if not isinstance(doc, dict):
        raise SpiceModelError(f"{part}: manifest is not a mapping")
    return doc


def list_manifests() -> List[Dict[str, Any]]:
    out = []
    if not _DIR.is_dir():
        return out
    for p in sorted(_DIR.glob("*/manifest.yaml")):
        try:
            out.append(read_manifest(p.parent.name))
        except SpiceModelError:
            continue
    return out


@dataclass
class SpiceModel:
    """One vendor subcircuit, ready to be instanced in a netlist."""
    part: str
    level: str
    lib_path: Path
    lib_sha256: str
    lib_name: str
    subckt: str
    pins: List[str]
    compat: str = "psa"
    #: True when the subcircuit has a separate Kelvin (source-sense) pin.
    kelvin: bool = False
    #: Thermal pins (L3) — the names, in pin order, after the electrical ones.
    thermal_pins: List[str] = field(default_factory=list)
    manifest: Dict[str, Any] = field(default_factory=dict)

    @property
    def include_path(self) -> Path:
        """What a netlist ``.include``s for ngspice (see
        :func:`ngspice_lib_for`)."""
        return ngspice_lib_for(self)

    @property
    def basis(self) -> str:
        """``spice:<lib file>@<sha256[:12]>:<subckt>`` — the provenance tag."""
        return f"spice:{self.lib_name}@{self.lib_sha256[:12]}:{self.subckt}"

    def instance(self, name: str, drain: str, gate: str, source: str,
                 kelvin: Optional[str] = None,
                 thermal: Optional[List[str]] = None) -> str:
        """``X<name> <nodes> <subckt>`` in the subcircuit's own pin order."""
        nodes: List[str] = []
        th = list(thermal or [])
        for p in self.pins:
            k = p.lower()
            if k == "drain":
                nodes.append(drain)
            elif k == "gate":
                nodes.append(gate)
            elif k == "source":
                nodes.append(source)
            elif k in ("sourcesense", "source_k", "kelvin"):
                nodes.append(kelvin or source)
            elif k in ("tj", "tcase", "ttop", "tbottom"):
                if not th:
                    raise SpiceModelError(
                        f"{self.subckt}: thermal pin {p} needs a node")
                nodes.append(th.pop(0))
            else:
                raise SpiceModelError(f"{self.subckt}: unknown pin {p!r}")
        return f"X{name} {' '.join(nodes)} {self.subckt}"


# ---------------------------------------------------------------------------
# ngspice and DDT()
# ---------------------------------------------------------------------------
# The CoolSiC 1200 V G2 library writes its nonlinear capacitances as current
# sources ``G a b VALUE = { C(...) * DDT(V(a,b)) }`` — SIMetrix's idiom.
# ngspice evaluates ``ddt()`` in a B-source EXPLICITLY (its derivative is not
# stamped into the Jacobian), so the device's own capacitances become an
# explicit feedback loop and the transient dies within the first 100 ps with
# "timestep too small" (re-checked 2026-09-23 on the datasheet double pulse
# of IMCQ120R004M2H: aborted at t = 77 ps, node xh.neoidx; the first pass
# also tried gear/trap, maxord, reltol, uic and the compatibility modes).
# KiCad's simulator IS ngspice, so the library as shipped cannot run a
# switching transient in KiCad either.
#
# The translation below is EXACT algebra, not a model change: every
# ``DDT(V(x,y))`` becomes the current of a 1 pF capacitor driven by a unit
# VCVS copy of ``V(x,y)``, scaled back by 1e12 —
#
#     EDDT_n  NDDT_n_A 0 x y 1          (a copy of V(x,y), no load on x/y)
#     CDDT_n  NDDT_n_A NDDT_n_B 1e-12
#     VDDT_n  NDDT_n_B 0 0              (I(VDDT_n) = 1e-12 * dV(x,y)/dt)
#     ... DDT(V(x,y))  ->  (I(VDDT_n)*1e12)
#
# — so the derivative is carried by a real capacitor the integrator treats
# implicitly.  No parameter, no expression other than the DDT() calls is
# touched; DC results are bit-identical (DDT = 0 either way).  The derived
# file sits next to the vendor file (``_vendor/_ngspice/``, not in git) and
# its header names the source file and both sha256s.

def _logical_lines(text: str) -> List[str]:
    out: List[str] = []
    for raw in text.splitlines():
        if raw.startswith("+") and out:
            out[-1] = out[-1] + " " + raw[1:]
        else:
            out.append(raw)
    return out


def ngspice_translate_ddt(text: str) -> Tuple[str, int]:
    """Return ``(translated_text, n_replacements)`` — see the block comment."""
    import re
    rx = re.compile(r"DDT\(\s*V\(\s*([^,()\s]+)\s*(?:,\s*([^,()\s]+)\s*)?\)\s*\)",
                    re.IGNORECASE)
    n = 0
    out: List[str] = []
    for ln in _logical_lines(text):
        s = ln.lstrip()
        if not s or s[0] in "*;" or "ddt(" not in s.lower():
            out.append(ln)
            continue
        extra: List[str] = []

        def _sub(m: "re.Match[str]") -> str:
            nonlocal n
            n += 1
            a, b = m.group(1), m.group(2) or "0"
            extra.extend([
                f"EDDT_{n} NDDT_{n}_A 0 {a} {b} 1",
                f"CDDT_{n} NDDT_{n}_A NDDT_{n}_B 1e-12",
                f"VDDT_{n} NDDT_{n}_B 0 0",
            ])
            return f"(I(VDDT_{n})*1e12)"

        out.append(rx.sub(_sub, ln))
        out.extend(extra)
    return "\n".join(out) + "\n", n


def ngspice_lib_for(model: "SpiceModel") -> Path:
    """The library path ngspice should ``.include`` for ``model`` — the vendor
    file itself when it has no ``DDT()``, else its translated copy (created on
    first use, re-created when the vendor file's sha256 changes)."""
    src = model.lib_path
    text = src.read_text(encoding="utf-8", errors="replace")
    if "ddt(" not in text.lower():
        return src
    dst_dir = src.parents[0]
    # keep it inside _vendor (git-ignored), in a sibling folder
    for p in src.parents:
        if p.name == "_vendor":
            dst_dir = p / "_ngspice"
            break
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / (src.stem + f".{model.lib_sha256[:12]}.ngspice.lib")
    if dst.is_file():
        return dst
    body, n = ngspice_translate_ddt(text)
    hdr = (f"* ngspice translation of {src.name} (sha256 {model.lib_sha256})\n"
           f"* {n} DDT(V(..)) calls replaced by an implicit 1 pF capacitor sense;\n"
           "* no parameter or other expression changed.  Generated by\n"
           "* motor_ai_sim.inverter.spice.models.ngspice_translate_ddt.\n"
           "* Vendor Terms of Use apply unchanged; NOT for redistribution.\n")
    dst.write_text(hdr + body, encoding="utf-8")
    return dst


def model_for(part: str, level: Optional[str] = None, *,
              check_sha: bool = True) -> SpiceModel:
    """The usable model of ``part`` at ``level`` (``L1`` isothermal, the
    junction at ``.temp``; ``L3`` with Tj/Tcase thermal nodes, which the
    netlists hold at T_j).  ``None`` = the manifest's ``ngspice.level``
    (the level that runs in ngspice), else ``L1``.

    Refuses — by name and reason — a part whose manifest says the library is
    encrypted for another simulator, a library that is not on disk, and a
    library whose sha256 no longer matches the manifest.
    """
    doc = read_manifest(part)
    status = str(doc.get("status") or "").strip()
    if status != "usable_ngspice":
        why = doc.get("status_reason") or status or "no status"
        raise SpiceModelError(f"{part}: SPICE model not runnable in ngspice — {why}")
    lib = doc.get("library") or {}
    rel = lib.get("file")
    if not rel:
        raise SpiceModelError(f"{part}: manifest names no library file")
    path = (_DIR / part / rel).resolve()
    if not path.is_file():
        raise SpiceModelError(
            f"{part}: vendor library {path} is not on disk — it is kept out of "
            f"git; fetch it from {((doc.get('source') or {}).get('url'))} and "
            "unpack it under config/devices/spice/_vendor/")
    want = str(lib.get("sha256") or "").lower()
    got = _sha256(path)
    if check_sha and want and got != want:
        raise SpiceModelError(
            f"{part}: {path.name} sha256 {got[:12]}… does not match the "
            f"manifest's {want[:12]}… — a different model revision")
    mods = doc.get("models") or {}
    if level is None:
        level = str((doc.get("ngspice") or {}).get("level") or "L1")
    m = mods.get(level)
    if not isinstance(m, dict) or not m.get("subckt"):
        raise SpiceModelError(f"{part}: manifest has no {level} model")
    pins = [str(p) for p in (m.get("pins") or [])]
    kel = any(p.lower() in ("sourcesense", "source_k", "kelvin") for p in pins)
    th = [p for p in pins if p.lower() in ("tj", "tcase", "ttop", "tbottom")]
    # the subcircuit NAME in the basis tag carries the level, so a table
    # built on L3 can never be mistaken for one built on L1
    return SpiceModel(
        part=part, level=level, lib_path=path, lib_sha256=got,
        lib_name=path.name, subckt=str(m["subckt"]), pins=pins,
        compat=str((doc.get("ngspice") or {}).get("compat") or "psa"),
        kelvin=kel, thermal_pins=th, manifest=doc)

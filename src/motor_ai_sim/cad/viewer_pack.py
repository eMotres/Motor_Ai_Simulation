"""Viewer copies of the 3-D models: the GLB the browser loads, made small (owner 2026-10-20: the models took 8-16 s to load).

The canonical GLBs (``<motor_id>_full.glb``, ``<motor_id>_simplified.glb``) stay exactly as the exporters wrote them: the build checks,
the geometry hashes and the stamped, logged simplified download all read them.  Next to each the build writes a VIEWER copy, packed by
gltfpack (meshoptimizer, MIT):

    <motor_id>_full.meshopt.glb          what /api/model3d/<die>/<config>/model.glb serves (the NDA view)
    <motor_id>_simplified.meshopt.glb    what /api/model3d/<die>/<config>/simplified_view.glb serves

``EXT_meshopt_compression`` + ``KHR_mesh_quantization``.  Positions stay FLOAT (``-vpf``: the exponential filter, ~0.002-0.006 mm on the
public motors) so no dequantisation transform is added to any node: the node tree, the names, the extras and the coordinates the
viewer reads directly (thread axes, section, measure) are what the canonical file has.  Normals are 10 bit (~0.2 degrees).  No
simplification: the tessellation is the exporter's.  The viewer decodes with three.js' MeshoptDecoder (bundled, no CDN).

A missing tool or a failed check never refuses a build and never loses a model: the copy is simply not written, the manifest says
why, and the routes serve the canonical GLB (``model3d.view_row``).

  python cad/run_build.py pack --out-root /out                     re-pack every built model in place (manifest updated)
  python cad/run_build.py pack --out-root /out --die D --config C

The tool: ``$MOTRES_GLTFPACK`` (a command line, e.g. ``node C:/tools/node_modules/gltfpack/cli.js``) or ``gltfpack`` on PATH (the CAD
image builds it from the pinned meshoptimizer source, deploy/Dockerfile.cad).  Stdlib only, like the rest of this package.
"""
import collections
import json
import os
import shlex
import shutil
import struct
import subprocess
import time

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
FLOAT = 5126

#: gltfpack options: highest meshopt ratio, float positions (no node transforms), 10-bit normals, keep every named node / material / extras
TOOL_ARGS = ("-cc", "-vpf", "-vn", "10", "-kn", "-km", "-ke")
#: the only extensions a packed file may REQUIRE: three.js' GLTFLoader handles both (meshopt with the decoder the viewer sets)
ALLOWED_REQUIRED = frozenset({"EXT_meshopt_compression", "KHR_mesh_quantization"})
#: (canonical suffix, viewer copy suffix)
PAIRS = (("_full.glb", "_full.meshopt.glb"), ("_simplified.glb", "_simplified.meshopt.glb"))
#: bounding boxes of a mesh may move by the position quantisation only
BBOX_TOL_MM = 0.05


def find_tool():
    """The gltfpack command as an argv list, or None: ``$MOTRES_GLTFPACK`` (a command line), else ``gltfpack`` on PATH."""
    env = (os.environ.get("MOTRES_GLTFPACK") or "").strip()
    if env:
        parts = shlex.split(env, posix=(os.name != "nt"))
        return [t[1:-1] if len(t) > 1 and t[0] == t[-1] == '"' else t for t in parts]      # Windows: "C:/a b/node.exe" cli.js
    p = shutil.which("gltfpack")
    return [p] if p else None


def _low_priority():
    if os.name == "nt":
        return dict(creationflags=0x00004000)                      # BELOW_NORMAL_PRIORITY_CLASS
    return dict(preexec_fn=lambda: os.nice(10))


def tool_version(cmd):
    """``gltfpack 1.3`` (first line of ``-v``), or '' when the tool does not answer."""
    try:
        p = subprocess.run(list(cmd) + ["-v"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return ((p.stdout or p.stderr or "").strip().splitlines() or [""])[0].strip()


def json_chunk(data):
    """The JSON document of a GLB (the BIN chunk is not needed: a packed one cannot be read without the meshopt decoder)."""
    if len(data) < 20:
        raise ValueError("not a GLB 2.0 file")
    magic, ver, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or ver != 2 or length != len(data):
        raise ValueError("not a GLB 2.0 file")
    jl, jt = struct.unpack_from("<II", data, 12)
    if jt != CHUNK_JSON:
        raise ValueError("first chunk is not JSON")
    return json.loads(data[20:20 + jl].decode("utf-8"))


def _tree(doc):
    """The facts the viewer relies on, independent of node / mesh / accessor indices:
    nodes = Counter of (name, extras, parent name, ordered child names); meshes = Counter of (node name, triangles);
    boxes = {node name: [(min, max), ...]}; transforms = number of nodes with a TRS / matrix; float = every POSITION is FLOAT."""
    nodes = doc.get("nodes") or []
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children") or []:
            parent[c] = n.get("name")
    node_rows, mesh_rows, boxes = collections.Counter(), collections.Counter(), collections.defaultdict(list)
    transforms, all_float = 0, True
    acc = doc.get("accessors") or []
    for i, n in enumerate(nodes):
        kids = tuple(nodes[c].get("name") for c in n.get("children") or [])
        node_rows[(n.get("name"), json.dumps(n.get("extras"), sort_keys=True), parent.get(i), kids)] += 1
        if any(k in n for k in ("matrix", "translation", "rotation", "scale")):
            transforms += 1
        if "mesh" in n:
            tris = 0
            for p in doc["meshes"][n["mesh"]].get("primitives") or []:
                if "indices" in p:
                    tris += acc[p["indices"]]["count"] // 3
                a = acc[p["attributes"]["POSITION"]]
                all_float = all_float and a.get("componentType") == FLOAT and not a.get("normalized")
                if "min" in a and "max" in a:
                    boxes[n.get("name")].append((tuple(a["min"]), tuple(a["max"])))
            mesh_rows[(n.get("name"), tris)] += 1
    roots = [nodes[i].get("name") for s in doc.get("scenes") or [] for i in s.get("nodes") or []]
    mats = [(m.get("name"), tuple(round(x, 4) for x in (m.get("pbrMetallicRoughness") or {}).get("baseColorFactor") or ()),
             m.get("alphaMode"), bool(m.get("doubleSided"))) for m in doc.get("materials") or []]
    return dict(nodes=node_rows, meshes=mesh_rows, boxes=boxes, transforms=transforms, float=all_float, roots=roots,
                materials=collections.Counter(mats), asset=(doc.get("asset") or {}).get("extras"))


def check(src, dst):
    """Problems of a packed GLB against its canonical GLB (bytes, bytes); [] = the viewer sees the same model."""
    try:
        a, b = json_chunk(src), json_chunk(dst)
    except (ValueError, UnicodeDecodeError) as e:
        return ["unreadable: %s" % e]
    out = []
    req = set(b.get("extensionsRequired") or [])
    if req - ALLOWED_REQUIRED:
        out.append("requires extensions the viewer does not load: %s" % sorted(req - ALLOWED_REQUIRED))
    ta, tb = _tree(a), _tree(b)
    if ta["nodes"] != tb["nodes"]:
        diff = list((ta["nodes"] - tb["nodes"]).elements())[:3] + list((tb["nodes"] - ta["nodes"]).elements())[:3]
        out.append("node tree differs (names / extras / parents / child order): %s" % [d[0] for d in diff])
    if ta["meshes"] != tb["meshes"]:
        out.append("triangle counts differ: %s" % list((ta["meshes"] - tb["meshes"]).elements())[:5])
    if tb["transforms"] != ta["transforms"]:
        out.append("node transforms added (%d -> %d): the viewer reads positions in the CAD frame" % (ta["transforms"], tb["transforms"]))
    if not tb["float"]:
        out.append("positions are not FLOAT")
    if ta["roots"] != tb["roots"]:
        out.append("scene roots differ")
    if ta["materials"] != tb["materials"]:
        out.append("materials differ")
    if ta["asset"] != tb["asset"]:
        out.append("asset extras differ")
    moved = []
    for name, bxs in ta["boxes"].items():
        got = sorted(tb["boxes"].get(name) or [])
        if len(got) != len(bxs) or any(max(abs(x - y) for x, y in zip(lo + hi, lo2 + hi2)) > BBOX_TOL_MM
                                       for (lo, hi), (lo2, hi2) in zip(sorted(bxs), got)):
            moved.append(name)
    if moved:
        out.append("bounding box missing or moved by more than %.2f mm: %d meshes (%s)" % (BBOX_TOL_MM, len(moved), ", ".join(map(str, moved[:3]))))
    if len(dst) >= len(src):
        out.append("not smaller (%d >= %d bytes)" % (len(dst), len(src)))
    return out


def pack(src_path, dst_path, cmd=None, timeout=600):
    """Pack one GLB.  -> ``{ok, message, bytes_in, bytes_out, ratio, seconds}``; on any failure no ``dst_path`` is left behind."""
    cmd = list(cmd or find_tool() or [])
    src = open(src_path, "rb").read()
    res = dict(ok=False, message="", bytes_in=len(src), bytes_out=None, ratio=None, seconds=None)
    if not cmd:
        res["message"] = "gltfpack not found (set $MOTRES_GLTFPACK or put gltfpack on PATH)"
        return res
    tmp = dst_path + ".tmp-%d.glb" % os.getpid()
    t0 = time.time()
    try:
        for _attempt in range(2):                                  # one retry: node's libuv can abort at exit on Windows (dev runs)
            if os.path.exists(tmp):
                os.remove(tmp)
            p = subprocess.run(cmd + ["-i", src_path, "-o", tmp] + list(TOOL_ARGS), capture_output=True, text=True, timeout=timeout,
                               **_low_priority())
            if p.returncode == 0 and os.path.isfile(tmp):
                break
        res["seconds"] = round(time.time() - t0, 2)
        if p.returncode != 0 or not os.path.isfile(tmp):
            res["message"] = "gltfpack exit %s: %s" % (p.returncode, (p.stderr or p.stdout or "").strip()[-400:])
            return res
        dst = open(tmp, "rb").read()
        problems = check(src, dst)
        if problems:
            res["message"] = "; ".join(problems)[:600]
            return res
        os.replace(tmp, dst_path)
        res.update(ok=True, bytes_out=len(dst), ratio=round(len(src) / max(1, len(dst)), 2))
        return res
    except (OSError, subprocess.SubprocessError) as e:
        res["message"] = "%s: %s" % (type(e).__name__, e)
        return res
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def pack_dir(out_dir, motor_id, cmd=None):
    """Viewer copies of every canonical GLB present in ``out_dir``.  -> the manifest block ``{ok, tool, args, files: {name: result}}``
    (``files`` holds only the copies that were written; ``skipped`` the ones that were not, with the reason)."""
    cmd = list(cmd or find_tool() or [])
    info = dict(ok=False, tool=tool_version(cmd) if cmd else "", args=" ".join(TOOL_ARGS), files={}, skipped={})
    for src_sfx, dst_sfx in PAIRS:
        src, dst = os.path.join(out_dir, motor_id + src_sfx), os.path.join(out_dir, motor_id + dst_sfx)
        if not os.path.isfile(src):
            if os.path.exists(dst):                                # a stale copy of a model that is no longer there
                os.remove(dst)
            continue
        r = pack(src, dst, cmd)
        (info["files"] if r["ok"] else info["skipped"])[motor_id + dst_sfx] = r
        if not r["ok"] and os.path.exists(dst):                    # never serve a copy of an older canonical file
            os.remove(dst)
    info["ok"] = bool(info["files"]) and not info["skipped"]
    if not cmd:
        info["message"] = "gltfpack not found: the viewer gets the canonical GLBs"
    return info


def produced(info):
    """File names a ``pack_dir`` block wrote (for the manifest's file rows)."""
    return sorted((info or {}).get("files") or {})


def repack(out_dir, cmd=None):
    """Re-pack one built output directory in place and update its manifest (files rows + ``viewer_pack``).  -> the block."""
    from . import manifest as mf
    mpath = os.path.join(out_dir, "manifest.json")
    with open(mpath, encoding="utf-8") as fh:
        m = json.load(fh)
    mid = m["motor_id"]
    info = pack_dir(out_dir, mid, cmd)
    files = {k: v for k, v in (m.get("files") or {}).items() if not k.endswith(".meshopt.glb")}
    files.update(mf.file_rows(out_dir, produced(info)))
    m["files"] = files
    m["viewer_pack"] = info
    tmp = mpath + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1, ensure_ascii=False, default=str)
    os.replace(tmp, mpath)
    return info


def repack_root(root, die=None, config=None, cmd=None):
    """``repack`` every built model under ``root`` (or the one die/config).  -> [(die, config, block)]."""
    out = []
    for d in sorted(os.listdir(root)):
        dd = os.path.join(root, d)
        if d.startswith(".") or ".building-" in d or ".old-" in d or not os.path.isdir(dd) or (die and d != die):
            continue
        for c in sorted(os.listdir(dd)):
            cd = os.path.join(dd, c)
            if c.startswith(".") or ".building-" in c or ".old-" in c or (config and c != config):
                continue
            if os.path.isfile(os.path.join(cd, "manifest.json")):
                out.append((d, c, repack(cd, cmd)))
    return out


def selftest(cmd=None):
    """Pack a synthetic model (a tessellated tube, two parts, node extras) with the installed tool.  -> (ok, message).
    Run at image build time (deploy/Dockerfile.cad: ``run_build.py pack-selftest``) so a broken gltfpack fails the image, not a rebuild."""
    import math
    import sys
    import tempfile
    from . import store
    sys.path.insert(0, os.path.join(store.CAD_DIR, "fc_scripts"))
    import glb_writer as G

    def tube(r0, r1, h, n=256):
        v = [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), z) for r in (r0, r1) for z in (-h, h) for k in range(n)]
        t = []
        for k in range(n):
            q = lambda ring, j: ring * n + j % n                                    # noqa: E731  ring 0..3 = (r0,-h) (r0,h) (r1,-h) (r1,h)
            for a, b, c, d in ((0, 1, k, k + 1), (2, 3, k, k + 1), (0, 2, k, k + 1), (1, 3, k, k + 1)):
                t += [(q(a, c), q(b, d), q(a, d)), (q(a, c), q(b, c), q(b, d))]
        return G.Mesh(v, t)

    core = G.Group("Core", (0.6, 0.62, 0.65), extras={"family": "Core", "explode": 0.0})
    core.add("Core", [tube(10.0, 20.0, 5.0)])
    coils = G.Group("Coils", (0.8, 0.47, 0.22), extras={"family": "Coils", "explode": -0.35})
    coils.add("Coil_01", [tube(12.0, 14.0, 6.0)])
    coils.add("Coil_02", [tube(15.0, 17.0, 6.0)], extras={"thread": {"pitch_mm": 0.5}})
    data = G.build_glb([core, coils], asset_extras={"motor_id": "SELFTEST"})[0]
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "SELFTEST_full.glb")
        with open(src, "wb") as fh:
            fh.write(data)
        r = pack(src, os.path.join(d, "SELFTEST_full.meshopt.glb"), cmd)
    if not r["ok"]:
        return False, "gltfpack self-test failed: %s" % r["message"]
    return True, "gltfpack self-test ok (%s): %d -> %d bytes" % (tool_version(cmd or find_tool() or []), r["bytes_in"], r["bytes_out"])

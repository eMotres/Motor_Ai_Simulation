"""Minimal glTF 2.0 binary (GLB) writer for the motor viewer.  Pure Python, no FreeCAD, no numpy.

A scene is a list of GROUPS (one per physical part: Core, Coils, Rotor, ...).  A group is a node named after the part,
carrying `extras` (part id, `explode` axial factor for the viewer's exploded view, colour name) and one child node per
mesh (Coil_01 ... Coil_12); all meshes of a group share one material.  Units are millimetres, Z along the motor axis
(the viewer rotates the model, the file keeps the CAD frame).  Normals are smooth inside a face and hard across faces
(the caller passes one mesh per solid; a solid's faces are tessellated separately, see `Mesh`).

    g = Group("Core", colour=(0.6, 0.6, 0.65), extras={"explode": 0.0})
    g.add("Core", [Mesh(vertices, triangles), ...])
    glb = build_glb([g], asset_extras={"motor_id": "..."})
"""
import array
import hashlib
import json
import math
import struct

GLB_MAGIC = 0x46546C67          # 'glTF'
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
FLOAT = 5126
UINT = 5125


class Mesh:
    """Triangles of one tessellated face (or any patch): `vertices` [(x, y, z)], `triangles` [(i, j, k)] outward-wound."""
    __slots__ = ("vertices", "triangles")

    def __init__(self, vertices, triangles):
        self.vertices = vertices
        self.triangles = triangles


class Group:
    def __init__(self, name, colour, extras=None, metallic=0.1, roughness=0.55, alpha=1.0):
        self.name = name
        self.colour = tuple(colour)
        self.extras = dict(extras or {})
        self.metallic = metallic
        self.roughness = roughness
        self.alpha = alpha
        self.items = []                     # [(node name, [Mesh, ...], node extras or None)]

    def add(self, name, patches, extras=None):
        """One mesh node.  `extras` (a dict) go into the glTF node's extras, e.g. {"thread": {...}} (cad/fc_scripts/threads.py)."""
        self.items.append((name, list(patches), dict(extras) if extras else None))


def smooth_normals(vertices, triangles):
    """Area-weighted vertex normals of one patch (vertices are shared inside a face, so curved faces come out smooth and
    planar faces exact).  Degenerate vertices get +Z."""
    acc = [0.0] * (3 * len(vertices))
    for i, j, k in triangles:
        ax, ay, az = vertices[i]
        bx, by, bz = vertices[j]
        cx, cy, cz = vertices[k]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx    # |n| = 2 x area
        for v in (i, j, k):
            acc[3 * v] += nx
            acc[3 * v + 1] += ny
            acc[3 * v + 2] += nz
    out = array.array("f", [0.0]) * len(acc)
    for v in range(len(vertices)):
        x, y, z = acc[3 * v], acc[3 * v + 1], acc[3 * v + 2]
        n = math.sqrt(x * x + y * y + z * z)
        if n < 1e-30:
            out[3 * v + 2] = 1.0
        else:
            out[3 * v], out[3 * v + 1], out[3 * v + 2] = x / n, y / n, z / n
    return out


def signed_volume(vertices, triangles):
    """Volume of the closed triangle surface (positive = outward winding).  Used by tests and the export check."""
    s = 0.0
    for i, j, k in triangles:
        a, b, c = vertices[i], vertices[j], vertices[k]
        s += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return s / 6.0


def _merge(patches):
    """-> (positions float32 array, normals float32 array, indices uint32 array, bbox min, bbox max)."""
    pos = array.array("f")
    nor = array.array("f")
    idx = array.array("I")
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    base = 0
    for p in patches:
        if not p.triangles:
            continue
        nn = smooth_normals(p.vertices, p.triangles)
        for v in p.vertices:
            pos.extend((v[0], v[1], v[2]))
            for a in range(3):
                lo[a] = min(lo[a], v[a])
                hi[a] = max(hi[a], v[a])
        nor.extend(nn)
        for t in p.triangles:
            idx.extend((base + t[0], base + t[1], base + t[2]))
        base += len(p.vertices)
    return pos, nor, idx, lo, hi


def geometry_hash(groups):
    """SHA-256 of the GEOMETRY of the groups, independent of how the meshes are split into nodes: per group (in order) the sorted
    set of triangles, each as its three vertices rounded to 1e-4 mm, started at the smallest vertex (the winding is kept).  Splitting a
    face off into its own node (the thread nodes) leaves it unchanged; moving a vertex or dropping a triangle does not."""
    h = hashlib.sha256()
    for g in groups:
        tris = []
        for item in g.items:
            for p in item[1]:
                vs = [tuple(round(c, 4) + 0.0 for c in v) for v in p.vertices]
                for i, j, k in p.triangles:
                    t = (vs[i], vs[j], vs[k])
                    m = t.index(min(t))
                    tris.append(t[m:] + t[:m])
        tris.sort()
        h.update(g.name.encode("utf-8"))
        h.update(repr(tris).encode("ascii"))
    return h.hexdigest()


def build_glb(groups, asset_extras=None, generator="motor_ai_sim.cad"):
    """GLB bytes.  Node tree: scene -> `Model` (the root node) -> one node per group -> one node per mesh."""
    bin_parts = []                  # (bytes, target)
    views, accessors, meshes, materials, nodes = [], [], [], [], []
    group_nodes = []
    stats = dict(triangles=0, vertices=0, groups={})

    def add_view(data, target):
        off = sum(len(b) for b, _ in bin_parts)
        pad = (-len(data)) % 4
        bin_parts.append((data + b"\0" * pad, target))
        views.append(dict(buffer=0, byteOffset=off, byteLength=len(data), target=target))
        return len(views) - 1

    def _linear(c):
        """Group colours are sRGB (the palette hex values); glTF baseColorFactor is LINEAR: without this the viewer shows every
        colour paler than its hex value (the washed-out look)."""
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    for g in groups:
        mat = len(materials)
        materials.append(dict(name=g.name, pbrMetallicRoughness=dict(
            baseColorFactor=[round(_linear(c), 4) for c in g.colour] + [g.alpha], metallicFactor=g.metallic, roughnessFactor=g.roughness),
            doubleSided=True, **({"alphaMode": "BLEND"} if g.alpha < 1.0 else {})))
        child_ids = []
        ntri = 0
        for name, patches, node_extras in g.items:
            pos, nor, idx, lo, hi = _merge(patches)
            if not len(idx):
                continue
            if struct.pack("=I", 1) != struct.pack("<I", 1):                      # big-endian host
                pos.byteswap(); nor.byteswap(); idx.byteswap()
            a_pos = len(accessors)
            accessors.append(dict(bufferView=add_view(pos.tobytes(), ARRAY_BUFFER), componentType=FLOAT, count=len(pos) // 3,
                                  type="VEC3", min=[round(v, 5) for v in lo], max=[round(v, 5) for v in hi]))
            accessors.append(dict(bufferView=add_view(nor.tobytes(), ARRAY_BUFFER), componentType=FLOAT, count=len(nor) // 3, type="VEC3"))
            accessors.append(dict(bufferView=add_view(idx.tobytes(), ELEMENT_ARRAY_BUFFER), componentType=UINT, count=len(idx), type="SCALAR"))
            meshes.append(dict(name=name, primitives=[dict(attributes=dict(POSITION=a_pos, NORMAL=a_pos + 1), indices=a_pos + 2,
                                                           material=mat, mode=4)]))
            nodes.append(dict(name=name, mesh=len(meshes) - 1, **({"extras": node_extras} if node_extras else {})))
            child_ids.append(len(nodes) - 1)
            ntri += len(idx) // 3
            stats["vertices"] += len(pos) // 3
        if not child_ids:
            continue
        stats["triangles"] += ntri
        stats["groups"][g.name] = ntri
        nodes.append(dict(name=g.name, children=child_ids, extras=dict(g.extras, part=g.name, triangles=ntri)))
        group_nodes.append(len(nodes) - 1)
    nodes.append(dict(name="Model", children=group_nodes))
    root = len(nodes) - 1
    blob = b"".join(b for b, _ in bin_parts)
    doc = dict(asset=dict(version="2.0", generator=generator, extras=dict(asset_extras or {}, units="mm", up_axis="Z (motor axis)")),
               scene=0, scenes=[dict(nodes=[root])], nodes=nodes, meshes=meshes, materials=materials, accessors=accessors,
               bufferViews=views, buffers=[dict(byteLength=len(blob))])
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    out = struct.pack("<III", GLB_MAGIC, 2, total) + struct.pack("<II", len(js), CHUNK_JSON) + js \
        + struct.pack("<II", len(blob), CHUNK_BIN) + blob
    return out, stats


def glb_geometry_hash(data):
    """Same idea as `geometry_hash`, read back from a GLB file (positions are float32 there): per group node of `Model`, the sorted
    triangles of all its mesh children.  Two files that differ only in how faces are split into nodes (or in node extras) give the same
    value; used to prove that the thread nodes leave the geometry as it was."""
    doc, blob = parse_glb(data)
    nodes = doc["nodes"]
    h = hashlib.sha256()

    def acc(i):
        a = doc["accessors"][i]
        bv = doc["bufferViews"][a["bufferView"]]
        n = {"VEC3": 3, "SCALAR": 1}[a["type"]]
        fmt = {FLOAT: "f", UINT: "I"}[a["componentType"]]
        return struct.unpack_from("<%d%s" % (a["count"] * n, fmt), blob, bv["byteOffset"])

    model = next(n for n in nodes if n["name"] == "Model")
    for gi in model["children"]:
        g = nodes[gi]
        tris = []
        for ci in g["children"]:
            prim = doc["meshes"][nodes[ci]["mesh"]]["primitives"][0]
            pos = acc(prim["attributes"]["POSITION"])
            idx = acc(prim["indices"])
            vs = [tuple(round(pos[3 * k + c], 4) + 0.0 for c in range(3)) for k in range(len(pos) // 3)]
            for t in range(0, len(idx), 3):
                tri = (vs[idx[t]], vs[idx[t + 1]], vs[idx[t + 2]])
                m = tri.index(min(tri))
                tris.append(tri[m:] + tri[:m])
        tris.sort()
        h.update(g["name"].encode("utf-8"))
        h.update(repr(tris).encode("ascii"))
    return h.hexdigest()


def glb_group_signature(data):
    """{group name: (triangles, unique vertices, volume, vertex-set hash)} of a GLB.  Looser than `glb_geometry_hash`: the diagonals
    OCC picks inside a planar face may differ from run to run (seen on the CIANO28 85 holders), the vertices, the triangle counts and
    the enclosed volume do not.  The thread export test compares this between a build with and without the thread nodes."""
    doc, blob = parse_glb(data)
    nodes = doc["nodes"]
    out = {}

    def acc(i):
        a = doc["accessors"][i]
        bv = doc["bufferViews"][a["bufferView"]]
        n = {"VEC3": 3, "SCALAR": 1}[a["type"]]
        fmt = {FLOAT: "f", UINT: "I"}[a["componentType"]]
        return struct.unpack_from("<%d%s" % (a["count"] * n, fmt), blob, bv["byteOffset"])

    for gi in next(n for n in nodes if n["name"] == "Model")["children"]:
        g = nodes[gi]
        vs, nt, vol = set(), 0, 0.0
        for ci in g["children"]:
            prim = doc["meshes"][nodes[ci]["mesh"]]["primitives"][0]
            pos, idx = acc(prim["attributes"]["POSITION"]), acc(prim["indices"])
            pts = [tuple(pos[3 * k:3 * k + 3]) for k in range(len(pos) // 3)]
            vs.update(tuple(round(c, 4) + 0.0 for c in p) for p in pts)
            nt += len(idx) // 3
            vol += signed_volume(pts, [tuple(idx[t:t + 3]) for t in range(0, len(idx), 3)])
        out[g["name"]] = (nt, len(vs), round(vol, 2), hashlib.sha256(repr(sorted(vs)).encode("ascii")).hexdigest()[:16])
    return out


def parse_glb(data):
    """-> (json dict, bin bytes).  Used by the tests and by the export self-check."""
    magic, ver, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or ver != 2 or length != len(data):
        raise ValueError("not a GLB 2.0 file")
    jl, jt = struct.unpack_from("<II", data, 12)
    if jt != CHUNK_JSON:
        raise ValueError("first chunk is not JSON")
    doc = json.loads(data[20:20 + jl].decode("utf-8"))
    bl, bt = struct.unpack_from("<II", data, 20 + jl)
    if bt != CHUNK_BIN:
        raise ValueError("second chunk is not BIN")
    return doc, data[28 + jl:28 + jl + bl]

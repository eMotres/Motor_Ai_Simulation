"""Global (admin-managed) materials layer, persisted in Firestore.

Built-in materials live in `materials_library.yaml` (read-only defaults). Admins
add / edit / delete materials in a SHARED layer stored in the Firestore collection
`materials_global` (one doc per material, id = "{category}__{name}"). This module
reads that layer and merges it OVER the built-in library; the merged view is what
GET /api/materials/library returns, so every client sees the same catalogue.

Mirrors routes/admin.py: the Firebase Admin SDK initialises from Application
Default Credentials on Cloud Run; locally (no creds) the SDK is unavailable and
the global layer is simply empty (built-in only), so dev is unaffected. Per-user
"my materials" are a SEPARATE, client-side Firestore layer (users/{uid}/materials)
merged in the frontend — not here.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Optional

_COLLECTION = "materials_global"
VALID_CATEGORIES = ("steel", "magnet", "conductor", "insulator", "coolant")

# Keys that are bookkeeping, not physical properties — stripped from the merged view.
_META_KEYS = {"category", "name", "hidden", "updatedBy", "updatedAt"}

_admin_mod = None
_init_done = False


def _ensure_admin():
    """Lazy-init firebase-admin once; None if the package/creds are unavailable."""
    global _admin_mod, _init_done
    if _init_done:
        return _admin_mod
    _init_done = True
    try:
        import firebase_admin
        if not firebase_admin._apps:                 # don't double-init (admin.py may have)
            firebase_admin.initialize_app()
        _admin_mod = firebase_admin
    except Exception:                                # noqa: BLE001 — no SDK / no ADC -> empty layer
        _admin_mod = None
    return _admin_mod


def _db():
    if _ensure_admin() is None:
        return None
    from firebase_admin import firestore
    return firestore.client()


def _doc_id(category: str, name: str) -> str:
    return f"{category}__{name}"


def available() -> bool:
    """True iff the Firestore-backed global layer is reachable (else built-in only)."""
    return _db() is not None


def read_global() -> Dict[str, Dict[str, dict]]:
    """{category: {name: raw_doc}} from Firestore. Empty if the SDK is unavailable."""
    db = _db()
    if db is None:
        return {}
    out: Dict[str, Dict[str, dict]] = {}
    try:
        for d in db.collection(_COLLECTION).stream():
            data = d.to_dict() or {}
            cat, name = data.get("category"), data.get("name")
            if cat in VALID_CATEGORIES and name:
                out.setdefault(cat, {})[name] = data
    except Exception:                                # noqa: BLE001 — non-fatal, fall back to built-in
        return {}
    return out


# Short-TTL cache so the solver (which calls get_material many times per solve, and
# in a subprocess for transients) doesn't hit Firestore on every lookup. Writes
# below invalidate it; otherwise it refreshes every _GLOBAL_TTL seconds.
_GLOBAL_TTL = 30.0
_global_cache: Optional[Dict[str, Dict[str, dict]]] = None
_global_cache_t = 0.0


def _read_global_cached() -> Dict[str, Dict[str, dict]]:
    global _global_cache, _global_cache_t
    now = time.time()
    if _global_cache is not None and (now - _global_cache_t) < _GLOBAL_TTL:
        return _global_cache
    _global_cache = read_global()
    _global_cache_t = now
    return _global_cache


def _invalidate_cache() -> None:
    global _global_cache
    _global_cache = None


def global_material(category: str, name: str) -> Optional[Dict[str, Any]]:
    """Physical props of a single GLOBAL material (None if absent or hidden); meta
    keys stripped. Cached. Used by materials.get_material so admin-added / edited
    materials resolve in every compute path (field maps, transient, analytical)."""
    doc = _read_global_cached().get(category, {}).get(name)
    if not doc or doc.get("hidden"):
        return None
    return {k: v for k, v in doc.items() if k not in _META_KEYS}


def merge_library(builtin: Dict[str, Dict[str, dict]]) -> Dict[str, Dict[str, dict]]:
    """Merge the global layer over the built-in library.

    Each entry is tagged `_source` ('builtin'|'global') and `_editable` (only the
    global layer is editable from the UI). A global doc with hidden=True removes
    the (built-in) entry from the catalogue ("delete" of a built-in)."""
    merged: Dict[str, Dict[str, dict]] = {}
    for cat, mats in (builtin or {}).items():
        merged[cat] = {name: {**props, "_source": "builtin", "_editable": False}
                       for name, props in mats.items()}
    for cat, mats in read_global().items():
        merged.setdefault(cat, {})
        for name, doc in mats.items():
            if doc.get("hidden"):
                merged[cat].pop(name, None)
                continue
            props = {k: v for k, v in doc.items() if k not in _META_KEYS}
            merged[cat][name] = {**props, "_source": "global", "_editable": True}
    return merged


def upsert_global(category: str, name: str, props: Dict[str, Any], who: str) -> dict:
    """Create or update a global material (admin). Returns a status dict."""
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid category {category!r}; valid: {VALID_CATEGORIES}")
    if not name or not name.strip():
        raise ValueError("material name is required")
    db = _db()
    if db is None:
        return {"ok": True, "source": "mock", "category": category, "name": name}
    from firebase_admin import firestore
    clean = {k: v for k, v in (props or {}).items() if k not in _META_KEYS and not k.startswith("_")}
    doc = {**clean, "category": category, "name": name, "hidden": False,
           "updatedBy": who, "updatedAt": firestore.SERVER_TIMESTAMP}
    db.collection(_COLLECTION).document(_doc_id(category, name)).set(doc, merge=True)
    _invalidate_cache()
    return {"ok": True, "source": "firebase", "category": category, "name": name}


def delete_global(category: str, name: str, who: str,
                  builtin_names: Optional[Iterable[str]] = None) -> dict:
    """Delete a global material. If `name` is a built-in, store a hidden marker
    instead (the built-in YAML can't be mutated) so it vanishes from the merge."""
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid category {category!r}")
    db = _db()
    if db is None:
        return {"ok": True, "source": "mock", "category": category, "name": name}
    from firebase_admin import firestore
    ref = db.collection(_COLLECTION).document(_doc_id(category, name))
    if builtin_names and name in set(builtin_names):
        ref.set({"category": category, "name": name, "hidden": True,
                 "updatedBy": who, "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True)
        _invalidate_cache()
        return {"ok": True, "source": "firebase", "hidden": True, "category": category, "name": name}
    ref.delete()
    _invalidate_cache()
    return {"ok": True, "source": "firebase", "deleted": True, "category": category, "name": name}

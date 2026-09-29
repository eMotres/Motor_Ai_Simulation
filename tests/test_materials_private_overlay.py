"""Private materials overlay (MOTOR_AI_SIM_PRIVATE_DATA/config/materials_overrides.yaml)."""
import yaml

from motor_ai_sim import materials as M


def _reset(monkeypatch, lib_path):
    monkeypatch.setattr(M, "_LIB_PATH", lib_path)
    monkeypatch.setattr(M, "_library", None)
    monkeypatch.setattr(M, "_lib_checked", 0.0)


def _write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data), encoding="utf-8")


BASE = {"magnet": {"N52UH_20C": {
    "Br": 1.43, "grade": "N52UH",
    "sources": [{"id": "arnold"}],
    "prov": {"Br": {"type": "derived", "src": "arnold"}},
}}}


def test_no_env_means_public_library_only(tmp_path, monkeypatch):
    lib = tmp_path / "materials_library.yaml"
    _write(lib, BASE)
    monkeypatch.delenv(M.PRIVATE_DATA_ENV, raising=False)
    _reset(monkeypatch, lib)
    card = M._load()["magnet"]["N52UH_20C"]
    assert card["Br"] == 1.43 and "private_overlay" not in card


def test_overlay_replaces_fields_and_keeps_provenance(tmp_path, monkeypatch):
    lib = tmp_path / "materials_library.yaml"
    _write(lib, BASE)
    priv = tmp_path / "private"
    _write(priv / "config" / "materials_overrides.yaml", {"magnet": {
        "N52UH_20C": {"Br": 1.426,
                      "sources": [{"id": "lot"}],
                      "prov": {"Br": {"type": "measured", "src": "lot"}}},
        "NEW_CARD": {"Br": 1.0},
    }})
    monkeypatch.setenv(M.PRIVATE_DATA_ENV, str(priv))
    _reset(monkeypatch, lib)
    mag = M._load()["magnet"]
    card = mag["N52UH_20C"]
    assert card["Br"] == 1.426
    assert card["grade"] == "N52UH"                        # untouched field kept
    assert [s["id"] for s in card["sources"]] == ["arnold", "lot"]
    assert card["prov"]["Br"]["src"] == "lot"
    assert card["private_overlay"] is True
    assert mag["NEW_CARD"]["private_overlay"] is True


def test_broken_overlay_is_ignored(tmp_path, monkeypatch):
    lib = tmp_path / "materials_library.yaml"
    _write(lib, BASE)
    priv = tmp_path / "private"
    (priv / "config").mkdir(parents=True)
    (priv / "config" / "materials_overrides.yaml").write_text("magnet: [unclosed", encoding="utf-8")
    monkeypatch.setenv(M.PRIVATE_DATA_ENV, str(priv))
    _reset(monkeypatch, lib)
    assert M._load()["magnet"]["N52UH_20C"]["Br"] == 1.43

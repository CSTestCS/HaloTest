"""The extraction cache: models and textures pulled out of each game's Editing Kit.

Layout (one folder per source model):
  <cache>/<game>/<model>/model.json          ManagedBlam dump of the render model  (tag sources)
  <cache>/<game>/<model>/shaders/<slug>.json dumps of the shaders it uses
  <cache>/<game>/<model>/bitmaps/<slug>.tga  textures exported by tool.exe
  <cache>/<game>/<model>/model.jms           JMS export (Halo CE, or a fallback for any game)
  <cache>/<game>/<model>/<material>_<role>.tga|tif   textures for JMS sources
"""
from __future__ import annotations

import json
from pathlib import Path

from . import jms, tagdump
from .umesh import UMesh

TEXTURE_EXTS = (".tga", ".tif", ".tiff")


class MissingExtraction(Exception):
    def __init__(self, game: str, model: str, path: Path, how: str = ""):
        super().__init__(f"{game}/{model}: not extracted yet ({path})")
        self.game, self.model, self.path, self.how = game, model, path, how


def slug(tag_path: str) -> str:
    return tag_path.replace("\\", "/").rsplit(".", 1)[0].strip("/").replace("/", "__")


def model_dir(cache: Path, game: str, model: str) -> Path:
    return Path(cache) / game / model


def is_extracted(cache: Path, game: dict, model_key: str) -> bool:
    d = model_dir(cache, game["id"], model_key)
    return (d / "model.json").is_file() or (d / "model.jms").is_file()


def _find_texture(folder: Path, stem: str) -> str | None:
    for ext in TEXTURE_EXTS:
        p = folder / (stem + ext)
        if p.is_file():
            return str(p)
    return None


def load_model(cache: Path, game: dict, model_key: str) -> UMesh:
    spec = game["models"][model_key]
    d = model_dir(cache, game["id"], model_key)
    # A hand-exported model.jms works for any game, as a fallback when a tag dump isn't available.
    if "jms" in spec or (not (d / "model.json").is_file() and (d / "model.jms").is_file()):
        path = d / "model.jms"
        if not path.is_file():
            raise MissingExtraction(game["id"], model_key, path, spec.get("how", "").replace("{cache}", str(cache)))
        mesh = jms.read(path, source_game=game["id"])
        for m in mesh.materials:
            m.textures = {}
            for role in ("base", "normal", "specular", "change_color"):
                found = _find_texture(d, f"{m.name}_{role}")
                if found:
                    m.textures[role] = found
        return mesh

    path = d / "model.json"
    if not path.is_file():
        raise MissingExtraction(game["id"], model_key, path)
    mesh = tagdump.render_model_to_umesh(json.loads(path.read_text()), game.get("dump_fields"), game["id"])
    for m in mesh.materials:
        if not m.shader:
            continue
        sp = d / "shaders" / (slug(m.shader) + ".json")
        if not sp.is_file():
            continue
        for role, bitmap in tagdump.shader_bitmaps(json.loads(sp.read_text())).items():
            found = _find_texture(d / "bitmaps", slug(bitmap))
            if found:
                m.textures[role] = found
    return mesh


class Loader:
    """Caches parsed models for one build and records which ones are missing."""

    def __init__(self, cache: Path, catalog):
        self.cache, self.catalog = Path(cache), catalog
        self._models: dict[tuple, UMesh] = {}
        self.missing: dict[tuple, MissingExtraction] = {}

    def __call__(self, game_id: str, model_key: str) -> UMesh:
        key = (game_id, model_key)
        if key in self.missing:
            raise self.missing[key]
        if key not in self._models:
            try:
                self._models[key] = load_model(self.cache, self.catalog.game(game_id), model_key)
            except MissingExtraction as e:
                self.missing[key] = e
                raise
        return self._models[key]

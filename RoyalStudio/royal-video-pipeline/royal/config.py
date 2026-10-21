"""Loads config.yaml and .env, resolves paths relative to the project folder."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent


class Config(dict):
    """dict with attribute access for top-level sections: cfg.faces["enabled"]."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def path(self, key: str) -> Path:
        p = Path(self["paths"][key])
        return p if p.is_absolute() else (PROJECT_DIR / p).resolve()


def load_config(path: str | os.PathLike | None = None) -> Config:
    load_dotenv(PROJECT_DIR / ".env")
    cfg_path = Path(path) if path else PROJECT_DIR / "config.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = Config(yaml.safe_load(fh))
    cfg.path("data_dir").mkdir(parents=True, exist_ok=True)
    cfg.path("models_dir").mkdir(parents=True, exist_ok=True)
    return cfg


def pretty_name(folder: str) -> str:
    """'King_Charles' -> 'King Charles'."""
    return folder.replace("_", " ").strip()

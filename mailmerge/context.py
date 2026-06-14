"""Runtime context: ties together config, database, and resolved paths.

One small object that the CLI builds once and threads through every command, so
modules never have to re-derive where the templates / resume / db live.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config, load_config
from .db import Database


class Context:
    def __init__(self, base_dir: Path, cfg: Config, db: Database):
        self.base_dir = base_dir
        self.cfg = cfg
        self.db = db

    # -- resolved paths ----------------------------------------------------

    def resolve(self, path: str) -> Path:
        return self.cfg.resolve(self.base_dir, path)

    @property
    def templates_dir(self) -> Path:
        return self.base_dir / "templates"

    @property
    def resume_path(self) -> Path:
        return self.resolve(self.cfg["resume"]["path"])

    @property
    def output_dir(self) -> Path:
        return self.resolve(self.cfg["sending"]["dry_run_output_dir"])

    @property
    def db_path(self) -> Path:
        return self.resolve(self.cfg["database"]["path"])

    # -- construction ------------------------------------------------------

    @classmethod
    def create(cls, base_dir: Path, config_path: str) -> "Context":
        cfg = load_config(config_path, base_dir)
        db_path = cfg.resolve(base_dir, cfg["database"]["path"])
        db = Database(db_path)
        return cls(base_dir, cfg, db)

    def close(self) -> None:
        self.db.close()

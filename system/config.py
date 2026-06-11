"""System configuration dataclass."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Global system configuration.

    All paths are resolved relative to the project root at runtime.
    """

    config_dir: str | Path = "config"
    profiles_dir: str | Path = "device_manager/device_profiles"
    db_path: str | Path = "data/agrimesh.db"
    rules_path: str | Path = "config/rules.yaml"
    notifiers_path: str | Path = "config/notifiers.yaml"

    def __post_init__(self) -> None:
        """Normalize all paths to Path objects and create missing directories."""
        for field in ["config_dir", "profiles_dir"]:
            val = getattr(self, field)
            path = Path(val)
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                logger.warning("created missing path: %s", path)
            setattr(self, field, path.resolve())

        self.db_path = Path(self.db_path).resolve()
        self.rules_path = Path(self.rules_path).resolve()
        self.notifiers_path = Path(self.notifiers_path).resolve()

        db_dir = self.db_path.parent
        db_dir.mkdir(parents=True, exist_ok=True)

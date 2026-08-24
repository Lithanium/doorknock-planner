from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

USER_AGENT = "doorknock-planner/0.1 (local campaign tool; contact: campaign volunteer)"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    The district is identified by its OpenStreetMap ``boundary=political``
    relation (``political_division=au_vic_la``), which is the Victorian
    Legislative Assembly district boundary.
    """

    district_relation_id: int = 15624487
    district_slug: str = "kew"
    data_dir: Path = REPO_ROOT / "data"
    mirrors: tuple[str, ...] = DEFAULT_MIRRORS
    overpass_timeout_s: int = 600
    max_attempts: int = 8

    @property
    def snapshot_path(self) -> Path:
        return self.data_dir / "district" / f"{self.district_slug}.json.gz"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"


def load_settings() -> Settings:
    overrides: dict = {}
    if v := os.environ.get("DK_DISTRICT_RELATION_ID"):
        overrides["district_relation_id"] = int(v)
    if v := os.environ.get("DK_DISTRICT_SLUG"):
        overrides["district_slug"] = v
    if v := os.environ.get("DK_DATA_DIR"):
        overrides["data_dir"] = Path(v).resolve()
    return Settings(**overrides)

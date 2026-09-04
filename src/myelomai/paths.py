from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataPaths:
    root: Path
    raw: Path
    raw_spreadsheets: Path
    raw_fcs: Path
    deidentified: Path
    deid_spreadsheets: Path
    deid_fcs: Path
    quarantine: Path
    private: Path
    manifests: Path
    audit: Path

    @classmethod
    def from_root(cls, root: Path) -> "DataPaths":
        root = root.expanduser().resolve()
        return cls(
            root=root,
            raw=root / "raw",
            raw_spreadsheets=root / "raw" / "spreadsheets",
            raw_fcs=root / "raw" / "fcs",
            deidentified=root / "deidentified",
            deid_spreadsheets=root / "deidentified" / "spreadsheets",
            deid_fcs=root / "deidentified" / "fcs",
            quarantine=root / "quarantine",
            private=root / "private",
            manifests=root / "manifests",
            audit=root / "audit",
        )

    def ensure(self) -> None:
        for p in (
            self.raw_spreadsheets,
            self.raw_fcs,
            self.deid_spreadsheets,
            self.deid_fcs,
            self.quarantine,
            self.private,
            self.manifests,
            self.audit,
        ):
            p.mkdir(parents=True, exist_ok=True)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_data_root() -> Path:
    """Resolve the external data root without storing it in Git.

    Priority:
      1. MYELOMAI_DATA_ROOT environment variable
      2. repo-local config.local.toml (gitignored)
    """
    env = os.getenv("MYELOMAI_DATA_ROOT")
    if env:
        return Path(env)

    cfg = _repo_root() / "config.local.toml"
    if cfg.exists():
        with cfg.open("rb") as f:
            data = tomllib.load(f)
        value = data.get("data_root")
        if value:
            return Path(value)

    raise RuntimeError(
        "Myelom.ai data root is not configured. Set MYELOMAI_DATA_ROOT or copy "
        "config.local.example.toml to config.local.toml and set data_root."
    )


def get_paths() -> DataPaths:
    paths = DataPaths.from_root(resolve_data_root())
    paths.ensure()
    return paths

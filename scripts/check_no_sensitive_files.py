"""Optional pre-commit guard against accidentally staging obvious clinical-data files."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BLOCKED_SUFFIXES = {
    ".fcs", ".xlsx", ".xls", ".xlsm", ".parquet", ".feather",
    ".h5", ".hdf5", ".npy", ".npz", ".pkl", ".pickle",
}
BLOCKED_NAME_PARTS = {
    "identity_map", "reident", "crosswalk", "subject_map", "specimen_map",
    "fcs_file_map", "deid_secret",
}


def main() -> int:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=True,
        capture_output=True,
        text=True,
    )
    bad = []
    for line in proc.stdout.splitlines():
        p = Path(line)
        low = p.name.lower()
        if p.suffix.lower() in BLOCKED_SUFFIXES or any(x in low for x in BLOCKED_NAME_PARTS):
            bad.append(line)
    if bad:
        print("Refusing commit: possible clinical/private files are staged:", file=sys.stderr)
        for item in bad:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

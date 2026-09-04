from __future__ import annotations

import argparse

from myelomai.deid.pipeline import run_deidentification
from myelomai.paths import get_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Deidentify Myelom.ai spreadsheets and FCS files")
    parser.add_argument(
        "--include-bma",
        action="store_true",
        help="Also export a deidentified BMA spreadsheet. BMA is not used as a model input by default.",
    )
    args = parser.parse_args()
    paths = get_paths()
    run_deidentification(paths, include_bma=args.include_bma)


if __name__ == "__main__":
    main()

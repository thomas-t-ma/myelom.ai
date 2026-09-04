from myelomai.paths import get_paths


def main() -> None:
    p = get_paths()
    p.ensure()
    print(f"Initialized Myelom.ai data layout at: {p.root}")
    print("\nPlace source files here:")
    print(f"  {p.raw_spreadsheets / 'flow.xlsx'}")
    print(f"  {p.raw_spreadsheets / 'BMA.xlsx'}  (optional)")
    print(f"  {p.raw_fcs}")


if __name__ == "__main__":
    main()

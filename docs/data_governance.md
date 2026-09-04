# Myelom.ai data governance and storage

## Recommended architecture

Keep **all clinical data on an encrypted external SSD**. Keep only code, documentation,
configuration templates, and non-sensitive aggregate results in GitHub.

The repository learns the SSD location from either:

1. `MYELOMAI_DATA_ROOT`, or
2. `config.local.toml` (ignored by Git).

This means the SSD can be `E:` on one workstation and `F:` on another without changing
committed code.

## External SSD layout

```text
MyelomAI_Data/
├── raw/                         # PHI; never share
│   ├── spreadsheets/
│   │   ├── flow.xlsx
│   │   └── BMA.xlsx            # optional
│   └── fcs/                    # original clinical FCS files
├── deidentified/               # candidate shareable dataset
│   ├── spreadsheets/
│   └── fcs/
├── manifests/                  # deidentified linkage/QC manifests
├── quarantine/                 # reserved; no raw-file copying by default
├── private/                    # REIDENTIFICATION MATERIAL; never share
│   ├── deid_secret.key
│   ├── identity_map.csv
│   ├── specimen_map.csv
│   ├── fcs_file_map.csv
│   ├── fcs_unresolved.csv
│   └── fcs_overrides.csv       # optional manual overrides
└── audit/
```

Only `deidentified/` and specifically reviewed shareable manifests should ever leave the
protected storage environment. `private/`, `raw/`, and audit material should be treated as identifiable clinical data.

## Encryption

Use institution-approved full-disk encryption for the external SSD (for example,
BitLocker To Go on Windows if permitted by your institution). Store the recovery key
separately from the SSD. The pseudonymization secret and identity maps are intentionally
kept on the external data volume, never in GitHub.

## What the deidentifier does

### Spreadsheets

- Links the same patient across rows and across `flow.xlsx` / `BMA.xlsx` using MRN and
  name+DOB aliases locally.
- Replaces patient identity with a stable `Subject_ID`.
- Replaces specimen number with a stable `Specimen_ID`.
- Removes name, DOB, MRN, exact accession date, and `concat`.
- Replaces physician identity with a stable `Provider_ID`.
- Keeps accession **year** and within-patient days from first specimen for longitudinal
  analysis without exposing exact dates.
- Scrubs known names, MRNs, specimen numbers, DOB/accession date strings, and submitting
  physician names from narrative `Text`.
- Keeps the reidentification crosswalk only in `private/`.

### FCS files

Renaming a file is not enough: FCS TEXT metadata can contain names, dates, original
filenames, instrument identifiers, comments, or accession data.

The included FCS scrubber therefore:

- determines patient/specimen linkage locally,
- generates a pseudonymous filename,
- redacts nonessential FCS TEXT metadata in-place,
- preserves structural metadata, channel labels, and spillover/compensation metadata,
- verifies event/channel counts and channel signatures after writing,
- verifies the FCS DATA segment is byte-for-byte unchanged,
- rejects files with supplemental TEXT, ANALYSIS, or multiple datasets rather than
  making an unsafe guess,
- records unmatched, ambiguous, or failed files in a private unresolved manifest rather than guessing or duplicating the raw data.

The `panel_hint_unverified` field records filename hints such as `MM`, `B`, `T`, `M1`, or
`M2`, but these are **not treated as authoritative panel labels**. The manifest also stores
an actual channel signature from the FCS metadata so panels can later be identified from
what was truly measured.

## Important limitation

This is a conservative research deidentification utility, not a legal determination that
a dataset satisfies HIPAA deidentification requirements. Before external disclosure,
follow your IRB/institutional privacy and data-use requirements and manually review the
resulting deidentified tables/manifests for residual identifiers.

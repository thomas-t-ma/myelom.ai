# Myelom.ai

Research codebase for an end-to-end, interpretable diagnostic flow-cytometry system for
detection and characterization of abnormal/clonal plasma-cell populations.

## Design principle

**GitHub = code. External encrypted SSD = data.**

No raw or deidentified patient-level data should be committed to this repository.

## Repository structure

```text
MyelomAI_starter/
├── configs/                     # committed experiment/config templates
├── docs/
│   └── data_governance.md
├── notebooks/                   # exploratory notebooks; no embedded PHI
├── scripts/
│   ├── init_external_data.py
│   └── deidentify_data.py
├── src/myelomai/
│   ├── paths.py
│   └── deid/
│       ├── core.py
│       └── pipeline.py
├── tests/                       # synthetic fixtures only
├── models/                      # model definitions later
├── results/                     # aggregate/non-sensitive results only
├── .gitignore
├── config.local.example.toml
└── pyproject.toml
```

## First-time setup on a workstation

```powershell
cd MyelomAI_starter
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item config.local.example.toml config.local.toml
```

Edit `config.local.toml` so `data_root` points to the external SSD, e.g.:

```toml
data_root = "E:/MyelomAI_Data"
```

Alternatively, set `MYELOMAI_DATA_ROOT` in the workstation environment. A different
workstation can use a different drive letter without changing the repository.

Initialize folders:

```powershell
python scripts/init_external_data.py
```

Then place data at:

```text
<SSD>/MyelomAI_Data/raw/spreadsheets/flow.xlsx
<SSD>/MyelomAI_Data/raw/spreadsheets/BMA.xlsx   # optional
<SSD>/MyelomAI_Data/raw/fcs/*.fcs
```

## Deidentify

For the primary FCS-only project:

```powershell
python scripts/deidentify_data.py
```

To also export a deidentified BMA table for secondary validation/adjudication:

```powershell
python scripts/deidentify_data.py --include-bma
```

### Recommended BMA role

Do **not** use BMA information as an input to the primary Myelom.ai classifier. The model
should remain an FCS-only diagnostic system. Keeping a separately deidentified BMA table
can still be valuable for:

- adjudicating flow/report disagreements,
- comparing flow-estimated plasma-cell burden with marrow morphology,
- secondary validation,
- future multimodal work.

This preserves a clean primary claim while keeping useful reference-standard information.

## Outputs

Candidate shareable outputs:

```text
<SSD>/MyelomAI_Data/deidentified/spreadsheets/flow_deidentified.xlsx
<SSD>/MyelomAI_Data/deidentified/fcs/
<SSD>/MyelomAI_Data/manifests/fcs_manifest_deidentified.csv
```

**Never share:**

```text
<SSD>/MyelomAI_Data/raw/
<SSD>/MyelomAI_Data/private/
```

If FCS filenames are inconsistent, unresolved files are intentionally withheld from the deidentified output rather
than automatically linked to the wrong specimen. Review `private/fcs_unresolved.csv` and,
when necessary, create `private/fcs_overrides.csv` with:

```text
original_filename,subject_id,specimen_id
```

Then rerun the script.

## Next development modules

The next planned code modules are:

1. dataset/label audit and physician-report label extraction,
2. FCS metadata/panel discovery,
3. compensation-state validation and QC,
4. preprocessing/transformation,
5. self-supervised/event representation learning,
6. hierarchical tube/case modeling,
7. structured evidence and deterministic reporting,
8. calibration/selective prediction,
9. clinical evaluation and GUI.

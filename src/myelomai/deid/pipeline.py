from __future__ import annotations

from pathlib import Path

import pandas as pd

from myelomai.deid.core import (
    build_global_identifier_strings,
    build_subject_alias_map,
    build_subject_registry,
    deidentify_dataframe,
    inspect_fcs_metadata,
    load_or_create_secret,
    load_overrides,
    match_fcs_file,
    panel_hint_from_filename,
    pseudonym,
    scrub_fcs_metadata_lossless,
    sha256_file,
)
from myelomai.paths import DataPaths


def run_deidentification(paths: DataPaths, include_bma: bool = False) -> None:
    flow_path = paths.raw_spreadsheets / "flow.xlsx"
    bma_path = paths.raw_spreadsheets / "BMA.xlsx"
    if not flow_path.exists():
        raise FileNotFoundError(f"Missing {flow_path}")

    flow_df = pd.read_excel(flow_path, dtype=object)
    bma_df = pd.read_excel(bma_path, dtype=object) if bma_path.exists() else None

    # BMA is included in the identity registry when present so cross-modal relationships
    # remain recoverable, but its deidentified table is exported only on explicit request.
    source_dfs = [flow_df] + ([bma_df] if bma_df is not None else [])
    global_ids = build_global_identifier_strings(source_dfs)

    secret_path = paths.private / "deid_secret.key"
    secret = load_or_create_secret(secret_path)

    alias_to_sid = build_subject_alias_map(source_dfs, secret)

    flow_deid, flow_ids, flow_specs = deidentify_dataframe(flow_df, secret, alias_to_sid, global_ids, "flow")
    flow_deid.to_excel(paths.deid_spreadsheets / "flow_deidentified.xlsx", index=False)

    id_tables = [flow_ids]
    spec_tables = [flow_specs]
    if include_bma:
        if bma_df is None:
            raise FileNotFoundError("--include-bma was requested but BMA.xlsx was not found")
        bma_deid, bma_ids, bma_specs = deidentify_dataframe(bma_df, secret, alias_to_sid, global_ids, "BMA")
        bma_deid.to_excel(paths.deid_spreadsheets / "BMA_deidentified.xlsx", index=False)
        id_tables.append(bma_ids)
        spec_tables.append(bma_specs)

    # Private reidentification maps: never share or commit.
    pd.concat(id_tables, ignore_index=True).drop_duplicates().to_csv(
        paths.private / "identity_map.csv", index=False
    )
    pd.concat(spec_tables, ignore_index=True).drop_duplicates().to_csv(
        paths.private / "specimen_map.csv", index=False
    )

    subjects, spec_lookup = build_subject_registry([flow_df], secret, alias_to_sid)
    override_path = paths.private / "fcs_overrides.csv"
    overrides = load_overrides(override_path)

    fcs_files = sorted([p for p in paths.raw_fcs.rglob("*") if p.is_file() and p.suffix.lower() == ".fcs"])
    public_rows = []
    private_rows = []
    unresolved_rows = []

    for src in fcs_files:
        sid, spid, status, reason = match_fcs_file(src, subjects, spec_lookup, overrides)
        raw_sha = sha256_file(src)
        if status != "MATCHED" and status != "OVERRIDE":
            unresolved_rows.append(
                {
                    "original_filename": src.name,
                    "status": status,
                    "reason": reason,
                    "candidate_subject_id": sid or "",
                }
            )
            private_rows.append(
                {
                    "original_filename": src.name,
                    "raw_sha256": raw_sha,
                    "Subject_ID": sid or "",
                    "Specimen_ID": spid or "",
                    "status": status,
                    "reason": reason,
                }
            )
            continue

        assert sid and spid
        fcs_id = pseudonym(secret, "FCS", f"{raw_sha}|{sid}|{spid}")
        out_name = f"{sid}__{spid}__{fcs_id}.fcs"
        dst = paths.deid_fcs / out_name

        # Identifier strings from the matched subject plus global values provide a strict
        # post-write leak check. Primary FCS metadata are otherwise scrubbed by allowlist.
        subject_rec = subjects[sid]
        forbidden = set(global_ids)
        forbidden.update(subject_rec["firsts"])
        forbidden.update(subject_rec["lasts"])
        forbidden.update(subject_rec["mrns"])
        forbidden.update(subject_rec["specs"])

        try:
            before = scrub_fcs_metadata_lossless(src, dst, forbidden)
            deid_sha = sha256_file(dst)
            instrument_raw = f"{before.get('cyt', '')}|{before.get('cytsn', '')}".strip("|")
            instrument_id = pseudonym(secret, "CYT", instrument_raw) if instrument_raw else ""
            public_rows.append(
                {
                    "FCS_ID": fcs_id,
                    "Subject_ID": sid,
                    "Specimen_ID": spid,
                    "deidentified_filename": out_name,
                    "panel_hint_unverified": panel_hint_from_filename(src),
                    "channel_signature": before.get("channel_signature", ""),
                    "event_count": before.get("event_count", ""),
                    "channel_count": before.get("channel_count", ""),
                    "spillover_metadata_present": before.get("spill_present", False),
                    "Instrument_ID": instrument_id,
                    "deidentified_sha256": deid_sha,
                }
            )
            private_rows.append(
                {
                    "original_filename": src.name,
                    "raw_sha256": raw_sha,
                    "FCS_ID": fcs_id,
                    "Subject_ID": sid,
                    "Specimen_ID": spid,
                    "deidentified_filename": out_name,
                    "status": "DEIDENTIFIED",
                    "reason": reason,
                }
            )
        except Exception as exc:
            dst.unlink(missing_ok=True)
            unresolved_rows.append(
                {
                    "original_filename": src.name,
                    "status": "FCS_SCRUB_FAILED",
                    "reason": str(exc),
                    "candidate_subject_id": sid,
                }
            )
            private_rows.append(
                {
                    "original_filename": src.name,
                    "raw_sha256": raw_sha,
                    "FCS_ID": "",
                    "Subject_ID": sid,
                    "Specimen_ID": spid,
                    "status": "FCS_SCRUB_FAILED",
                    "reason": str(exc),
                }
            )

    pd.DataFrame(public_rows).to_csv(paths.manifests / "fcs_manifest_deidentified.csv", index=False)
    pd.DataFrame(private_rows).to_csv(paths.private / "fcs_file_map.csv", index=False)
    pd.DataFrame(unresolved_rows).to_csv(paths.private / "fcs_unresolved.csv", index=False)

    print("Deidentification complete.")
    print(f"  Deidentified flow table: {paths.deid_spreadsheets / 'flow_deidentified.xlsx'}")
    if include_bma:
        print(f"  Deidentified BMA table:  {paths.deid_spreadsheets / 'BMA_deidentified.xlsx'}")
    print(f"  Deidentified FCS files:  {paths.deid_fcs}")
    print(f"  Shareable FCS manifest:  {paths.manifests / 'fcs_manifest_deidentified.csv'}")
    print(f"  PRIVATE identity maps:   {paths.private}")
    if unresolved_rows:
        print(f"  WARNING: {len(unresolved_rows)} FCS file(s) require manual resolution. See private/fcs_unresolved.csv")

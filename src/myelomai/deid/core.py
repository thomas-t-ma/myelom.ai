from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

try:
    import flowio
except ImportError:  # pragma: no cover
    flowio = None


DIRECT_ID_COLUMNS = {"Pt_lastname", "Pt_firstname", "DOB", "MRN", "concat"}
DROP_AFTER_DERIVATION = {"Accession_date", "Spec_num", "Submitting_physician"}
KNOWN_PANEL_HINTS = {"MM", "B", "T", "M1", "M2"}


def _s(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _norm(value) -> str:
    return re.sub(r"[^A-Z0-9]+", "", _s(value).upper())


def _norm_name(value) -> str:
    return re.sub(r"[^A-Z]+", "", _s(value).upper())


def _norm_mrn(value) -> str:
    s = _s(value)
    # Excel frequently turns integer identifiers into '12345.0'.
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return re.sub(r"\D+", "", s)


def _norm_spec(value) -> str:
    s = _s(value)
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return re.sub(r"[^A-Z0-9]+", "", s.upper())


def _parse_date(value) -> pd.Timestamp | None:
    if pd.isna(value) or _s(value) == "":
        return None
    dt = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(dt) else pd.Timestamp(dt)


def _date_variants(value) -> set[str]:
    dt = _parse_date(value)
    if dt is None:
        return set()
    variants = {
        dt.strftime("%m/%d/%Y"),
        dt.strftime("%-m/%-d/%Y") if os.name != "nt" else "",
        dt.strftime("%m-%d-%Y"),
        dt.strftime("%Y-%m-%d"),
        dt.strftime("%m/%d/%y"),
        dt.strftime("%m-%d-%y"),
        dt.strftime("%b %d, %Y"),
        dt.strftime("%B %d, %Y"),
    }
    # Windows-safe unpadded forms.
    variants.add(f"{dt.month}/{dt.day}/{dt.year}")
    variants.add(f"{dt.month}-{dt.day}-{dt.year}")
    return {v for v in variants if v}


def load_or_create_secret(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = path.read_text(encoding="ascii").strip()
        return bytes.fromhex(raw)
    key = secrets.token_bytes(32)
    path.write_text(key.hex(), encoding="ascii")
    return key


def pseudonym(secret: bytes, prefix: str, stable_value: str, n_hex: int = 12) -> str:
    digest = hmac.new(secret, stable_value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{prefix}-{digest[:n_hex].upper()}"


def subject_aliases(row: pd.Series) -> list[str]:
    aliases: list[str] = []
    mrn = _norm_mrn(row.get("MRN", ""))
    if mrn:
        aliases.append(f"MRN:{mrn}")
    last = _norm_name(row.get("Pt_lastname", ""))
    first = _norm_name(row.get("Pt_firstname", ""))
    dob = _parse_date(row.get("DOB", ""))
    if last and first and dob is not None:
        aliases.append(f"NAME_DOB:{last}|{first}|{dob.date().isoformat()}")
    return aliases


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_subject_alias_map(dfs: Iterable[pd.DataFrame], secret: bytes) -> dict[str, str]:
    """Link MRN and name+DOB aliases before assigning a subject pseudonym.

    This means a row missing MRN can still map to the same patient as another row that
    contains the MRN, provided their name+DOB alias agrees.
    """
    uf = _UnionFind()
    for df in dfs:
        for _, row in df.iterrows():
            aliases = subject_aliases(row)
            for alias in aliases:
                uf.find(alias)
            for alias in aliases[1:]:
                uf.union(aliases[0], alias)

    components: dict[str, list[str]] = {}
    for alias in list(uf.parent):
        components.setdefault(uf.find(alias), []).append(alias)

    alias_to_sid: dict[str, str] = {}
    for aliases in components.values():
        mrn_aliases = sorted(a for a in aliases if a.startswith("MRN:"))
        canonical = mrn_aliases[0] if mrn_aliases else sorted(aliases)[0]
        sid = pseudonym(secret, "MYE", canonical)
        for alias in aliases:
            alias_to_sid[alias] = sid
    return alias_to_sid


def resolve_subject_id(row: pd.Series, alias_to_sid: dict[str, str]) -> str | None:
    aliases = subject_aliases(row)
    sids = {alias_to_sid[a] for a in aliases if a in alias_to_sid}
    if len(sids) == 1:
        return next(iter(sids))
    if len(sids) > 1:
        raise ValueError(f"Conflicting patient identifiers in row: aliases resolve to {sorted(sids)}")
    return None


def specimen_identity_key(row: pd.Series, subject_id: str, row_index: int) -> str:
    spec = _norm_spec(row.get("Spec_num", ""))
    if spec:
        return f"{subject_id}|SPEC:{spec}"
    dt = _parse_date(row.get("Accession_date", ""))
    if dt is not None:
        # Rows with different Text_type values for the same subject/date stay together.
        return f"{subject_id}|DATE:{dt.date().isoformat()}"
    return f"{subject_id}|ROW:{row_index}"


def build_global_identifier_strings(dfs: Iterable[pd.DataFrame]) -> set[str]:
    values: set[str] = set()
    for df in dfs:
        for _, row in df.iterrows():
            for col in ("MRN", "Spec_num"):
                val = _s(row.get(col, ""))
                if len(val) >= 4:
                    values.add(val)
                    normalized = _norm_mrn(val) if col == "MRN" else _norm_spec(val)
                    if len(normalized) >= 4:
                        values.add(normalized)
            for col in ("Pt_lastname", "Pt_firstname"):
                val = _s(row.get(col, ""))
                if len(val) >= 4:
                    values.add(val)
            first = _s(row.get("Pt_firstname", ""))
            last = _s(row.get("Pt_lastname", ""))
            if first and last:
                values.update({f"{first} {last}", f"{last}, {first}", f"{last} {first}"})
            values.update(_date_variants(row.get("DOB", "")))
            values.update(_date_variants(row.get("Accession_date", "")))
    return {x for x in values if x}


def _replace_literal_ci(text: str, needle: str, replacement: str) -> str:
    if not needle:
        return text
    return re.sub(re.escape(needle), replacement, text, flags=re.IGNORECASE)


def scrub_report_text(text, row: pd.Series, global_identifiers: set[str]) -> str:
    out = _s(text)
    if not out:
        return out

    row_values: set[str] = set()
    for col in ("Pt_lastname", "Pt_firstname", "MRN", "Spec_num", "Submitting_physician"):
        val = _s(row.get(col, ""))
        if val:
            row_values.add(val)
    first = _s(row.get("Pt_firstname", ""))
    last = _s(row.get("Pt_lastname", ""))
    if first and last:
        row_values.update({f"{first} {last}", f"{last}, {first}", f"{last} {first}"})
    row_values.update(_date_variants(row.get("DOB", "")))
    row_values.update(_date_variants(row.get("Accession_date", "")))

    # Longest first prevents a surname replacement from disrupting a full-name match.
    for value in sorted(row_values, key=len, reverse=True):
        out = _replace_literal_ci(out, value, "[REDACTED]")

    # Defense in depth: remove any other known direct identifier that survived.
    # Skip very short strings to avoid erasing clinical abbreviations/numbers.
    for value in sorted(global_identifiers, key=len, reverse=True):
        if len(value) >= 5 and re.search(re.escape(value), out, flags=re.IGNORECASE):
            out = _replace_literal_ci(out, value, "[REDACTED]")

    return out


def deidentify_dataframe(
    df: pd.DataFrame,
    secret: bytes,
    alias_to_sid: dict[str, str],
    global_identifiers: set[str],
    source_name: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    required = {"Pt_lastname", "Pt_firstname", "DOB", "MRN", "Spec_num", "Accession_date", "Text", "Text_type"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"{source_name} is missing expected columns: {missing}")

    out_rows: list[dict] = []
    identity_rows: list[dict] = []
    specimen_rows: list[dict] = []

    # Compute first accession date per subject for non-identifying longitudinal intervals.
    temp: list[tuple[str, pd.Timestamp | None]] = []
    subject_ids: list[str | None] = []
    for i, row in df.iterrows():
        sid = resolve_subject_id(row, alias_to_sid)
        subject_ids.append(sid)
        temp.append((sid or "", _parse_date(row.get("Accession_date"))))
    first_dates: dict[str, pd.Timestamp] = {}
    for sid, dt in temp:
        if sid and dt is not None and (sid not in first_dates or dt < first_dates[sid]):
            first_dates[sid] = dt

    for position, (idx, row) in enumerate(df.iterrows()):
        aliases = subject_aliases(row)
        subject_id = subject_ids[position]
        if subject_id is None:
            raise ValueError(
                f"Cannot deidentify {source_name} row {idx}: no usable MRN and no complete name+DOB fallback."
            )
        assert subject_id is not None
        spec_key = specimen_identity_key(row, subject_id, position)
        specimen_id = pseudonym(secret, "SPC", spec_key)
        provider = _s(row.get("Submitting_physician", ""))
        provider_id = pseudonym(secret, "PRV", _norm(provider)) if provider else ""

        acc = _parse_date(row.get("Accession_date", ""))
        first_acc = first_dates.get(subject_id)
        accession_year = int(acc.year) if acc is not None else pd.NA
        days_from_first = int((acc - first_acc).days) if acc is not None and first_acc is not None else pd.NA

        safe = {
            "Subject_ID": subject_id,
            "Specimen_ID": specimen_id,
            "Accession_year": accession_year,
            "Days_from_subject_first_accession": days_from_first,
            "Provider_ID": provider_id,
            "Text": scrub_report_text(row.get("Text", ""), row, global_identifiers),
            "Text_type": row.get("Text_type", ""),
            "Source_table": source_name,
        }

        # Preserve any non-identifier columns not explicitly handled. For BMA this allows
        # future non-PHI fields to survive without retaining 'concat'.
        for col in df.columns:
            if col in DIRECT_ID_COLUMNS | DROP_AFTER_DERIVATION | {"Text", "Text_type"}:
                continue
            safe[col] = row[col]
        out_rows.append(safe)

        identity_rows.append(
            {
                "Subject_ID": subject_id,
                "Identity_aliases": " || ".join(aliases),
                "Pt_lastname": _s(row.get("Pt_lastname")),
                "Pt_firstname": _s(row.get("Pt_firstname")),
                "DOB": _s(row.get("DOB")),
                "MRN": _s(row.get("MRN")),
                "Source_table": source_name,
            }
        )
        specimen_rows.append(
            {
                "Subject_ID": subject_id,
                "Specimen_ID": specimen_id,
                "Original_Spec_num": _s(row.get("Spec_num")),
                "Original_Accession_date": _s(row.get("Accession_date")),
                "Source_table": source_name,
            }
        )

    deid = pd.DataFrame(out_rows)
    identities = pd.DataFrame(identity_rows).drop_duplicates()
    specimens = pd.DataFrame(specimen_rows).drop_duplicates()
    return deid, identities, specimens


# --------------------------- FCS metadata handling ---------------------------

@dataclass
class TextToken:
    value: str
    start: int
    end: int  # exclusive, within the TEXT segment byte array


def _read_header_offset(header: bytes, start: int, end: int) -> int:
    raw = header[start:end].decode("ascii", errors="ignore").strip()
    return int(raw) if raw and raw.isdigit() else 0


def _parse_text_tokens(segment: bytes) -> tuple[int, list[TextToken]]:
    if not segment:
        raise ValueError("Empty FCS TEXT segment")
    delim = segment[0]
    tokens: list[TextToken] = []
    i = 1
    start = i
    buf = bytearray()
    while i < len(segment):
        if segment[i] == delim:
            if i + 1 < len(segment) and segment[i + 1] == delim:
                buf.append(delim)
                i += 2
                continue
            tokens.append(TextToken(buf.decode("latin-1", errors="replace"), start, i))
            i += 1
            start = i
            buf = bytearray()
        else:
            buf.append(segment[i])
            i += 1
    if buf:
        tokens.append(TextToken(buf.decode("latin-1", errors="replace"), start, len(segment)))
    return delim, tokens


def _text_dict(tokens: list[TextToken]) -> dict[str, str]:
    result: dict[str, str] = {}
    for i in range(0, len(tokens) - 1, 2):
        result[tokens[i].value.lower().lstrip("$")] = tokens[i + 1].value
    return result


def _is_safe_fcs_key(key: str) -> bool:
    k = key.lower().lstrip("$")
    fixed = {
        "beginanalysis", "endanalysis", "beginstext", "endstext",
        "begindata", "enddata", "byteord", "datatype", "mode",
        "nextdata", "par", "tot", "spill", "spillover", "timestep",
    }
    if k in fixed:
        return True
    # Required/important per-parameter channel metadata (PnN/PnS/etc.).
    return bool(re.fullmatch(r"p\d+(?:b|e|g|n|r|s|v)", k))


def inspect_fcs_metadata(path: Path) -> dict:
    if flowio is None:
        raise RuntimeError("flowio is required for FCS verification. Install the project dependencies.")
    fd = flowio.FlowData(str(path), only_text=True)
    text = dict(fd.text)
    channel_names: list[str] = []
    i = 1
    while True:
        n = text.get(f"p{i}n")
        s = text.get(f"p{i}s")
        if n is None and s is None:
            break
        channel_names.append(str(s or n))
        i += 1
    spill = text.get("spill") or text.get("spillover") or ""
    cyt = text.get("cyt", "")
    cytsn = text.get("cytsn", "")
    return {
        "event_count": getattr(fd, "event_count", None),
        "channel_count": getattr(fd, "channel_count", None),
        "channel_signature": "|".join(channel_names),
        "spill_present": bool(spill),
        "cyt": str(cyt),
        "cytsn": str(cytsn),
        "text": text,
    }


def scrub_fcs_metadata_lossless(src: Path, dst: Path, forbidden_strings: Iterable[str]) -> dict:
    """Create a deidentified FCS copy while keeping the DATA segment byte-for-byte identical.

    We redact nonessential primary TEXT metadata values *in place* using same-length
    replacement bytes. Structural/channel/compensation fields are preserved. Files with
    supplemental TEXT, ANALYSIS, or multiple datasets are rejected rather than guessed.
    """
    raw = bytearray(src.read_bytes())
    if len(raw) < 58:
        raise ValueError("File is too small to be a valid FCS file")
    header = bytes(raw[:58])
    text_begin = _read_header_offset(header, 10, 18)
    text_end = _read_header_offset(header, 18, 26)
    if text_begin <= 0 or text_end < text_begin:
        raise ValueError("Could not resolve the primary FCS TEXT segment from the HEADER")

    segment = bytes(raw[text_begin : text_end + 1])
    _, tokens = _parse_text_tokens(segment)
    meta = _text_dict(tokens)

    for optional_start, optional_end, label in (
        (meta.get("beginstext", "0"), meta.get("endstext", "0"), "supplemental TEXT"),
        (meta.get("beginanalysis", "0"), meta.get("endanalysis", "0"), "ANALYSIS"),
    ):
        try:
            a, b = int(optional_start or 0), int(optional_end or 0)
        except ValueError:
            a, b = 0, 0
        if a > 0 and b >= a:
            raise ValueError(f"{label} segment is present; file is quarantined for manual handling")
    try:
        if int(meta.get("nextdata", "0") or 0) != 0:
            raise ValueError("Multiple FCS datasets detected; file is quarantined for manual handling")
    except ValueError:
        raise ValueError("Invalid $NEXTDATA value")

    before = inspect_fcs_metadata(src)

    patched = bytearray(segment)
    for i in range(0, len(tokens) - 1, 2):
        key_token = tokens[i]
        value_token = tokens[i + 1]
        if _is_safe_fcs_key(key_token.value):
            continue
        # Same byte count, no delimiter characters: DATA offsets remain unchanged.
        patched[value_token.start : value_token.end] = b"X" * (value_token.end - value_token.start)

    raw[text_begin : text_end + 1] = patched
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(raw)

    after = inspect_fcs_metadata(dst)
    if before["event_count"] != after["event_count"] or before["channel_count"] != after["channel_count"]:
        dst.unlink(missing_ok=True)
        raise ValueError("FCS verification failed: event/channel counts changed")
    if before["channel_signature"] != after["channel_signature"]:
        dst.unlink(missing_ok=True)
        raise ValueError("FCS verification failed: channel labels changed")
    if before["spill_present"] != after["spill_present"]:
        dst.unlink(missing_ok=True)
        raise ValueError("FCS verification failed: spillover metadata changed")

    # Compare the event DATA segment bytes exactly.
    data_begin = _read_header_offset(header, 26, 34)
    data_end = _read_header_offset(header, 34, 42)
    if data_begin == 0:
        try:
            data_begin = int(meta.get("begindata", "0"))
            data_end = int(meta.get("enddata", "0"))
        except ValueError:
            data_begin = data_end = 0
    if data_begin > 0 and data_end >= data_begin:
        src_bytes = src.read_bytes()[data_begin : data_end + 1]
        dst_bytes = dst.read_bytes()[data_begin : data_end + 1]
        if src_bytes != dst_bytes:
            dst.unlink(missing_ok=True)
            raise ValueError("FCS verification failed: event DATA bytes changed")

    # Final metadata scan for known identifiers.
    haystack = "\n".join(
        f"{k}={v}" for k, v in after["text"].items() if not _is_safe_fcs_key(str(k))
    )
    survivors = []
    for s in forbidden_strings:
        s = _s(s)
        if len(s) >= 4 and re.search(re.escape(s), haystack, flags=re.IGNORECASE):
            survivors.append(s)
    if survivors:
        dst.unlink(missing_ok=True)
        raise ValueError(f"Known identifiers survived FCS scrub: {survivors[:5]}")

    return before


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def filename_tokens(path: Path) -> list[str]:
    return [t for t in re.split(r"[^A-Za-z0-9]+", path.stem.upper()) if t]


def panel_hint_from_filename(path: Path) -> str:
    for tok in filename_tokens(path):
        if tok in KNOWN_PANEL_HINTS:
            return tok
    return ""


def _candidate_score(filename: Path, subject: dict) -> tuple[int, list[str]]:
    tokens = filename_tokens(filename)
    token_set = set(tokens)
    compact = "".join(tokens)
    score = 0
    reasons: list[str] = []

    for mrn in subject.get("mrns", set()):
        n = _norm_mrn(mrn)
        if len(n) >= 4 and n in compact:
            score = max(score, 120)
            reasons.append("MRN")

    spec_match = False
    for spec in subject.get("specs", set()):
        n = _norm_spec(spec)
        if len(n) >= 2 and (n in token_set or (len(n) >= 5 and n in compact)):
            spec_match = True
            score = max(score, 90)
            reasons.append("SPEC")
            break

    firsts = {_norm_name(x) for x in subject.get("firsts", set()) if _norm_name(x)}
    lasts = {_norm_name(x) for x in subject.get("lasts", set()) if _norm_name(x)}
    first_hit = any(x in token_set or x in compact for x in firsts if len(x) >= 2)
    last_hit = any(x in token_set or x in compact for x in lasts if len(x) >= 2)
    if first_hit and last_hit:
        score = max(score, 100)
        reasons.append("FIRST+LAST")
    elif last_hit:
        score = max(score, 45)
        reasons.append("LAST")
    elif first_hit:
        score = max(score, 20)
        reasons.append("FIRST")

    if spec_match and first_hit and last_hit:
        score += 25
    return score, reasons


def build_subject_registry(source_dfs: Iterable[pd.DataFrame], secret: bytes, alias_to_sid: dict[str, str]) -> tuple[dict, dict]:
    subjects: dict[str, dict] = {}
    spec_lookup: dict[tuple[str, str], str] = {}
    for df in source_dfs:
        for pos, (_, row) in enumerate(df.iterrows()):
            sid = resolve_subject_id(row, alias_to_sid)
            if sid is None:
                continue
            rec = subjects.setdefault(sid, {"firsts": set(), "lasts": set(), "mrns": set(), "specs": set()})
            for field, dest in (("Pt_firstname", "firsts"), ("Pt_lastname", "lasts"), ("MRN", "mrns"), ("Spec_num", "specs")):
                val = _s(row.get(field, ""))
                if val:
                    rec[dest].add(val)
            spec = _norm_spec(row.get("Spec_num", ""))
            if spec:
                spid = pseudonym(secret, "SPC", f"{sid}|SPEC:{spec}")
                spec_lookup[(sid, spec)] = spid
    return subjects, spec_lookup


def load_overrides(path: Path) -> dict[str, tuple[str, str]]:
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype=str).fillna("")
    needed = {"original_filename", "subject_id", "specimen_id"}
    if not needed.issubset(df.columns):
        raise ValueError(f"Override file must contain columns: {sorted(needed)}")
    return {
        row["original_filename"]: (row["subject_id"], row["specimen_id"])
        for _, row in df.iterrows()
    }


def match_fcs_file(
    path: Path,
    subjects: dict,
    spec_lookup: dict[tuple[str, str], str],
    overrides: dict[str, tuple[str, str]],
) -> tuple[str | None, str | None, str, str]:
    if path.name in overrides:
        sid, spid = overrides[path.name]
        return sid, spid, "OVERRIDE", "manual override"

    scored = []
    for sid, rec in subjects.items():
        score, reasons = _candidate_score(path, rec)
        if score:
            scored.append((score, sid, reasons))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 80:
        return None, None, "UNMATCHED", "no candidate reached confidence threshold"
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 20:
        return None, None, "AMBIGUOUS_SUBJECT", f"top scores {scored[0][0]} vs {scored[1][0]}"

    score, sid, reasons = scored[0]
    tokens = set(filename_tokens(path))
    possible_specs = []
    for (candidate_sid, spec), spid in spec_lookup.items():
        if candidate_sid != sid:
            continue
        if spec in tokens or (len(spec) >= 5 and spec in "".join(tokens)):
            possible_specs.append(spid)
    possible_specs = sorted(set(possible_specs))
    if len(possible_specs) == 1:
        return sid, possible_specs[0], "MATCHED", f"score={score}; {','.join(reasons)}; specimen token"

    all_subject_specs = sorted({spid for (candidate_sid, _), spid in spec_lookup.items() if candidate_sid == sid})
    if len(all_subject_specs) == 1:
        return sid, all_subject_specs[0], "MATCHED", f"score={score}; {','.join(reasons)}; only known specimen"
    return sid, None, "AMBIGUOUS_SPECIMEN", f"subject matched (score={score}) but specimen unresolved"


def copy_to_quarantine(src: Path, quarantine_dir: Path) -> Path:
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    dst = quarantine_dir / src.name
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return dst

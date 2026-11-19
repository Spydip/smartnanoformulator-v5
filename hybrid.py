"""Hybrid AI/ML services for SmartNanoFormulator v3.

The 928-run metrics shown in the UI are manuscript-reported context until the
corresponding source rows and model artefacts are imported and independently
reproduced. This module never fabricates rows or silently activates a model.
"""
from __future__ import annotations

import csv
import base64
import hashlib
import io
import json
import math
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import llm_explainer

APP_VERSION = "3.2"
MAX_IMPORT_ROWS = 5000
MAX_CSV_CHARS = 6_000_000

# Canonical model inputs are drawn only from microfluidic formulation/process columns.
FEATURES = [
    "esm_concentration", "hspc_concentration", "chol_concentration", "peg_concentration",
    "total_flow_rate", "flow_rate_ratio", "chip_type", "aqueous_medium",
]
TARGETS = {"size_nm": "Particle size (nm)", "pdi": "Polydispersity index"}

ALIASES = {
    "run_id": ["run_id", "run", "experiment_id", "experiment", "formulation_id", "batch_id", "sample_id", "id"],
    "study_id": ["study_id", "study", "paper_id", "publication_id", "doi", "source_study", "study_identifier"],
    "lab_id": ["lab_id", "lab", "laboratory", "site", "equipment_site"],
    "operator_id": ["operator_id", "operator", "experimenter"],
    "chip_type": ["chip_type", "chip", "chip configuration", "microfluidic_chip"],
    "aqueous_medium": ["aqueous_medium", "aqueous", "buffer", "aqueous_phase", "medium"],
    "esm_concentration": ["esm_concentration", "esm", "egg_sphingomyelin", "esm_mg_ml", "esm_mm", "esm_concentration_mg_ml", "esm_concentration_mm"],
    "hspc_concentration": ["hspc_concentration", "hspc", "hspc_mg_ml", "hspc_mm", "hspc_concentration_mg_ml", "hspc_concentration_mm"],
    "chol_concentration": ["chol_concentration", "chol", "cholesterol", "chol_mg_ml", "chol_mm", "cholesterol_mg_ml", "cholesterol_mm"],
    "peg_concentration": ["peg_concentration", "peg", "dspe_peg", "peg_mg_ml", "peg_mm", "dspe_peg_mg_ml", "dspe_peg_mm", "peg_lipid_mg_ml", "peg_lipid_mm"],
    "total_flow_rate": ["total_flow_rate", "tfr", "tfr_ml_min", "tfr_ml_h", "total_flow_rate_ml_min", "total_flow_rate_ml_h", "flow_rate_total_ml_min", "flow_rate_total_ml_h"],
    "flow_rate_ratio": ["flow_rate_ratio", "frr", "flow_rate_ratio_frr", "aqueous_to_organic_ratio"],
    "size_nm": ["size_nm", "size", "particle_size", "particle_size_nm", "diameter_nm", "z_average_nm"],
    "pdi": ["pdi", "polydispersity_index", "polydispersity"],
    "formation_status": ["formation_status", "formation", "liposome_formation", "formed", "formation_indicator"],
    "is_synthetic": ["is_synthetic", "synthetic", "augmented", "is_augmented", "data_origin"],
    "is_holdout": ["is_holdout", "holdout", "independent_validation", "validation_only",
                   "external_validation", "validation_set", "wet_lab_validation", "split", "data_split", "dataset_split"],
}

STOP_WORDS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "what", "how", "does", "are", "was",
    "were", "can", "could", "should", "would", "about", "please", "based", "show", "using", "used", "have",
    "has", "had", "its", "their", "than", "then", "when", "where", "which", "while", "into", "onto",
    "about", "describe", "explain", "compare", "nanovesicle", "formulation", "formulations", "study", "studies",
}


def _table_exists(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_header(value: str) -> str:
    value = str(value or "").strip().lower().replace("µ", "u").replace("μ", "u")
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"\[[^]]*\]", "", value)
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def _declared_units(headers: list[str]) -> tuple[set[str], set[str]]:
    """Read explicit units from headers without guessing units from bare ESM/TFR labels."""
    concentration_units: set[str] = set()
    flow_units: set[str] = set()
    for header in headers:
        h = str(header or "").lower().replace("μ", "u").replace("µ", "u")
        if re.search(r"mg\s*/\s*ml|mg[_ ]ml", h):
            concentration_units.add("mg/mL")
        if re.search(r"(?<![a-z])mm(?![a-z])|_mm(?:_|$)", h):
            concentration_units.add("mM")
        if re.search(r"ml\s*/\s*min|ml[_ ]min", h):
            flow_units.add("mL/min")
        if re.search(r"ml\s*/\s*h(?:r|our)?|ml[_ ]h(?:r|our)?(?:_|$)", h):
            flow_units.add("mL/h")
    return concentration_units, flow_units


def ensure_tables(con) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS microfluidic_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_version TEXT NOT NULL,
        run_id TEXT,
        study_id TEXT,
        lab_id TEXT,
        operator_id TEXT,
        chip_type TEXT,
        aqueous_medium TEXT,
        esm_mM REAL,
        hspc_mM REAL,
        chol_mM REAL,
        peg_mM REAL,
        total_flow_rate_ml_h REAL,
        esm_concentration REAL,
        hspc_concentration REAL,
        chol_concentration REAL,
        peg_concentration REAL,
        concentration_unit TEXT,
        total_flow_rate REAL,
        flow_rate_unit TEXT,
        flow_rate_ratio REAL,
        size_nm REAL,
        pdi REAL,
        formation_status TEXT,
        is_synthetic INTEGER NOT NULL DEFAULT 0,
        is_holdout INTEGER NOT NULL DEFAULT 0,
        raw_json TEXT NOT NULL,
        row_fingerprint TEXT NOT NULL UNIQUE,
        imported_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_microfluidic_study ON microfluidic_runs(study_id);
    CREATE INDEX IF NOT EXISTS idx_microfluidic_dataset ON microfluidic_runs(dataset_version);
    CREATE TABLE IF NOT EXISTS microfluidic_imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_version TEXT NOT NULL,
        filename TEXT NOT NULL,
        rows_seen INTEGER NOT NULL,
        rows_inserted INTEGER NOT NULL,
        duplicates INTEGER NOT NULL,
        rows_with_size INTEGER NOT NULL,
        rows_with_pdi INTEGER NOT NULL,
        study_groups INTEGER NOT NULL,
        imported_at TEXT NOT NULL,
        notes TEXT
    );
    """)
    # Migrate existing v2/v3 databases safely without requiring a rebuild.
    columns = {str(r[1]) for r in con.execute("PRAGMA table_info(microfluidic_runs)").fetchall()}
    if "is_holdout" not in columns:
        con.execute("ALTER TABLE microfluidic_runs ADD COLUMN is_holdout INTEGER NOT NULL DEFAULT 0")
    migrations = {
        "esm_concentration": "REAL", "hspc_concentration": "REAL", "chol_concentration": "REAL",
        "peg_concentration": "REAL", "concentration_unit": "TEXT", "total_flow_rate": "REAL",
        "flow_rate_unit": "TEXT",
    }
    for column, definition in migrations.items():
        if column not in columns:
            con.execute(f"ALTER TABLE microfluidic_runs ADD COLUMN {column} {definition}")
    # Backfill rows imported by v3.1 using its explicitly labelled legacy units.
    # Do not relabel them as mg/mL or mL/min: the old schema called them mM and mL/h.
    con.execute("""UPDATE microfluidic_runs SET
        esm_concentration=COALESCE(esm_concentration, esm_mM),
        hspc_concentration=COALESCE(hspc_concentration, hspc_mM),
        chol_concentration=COALESCE(chol_concentration, chol_mM),
        peg_concentration=COALESCE(peg_concentration, peg_mM),
        total_flow_rate=COALESCE(total_flow_rate, total_flow_rate_ml_h),
        concentration_unit=CASE WHEN concentration_unit IS NULL AND
            (esm_mM IS NOT NULL OR hspc_mM IS NOT NULL OR chol_mM IS NOT NULL OR peg_mM IS NOT NULL)
            THEN 'mM' ELSE concentration_unit END,
        flow_rate_unit=CASE WHEN flow_rate_unit IS NULL AND total_flow_rate_ml_h IS NOT NULL
            THEN 'mL/h' ELSE flow_rate_unit END
    WHERE esm_concentration IS NULL OR hspc_concentration IS NULL OR chol_concentration IS NULL OR
          peg_concentration IS NULL OR total_flow_rate IS NULL OR concentration_unit IS NULL OR flow_rate_unit IS NULL""")
    con.commit()


def parse_and_import_xlsx(con, filename: str, file_base64: str, mark_as_holdout: bool = False,
                          source_group: str = "", source_group_type: str = "lab_id",
                          concentration_unit: str = "mg/mL", flow_rate_unit: str = "mL/min") -> dict:
    """Import the active worksheet from an XLSX/XLSM workbook via base64 payload."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError("Excel import requires openpyxl. Install the v3 requirements first.") from exc
    if not file_base64 or len(file_base64) > 7_200_000:
        raise ValueError("Excel workbook is empty or larger than the 5 MB upload limit.")
    try:
        raw_bytes = base64.b64decode(file_base64, validate=True)
        if len(raw_bytes) > 5 * 1024 * 1024:
            raise ValueError("Excel workbook is larger than the 5 MB upload limit.")
        workbook = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        worksheet = workbook.active
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        row_count = 0
        for row in worksheet.iter_rows(values_only=True):
            row_count += 1
            if row_count > MAX_IMPORT_ROWS + 1:
                raise ValueError(f"Worksheet exceeds the {MAX_IMPORT_ROWS:,}-row safety limit.")
            values = []
            for value in row:
                if value is None:
                    values.append("")
                elif hasattr(value, "isoformat"):
                    values.append(value.isoformat())
                else:
                    values.append(str(value))
            writer.writerow(values)
        workbook.close()
        return parse_and_import_csv(con, filename, buffer.getvalue(), mark_as_holdout=mark_as_holdout,
                                     source_group=source_group, source_group_type=source_group_type,
                                     concentration_unit=concentration_unit, flow_rate_unit=flow_rate_unit)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Excel workbook could not be read: {exc}") from exc


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    raw = str(value).strip()
    if raw.lower() in {"", "na", "n/a", "nan", "none", "null", "-", "not reported"}:
        return None
    raw = raw.replace(" ", "")
    # Keep the importer conservative: ratio strings such as 5:1 must be normalized
    # by the researcher with an explicit convention instead of being guessed.
    try:
        result = float(raw)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _truthy(value: Any) -> bool:
    v = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    return v in {"1", "1.0", "true", "yes", "y", "validation", "val", "test", "holdout",
                 "external", "external validation", "independent validation", "wet lab validation"} or \
        "holdout" in v or "validation" in v


def _is_synthetic(value: Any) -> bool:
    v = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    return v in {"1", "1.0", "true", "yes", "y", "synthetic", "augmented", "smote", "gaussian noise", "noise"} or \
        any(term in v for term in ("synthetic", "augmented", "smote", "gaussian noise"))


def parse_and_import_csv(con, filename: str, csv_text: str, mark_as_holdout: bool = False,
                         source_group: str = "", source_group_type: str = "lab_id",
                         concentration_unit: str = "mg/mL", flow_rate_unit: str = "mL/min") -> dict:
    ensure_tables(con)
    if not csv_text or len(csv_text) > MAX_CSV_CHARS:
        raise ValueError(f"CSV content must be non-empty and no larger than {MAX_CSV_CHARS // 1_000_000} MB.")
    try:
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        original_headers = reader.fieldnames or []
        if len(original_headers) < 2:
            raise ValueError("CSV needs a header row and at least two columns.")
        normalized_headers = {normalize_header(h): h for h in original_headers if h is not None}
        concentration_unit = str(concentration_unit or "mg/mL").strip()
        flow_rate_unit = str(flow_rate_unit or "mL/min").strip()
        if concentration_unit not in {"mg/mL", "mM"}:
            raise ValueError("Lipid concentration unit must be mg/mL or mM.")
        if flow_rate_unit not in {"mL/min", "mL/h"}:
            raise ValueError("Total flow-rate unit must be mL/min or mL/h.")
        declared_conc, declared_flow = _declared_units(original_headers)
        if len(declared_conc) > 1 or len(declared_flow) > 1:
            raise ValueError("The file contains mixed explicit units in its headers. Standardize the units before importing.")
        if declared_conc and concentration_unit not in declared_conc:
            raise ValueError(f"Column headers specify {', '.join(sorted(declared_conc))} lipid concentrations, but the selected unit is {concentration_unit}. Select the matching unit.")
        if declared_flow and flow_rate_unit not in declared_flow:
            raise ValueError(f"Column headers specify {', '.join(sorted(declared_flow))} for total flow rate, but the selected unit is {flow_rate_unit}. Select the matching unit.")
        mapped: dict[str, str] = {}
        for canonical, aliases in ALIASES.items():
            for alias in aliases:
                key = normalize_header(alias)
                if key in normalized_headers:
                    mapped[canonical] = normalized_headers[key]
                    break
        if not any(k in mapped for k in ("size_nm", "pdi", "formation_status")):
            raise ValueError("No recognized target column found. Include at least Size (nm), PDI, or Formation status.")
        if not any(k in mapped for k in FEATURES):
            raise ValueError("No recognized formulation/process feature found. Expected fields such as ESM, HSPC, CHOL, PEG, TFR, FRR, chip or aqueous medium.")

        dataset_version = "microfluidic_" + hashlib.sha256((filename + utc_now()).encode()).hexdigest()[:12]
        imported_at = utc_now()
        rows_seen = rows_inserted = duplicates = with_size = with_pdi = 0
        rows_reclassified_to_holdout = 0
        group_rows: list[dict] = []
        nonnumeric_values: dict[str, int] = {}
        source_group = str(source_group or "").strip()[:120]
        source_group_type = str(source_group_type or "lab_id").strip().lower()
        if source_group_type not in {"lab_id", "operator_id"}:
            source_group_type = "lab_id"
        for row_no, row in enumerate(reader, start=1):
            if row_no > MAX_IMPORT_ROWS:
                raise ValueError(f"Import exceeds the {MAX_IMPORT_ROWS:,}-row safety limit. Split the CSV into smaller files.")
            if not row or not any(str(v or "").strip() for v in row.values()):
                continue
            rows_seen += 1
            raw = {str(k): (str(v).strip() if v is not None else "") for k, v in row.items() if k is not None}
            canonical: dict[str, Any] = {}
            for key, src_col in mapped.items():
                value = row.get(src_col)
                if key in {"esm_concentration", "hspc_concentration", "chol_concentration", "peg_concentration", "total_flow_rate", "flow_rate_ratio", "size_nm", "pdi"}:
                    parsed_value = _parse_float(value)
                    canonical[key] = parsed_value
                    if str(value or "").strip() and parsed_value is None:
                        nonnumeric_values[key] = nonnumeric_values.get(key, 0) + 1
                elif key == "is_synthetic":
                    canonical[key] = 1 if _is_synthetic(value) else 0
                elif key == "is_holdout":
                    canonical[key] = 1 if _truthy(value) else 0
                else:
                    canonical[key] = str(value or "").strip()[:240] or None
            canonical.setdefault("run_id", f"{dataset_version}-row-{row_no:04d}")
            canonical.setdefault("study_id", None)
            canonical.setdefault("lab_id", None)
            canonical.setdefault("operator_id", None)
            # When a source workbook has no per-row lab/operator field, let the
            # researcher assign the known provenance of this whole upload.
            # This must only be used for genuinely single-source files.
            if source_group and not canonical.get(source_group_type):
                canonical[source_group_type] = source_group
            canonical.setdefault("chip_type", None)
            canonical.setdefault("aqueous_medium", None)
            canonical.setdefault("esm_concentration", None)
            canonical.setdefault("hspc_concentration", None)
            canonical.setdefault("chol_concentration", None)
            canonical.setdefault("peg_concentration", None)
            canonical.setdefault("total_flow_rate", None)
            canonical.setdefault("concentration_unit", concentration_unit)
            canonical.setdefault("flow_rate_unit", flow_rate_unit)
            canonical.setdefault("flow_rate_ratio", None)
            canonical.setdefault("size_nm", None)
            canonical.setdefault("pdi", None)
            canonical.setdefault("formation_status", None)
            canonical.setdefault("is_synthetic", 0)
            canonical.setdefault("is_holdout", 0)
            if mark_as_holdout:
                canonical["is_holdout"] = 1
            # Reject impossible numeric values early rather than letting them silently train a model.
            for col in ("esm_concentration", "hspc_concentration", "chol_concentration", "peg_concentration", "total_flow_rate", "flow_rate_ratio"):
                value = canonical.get(col)
                if value is not None and value < 0:
                    raise ValueError(f"Row {row_no}: {col} cannot be negative. Check the source units and values.")
            if canonical.get("total_flow_rate") == 0:
                raise ValueError(f"Row {row_no}: total flow rate must be greater than zero.")
            if canonical.get("flow_rate_ratio") == 0:
                raise ValueError(f"Row {row_no}: flow-rate ratio must be greater than zero.")
            if canonical.get("size_nm") is not None and canonical["size_nm"] <= 0:
                raise ValueError(f"Row {row_no}: particle size must be greater than zero nm.")
            if canonical.get("pdi") is not None and not 0 <= canonical["pdi"] <= 1:
                raise ValueError(f"Row {row_no}: PDI must be between 0 and 1. Convert percentages explicitly before import.")
            # Grouping uses a supplied DOI first, then lab/operator. Never infer a
            # group from row IDs or silently assign multiple origins to one file.
            group_rows.append({k: canonical.get(k) for k in ("study_id", "lab_id", "operator_id")})
            if canonical.get("size_nm") is not None:
                with_size += 1
            if canonical.get("pdi") is not None:
                with_pdi += 1
            packed = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            # Fingerprint provenance supplied at upload level and the selected units
            # as well as raw row cells. Two labs can legitimately reuse local run IDs
            # and have otherwise identical measurements; they must not be collapsed.
            fingerprint_payload = json.dumps({
                "raw": raw,
                "upload_group_type": source_group_type if source_group else None,
                "upload_group": source_group or None,
                "concentration_unit": concentration_unit,
                "flow_rate_unit": flow_rate_unit,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
            fields = [
                "dataset_version", "run_id", "study_id", "lab_id", "operator_id", "chip_type", "aqueous_medium",
                "esm_concentration", "hspc_concentration", "chol_concentration", "peg_concentration", "concentration_unit",
                "total_flow_rate", "flow_rate_unit", "flow_rate_ratio", "size_nm", "pdi",
                "formation_status", "is_synthetic", "is_holdout", "raw_json", "row_fingerprint", "imported_at",
            ]
            vals = [dataset_version] + [canonical[k] for k in fields[1:20]] + [packed, fingerprint, imported_at]
            try:
                con.execute(
                    f"INSERT INTO microfluidic_runs ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", vals
                )
                rows_inserted += 1
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    duplicates += 1
                    # A deliberate holdout import must reclassify identical previously
                    # imported training rows so they cannot leak into model fitting.
                    if canonical.get("is_holdout"):
                        cur = con.execute("UPDATE microfluidic_runs SET is_holdout=1 WHERE row_fingerprint=? AND is_holdout=0", (fingerprint,))
                        rows_reclassified_to_holdout += max(0, int(cur.rowcount or 0))
                else:
                    raise
        if rows_seen == 0:
            raise ValueError("CSV contains no data rows.")
        notes = []
        # Import summaries describe which provenance field is present in this upload,
        # even when the file represents only one lab. The training readiness check
        # separately requires >=2 groups across the combined training dataset.
        grouping_column, group_count = _choose_group_column(group_rows)
        if not grouping_column:
            for candidate_column in ("lab_id", "operator_id", "study_id"):
                observed = {str(r.get(candidate_column)).strip() for r in group_rows
                            if r.get(candidate_column) is not None and str(r.get(candidate_column)).strip()}
                if observed:
                    grouping_column, group_count = candidate_column, len(observed)
                    break
        if not grouping_column:
            notes.append("No study_id/DOI, lab_id or operator_id grouping values were detected; grouped cross-validation will not be available until source provenance is supplied.")
        notes.append(f"Imported input units: lipid concentrations {concentration_unit}; total flow rate {flow_rate_unit}. These units are stored and checked before datasets are pooled.")
        if source_group:
            notes.append(f"Rows without a row-level {source_group_type} value were assigned source group '{source_group}'. Use this only when the upload contains one genuine laboratory/operator source.")
        if mark_as_holdout:
            notes.append("This upload was explicitly marked as independent holdout data and will never be used to fit model parameters.")
        for column, count in sorted(nonnumeric_values.items()):
            notes.append(f"{count} non-empty value(s) in {column} were not numeric and were stored as missing. Normalize units/uncertainty text explicitly before training.")
        if not mapped.get("run_id"):
            notes.append("No run_id column was detected; stable import row IDs were assigned for traceability.")
        if any(mapped.get(k) is None for k in ("esm_concentration", "hspc_concentration", "chol_concentration", "peg_concentration", "total_flow_rate", "flow_rate_ratio")):
            notes.append("Some canonical process inputs were not mapped. Review column names and export a normalized CSV before model training.")
        con.execute("""INSERT INTO microfluidic_imports
            (dataset_version,filename,rows_seen,rows_inserted,duplicates,rows_with_size,rows_with_pdi,study_groups,imported_at,notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (dataset_version, Path(filename or "microfluidic_upload.csv").name[:180], rows_seen, rows_inserted, duplicates,
             with_size, with_pdi, group_count, imported_at, " ".join(notes) or None))
        con.commit()
        return {
            "dataset_version": dataset_version, "filename": Path(filename or "microfluidic_upload.csv").name,
            "rows_seen": rows_seen, "rows_inserted": rows_inserted, "duplicates": duplicates,
            "rows_reclassified_to_holdout": rows_reclassified_to_holdout,
            "rows_with_size": with_size, "rows_with_pdi": with_pdi, "study_groups": group_count,
            "grouping_column": grouping_column, "mapped_columns": mapped, "notes": notes, "imported_at": imported_at,
        }
    except csv.Error as exc:
        con.rollback()
        raise ValueError(f"CSV could not be parsed: {exc}") from exc
    except Exception:
        con.rollback()
        raise


def model_cards_path(root: str | Path) -> Path:
    return Path(root) / "ml_models" / "artifacts" / "model_cards.json"


def get_model_cards(root: str | Path) -> dict:
    path = model_cards_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _choose_group_column(rows: list[dict]) -> tuple[str | None, int]:
    """Choose the strongest available independent grouping key, never a row identifier."""
    for column in ("study_id", "lab_id", "operator_id"):
        groups = {str(r.get(column)).strip() for r in rows if r.get(column) is not None and str(r.get(column)).strip()}
        if len(groups) >= 2:
            return column, len(groups)
    return None, 0


def invalidate_models(root: str | Path) -> None:
    """Remove stale model cards/artifacts after any new source rows are imported."""
    artifact_dir = Path(root) / "ml_models" / "artifacts"
    if artifact_dir.exists():
        for path in artifact_dir.glob("*.joblib"):
            try:
                path.unlink()
            except OSError:
                pass
    path = model_cards_path(root)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def status(con, root: str | Path) -> dict:
    ensure_tables(con)
    all_rows = [dict(r) for r in con.execute("SELECT * FROM microfluidic_runs").fetchall()]
    training_rows = [r for r in all_rows if not int(r.get("is_synthetic") or 0) and not int(r.get("is_holdout") or 0)]
    holdout_rows = [r for r in all_rows if int(r.get("is_holdout") or 0) and not int(r.get("is_synthetic") or 0)]
    grouping_column, groups = _choose_group_column(training_rows)
    unit_pairs = {(str(r.get("concentration_unit") or "").strip(), str(r.get("flow_rate_unit") or "").strip())
                  for r in training_rows}
    # Missing unit labels must not disappear from the set: that would make the
    # dashboard say "ready" even though train_models correctly refuses the rows.
    unit_conflict = len(unit_pairs) > 1 or any(not a or not b for a, b in unit_pairs)
    input_units = None
    if len(unit_pairs) == 1 and not unit_conflict:
        cu, fu = next(iter(unit_pairs))
        input_units = {"concentration": cu, "flow_rate": fu}
    size_n = sum(r.get("size_nm") is not None for r in training_rows)
    pdi_n = sum(r.get("pdi") is not None for r in training_rows)
    synthetic_n = sum(int(r.get("is_synthetic") or 0) for r in all_rows)
    imports = [dict(r) for r in con.execute("SELECT * FROM microfluidic_imports ORDER BY id DESC LIMIT 10").fetchall()]
    cards = get_model_cards(root)
    latest = imports[0] if imports else None
    training_n = len(training_rows)
    return {
        "app_version": APP_VERSION,
        "dataset": {"rows": len(all_rows), "original_rows": sum(not int(r.get("is_synthetic") or 0) for r in all_rows),
                    "training_rows": training_n, "holdout_rows": len(holdout_rows), "study_groups": groups,
                    "grouping_column": grouping_column, "size_labelled": size_n, "pdi_labelled": pdi_n,
                    "synthetic_rows": synthetic_n, "latest_import": latest, "imports": imports,
                    "input_units": input_units, "unit_conflict": unit_conflict,
                    "training_ready": bool(training_n >= 50 and groups >= 2 and (size_n >= 30 or pdi_n >= 30) and input_units is not None and not unit_conflict)},
        "models": cards,
        "llm": llm_explainer.settings(),
        "reported_benchmark": {"rows": 928, "particle_size_mae_nm": 17.42, "particle_size_baseline_mae_nm": 46.35,
                               "pdi_mae": 0.0376, "pdi_baseline_mae": 0.0518,
                               "status": "reported in project abstract; not yet reproduced from imported raw rows"},
        "related_public_dataset": {"rows": 304, "independent_validation_rows": 12,
                                   "doi": "10.5281/zenodo.17867478", "status": "separate public dataset; not the project 928-run dataset"},
    }


def _to_dataframe(rows):
    try:
        import pandas as pd
    except ImportError as exc:
        raise ValueError("Training requires pandas. Install the v3 requirements first.") from exc
    return pd.DataFrame(rows)


def train_models(con, root: str | Path) -> dict:
    ensure_tables(con)
    try:
        import joblib
        import numpy as np
        from sklearn.compose import ColumnTransformer
        from sklearn.dummy import DummyRegressor
        from sklearn.ensemble import ExtraTreesRegressor
        from sklearn.impute import SimpleImputer
        from sklearn.metrics import mean_absolute_error
        from sklearn.model_selection import GroupKFold
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler
    except ImportError as exc:
        raise ValueError("ML training dependencies are not installed. Run: python -m pip install -r requirements.txt") from exc

    sql = "SELECT * FROM microfluidic_runs WHERE is_synthetic=0 AND is_holdout=0 ORDER BY id"
    rows = [dict(r) for r in con.execute(sql).fetchall()]
    if len(rows) < 50:
        raise ValueError(f"Only {len(rows)} rows are available. At least 50 original rows (non-synthetic and non-holdout) are required before exploratory training.")
    unit_pairs = {(str(r.get("concentration_unit") or "").strip(), str(r.get("flow_rate_unit") or "").strip()) for r in rows}
    if len(unit_pairs) != 1 or any(not a or not b for a, b in unit_pairs):
        raise ValueError("Training rows have missing or mixed input units. Keep lipid concentrations and total flow-rate units consistent across all training imports; re-import normalized data before training.")
    concentration_unit, flow_rate_unit = next(iter(unit_pairs))
    input_units = {"concentration": concentration_unit, "flow_rate": flow_rate_unit}
    group_column, n_groups = _choose_group_column(rows)
    if not group_column or n_groups < 2:
        raise ValueError("Grouped validation needs at least two independent study_id, lab_id or operator_id groups. Do not use run_id as a substitute.")

    df = _to_dataframe(rows)
    # Model scope is restricted to process/formulation variables. Study/run identifiers
    # and measurements are never used as features.
    numeric_features = [c for c in FEATURES if c in df.columns and c not in {"chip_type", "aqueous_medium"} and df[c].notna().any()]
    categorical_features = [c for c in ("chip_type", "aqueous_medium") if c in df.columns and df[c].notna().any()]
    features = numeric_features + categorical_features
    if len(features) < 2:
        raise ValueError("At least two mapped formulation/process input columns with non-empty values are required for training.")

    artifact_dir = Path(root) / "ml_models" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    n_splits = min(5, n_groups)
    reports: dict[str, Any] = {}
    for target, label in TARGETS.items():
        if target not in df.columns:
            continue
        mask = df[target].notna() & df[group_column].notna() & (df[group_column].astype(str).str.strip() != "")
        subset = df.loc[mask].copy()
        n_target_groups = subset[group_column].nunique()
        if len(subset) < 30 or n_target_groups < 2:
            reports[target] = {"status": "withheld", "reason": f"Insufficient labelled rows or independent groups ({len(subset)} rows, {n_target_groups} groups using {group_column})."}
            continue
        target_features = [c for c in features if subset[c].notna().any()]
        if len(target_features) < 2:
            reports[target] = {"status": "withheld", "reason": f"Only {len(target_features)} usable input feature(s) remain among rows labelled for {target}; at least two are required."}
            continue
        X = subset[target_features]
        y = subset[target].astype(float).to_numpy()
        g = subset[group_column].astype(str).to_numpy()
        n_target_splits = min(n_splits, len(set(g)))
        target_cv = GroupKFold(n_splits=n_target_splits)
        numeric = [c for c in numeric_features if c in X.columns and subset[c].notna().any()]
        categorical = [c for c in categorical_features if c in X.columns and subset[c].notna().any()]
        transformers = []
        if numeric:
            transformers.append(("numeric", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric))
        if categorical:
            transformers.append(("categorical", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")),
                                                               ("onehot", OneHotEncoder(handle_unknown="ignore"))]), categorical))
        preprocess = ColumnTransformer(transformers, remainder="drop")
        candidate = Pipeline([("preprocess", preprocess), ("model", ExtraTreesRegressor(
            n_estimators=350, min_samples_leaf=2, max_features=1.0, random_state=42, n_jobs=-1))])
        baseline = DummyRegressor(strategy="median")
        pred = np.full(len(X), np.nan, dtype=float)
        base_pred = np.full(len(X), np.nan, dtype=float)
        fold_rows = []
        for fold, (train_idx, test_idx) in enumerate(target_cv.split(X, y, g), start=1):
            candidate.fit(X.iloc[train_idx], y[train_idx])
            baseline.fit(X.iloc[train_idx], y[train_idx])
            pred[test_idx] = candidate.predict(X.iloc[test_idx])
            base_pred[test_idx] = baseline.predict(X.iloc[test_idx])
            fold_rows.append({"fold": fold, "train_rows": len(train_idx), "test_rows": len(test_idx),
                              "train_groups": len(set(g[train_idx])), "test_groups": len(set(g[test_idx])),
                              "candidate_mae": float(mean_absolute_error(y[test_idx], pred[test_idx])),
                              "median_baseline_mae": float(mean_absolute_error(y[test_idx], base_pred[test_idx]))})
        candidate_mae = float(mean_absolute_error(y, pred))
        baseline_mae = float(mean_absolute_error(y, base_pred))
        candidate.fit(X, y)
        baseline.fit(X, y)
        artifact = artifact_dir / f"{target}.joblib"
        joblib.dump(candidate, artifact)
        # Applicability ranges are computed only from original training rows.
        domain = {}
        categories = {}
        for col in numeric:
            vals = subset[col].dropna().astype(float)
            if len(vals): domain[col] = {"min": float(vals.min()), "max": float(vals.max())}
        for col in categorical:
            categories[col] = sorted({str(v) for v in subset[col].dropna().tolist()})
        # Independent holdout rows are never used for fitting or cross-validation.
        external_rows = [dict(r) for r in con.execute(
            "SELECT * FROM microfluidic_runs WHERE is_synthetic=0 AND is_holdout=1 AND " + target + " IS NOT NULL ORDER BY id"
        ).fetchall()]
        external_result = {"rows": len(external_rows), "in_domain_rows": 0, "out_of_domain_rows": 0,
                           "mae": None, "median_baseline_mae": None}
        if external_rows:
            ext_df = _to_dataframe(external_rows)
            for col in features:
                if col not in ext_df.columns:
                    ext_df[col] = np.nan
            valid_indices, invalid_count = [], 0
            for idx, ext in ext_df.iterrows():
                in_domain = (str(ext.get("concentration_unit") or "").strip() == concentration_unit and
                             str(ext.get("flow_rate_unit") or "").strip() == flow_rate_unit)
                for col, bounds in domain.items():
                    if not in_domain:
                        break
                    value = _parse_float(ext.get(col))
                    if value is None or value < bounds["min"] or value > bounds["max"]:
                        in_domain = False
                        break
                if in_domain:
                    for col, values in categories.items():
                        value = str(ext.get(col) or "").strip()
                        if not value or value not in values:
                            in_domain = False
                            break
                if in_domain:
                    valid_indices.append(idx)
                else:
                    invalid_count += 1
            external_result["in_domain_rows"] = len(valid_indices)
            external_result["out_of_domain_rows"] = invalid_count
            if valid_indices:
                ext_X = ext_df.loc[valid_indices, features]
                ext_y = ext_df.loc[valid_indices, target].astype(float).to_numpy()
                ext_pred = candidate.predict(ext_X)
                ext_base = baseline.predict(ext_X)
                external_result["mae"] = float(mean_absolute_error(ext_y, ext_pred))
                external_result["median_baseline_mae"] = float(mean_absolute_error(ext_y, ext_base))
        reports[target] = {
            "label": label, "status": "candidate" if candidate_mae < baseline_mae else "withheld",
            "reason": "Exploratory model candidate; not prospectively validated." if candidate_mae < baseline_mae else "Candidate did not beat the median baseline under grouped validation; prediction remains withheld.",
            "algorithm": "ExtraTreesRegressor", "n_original_rows": int(len(subset)), "n_validation_groups": int(subset[group_column].nunique()),
            "grouping_column": group_column, "external_validation": external_result,
            "features": target_features, "numeric_features": numeric, "categorical_features": categorical,
            "grouped_cv_folds": n_target_splits, "grouped_cv_mae": candidate_mae, "median_baseline_mae": baseline_mae,
            "beats_median_baseline": bool(candidate_mae < baseline_mae), "folds": fold_rows,
            "applicability_domain": {"numeric_ranges": domain, "categorical_values": categories},
            "artifact": str(artifact.relative_to(Path(root))), "trained_at": utc_now(),
            "validation_note": f"Grouped by {group_column}; not a prospective wet-lab validation. Synthetic and holdout rows excluded from fitting.",
        }
    if not reports:
        raise ValueError("No supported target columns were found. Import size_nm and/or pdi labels.")
    cards = {"trained_at": utc_now(), "dataset_rows_original": len(rows), "validation_groups": n_groups,
             "study_groups": n_groups, "grouping_column": group_column, "input_units": input_units,
             "holdout_rows": int(con.execute("SELECT COUNT(*) FROM microfluidic_runs WHERE is_synthetic=0 AND is_holdout=1").fetchone()[0]),
             "features": features, "models": reports,
             "global_status": "exploratory_only", "note": "Research prototype. These models do not establish efficacy or optimized formulations."}
    cards_path = model_cards_path(root)
    tmp_path = cards_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(cards, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(cards_path)
    return cards


def predict(con, root: str | Path, payload: dict) -> dict:
    try:
        import joblib
        import pandas as pd
    except ImportError as exc:
        raise ValueError("Prediction dependencies are not installed. Install the v3 requirements.") from exc
    cards = get_model_cards(root)
    models = cards.get("models") or {}
    if not models:
        raise ValueError("No trained model package is installed. Import the verified dataset and train the exploratory models first.")
    input_row = {}
    expected_units = cards.get("input_units") or {}
    requested_concentration_unit = str(payload.get("concentration_unit") or expected_units.get("concentration") or "").strip()
    requested_flow_unit = str(payload.get("flow_rate_unit") or expected_units.get("flow_rate") or "").strip()
    units_match = (requested_concentration_unit == expected_units.get("concentration") and
                   requested_flow_unit == expected_units.get("flow_rate"))
    for col in FEATURES:
        raw = payload.get(col)
        if col in {"chip_type", "aqueous_medium"}:
            input_row[col] = str(raw or "").strip() or None
        else:
            input_row[col] = _parse_float(raw)
    results = {}
    for target, card in models.items():
        if card.get("status") != "candidate":
            results[target] = {"status": "withheld", "reason": card.get("reason")}
            continue
        if not units_match:
            results[target] = {"status": "abstained_unit_mismatch", "reason": f"Model was trained with concentrations in {expected_units.get('concentration')} and total flow rate in {expected_units.get('flow_rate')}. Supply inputs in those same units."}
            continue
        domain = card.get("applicability_domain", {})
        violations = []
        for col, bounds in domain.get("numeric_ranges", {}).items():
            value = input_row.get(col)
            if value is None:
                violations.append(f"{col} is required")
            elif value < bounds["min"] or value > bounds["max"]:
                violations.append(f"{col}={value} is outside training range {bounds['min']}–{bounds['max']}")
        for col, values in domain.get("categorical_values", {}).items():
            value = input_row.get(col)
            if value is None:
                violations.append(f"{col} is required")
            elif value not in values:
                violations.append(f"{col}='{value}' was not present in training data")
        if violations:
            results[target] = {"status": "abstained_outside_domain", "reason": "; ".join(violations)}
            continue
        artifact = Path(root) / card["artifact"]
        if not artifact.exists():
            results[target] = {"status": "withheld", "reason": "Model artifact is missing from this installation."}
            continue
        model = joblib.load(artifact)
        x = pd.DataFrame([{c: input_row.get(c) for c in card["features"]}])
        results[target] = {"status": "exploratory_estimate", "prediction": float(model.predict(x)[0]),
                           "unit": "nm" if target == "size_nm" else "PDI",
                           "grouped_cv_mae": card.get("grouped_cv_mae"), "baseline_mae": card.get("median_baseline_mae"),
                           "caveat": "Exploratory within-domain estimate only; no prospective validation."}
    return {"status": "exploratory_only", "results": results, "input": input_row, "input_units": expected_units}



def _norm_match(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def recommend_precedents(con, payload: dict) -> dict:
    """Rank real catalogue batches by transparent field matches; this is not a predictor."""
    drug = str(payload.get("drug") or "").strip()[:120]
    organ = str(payload.get("organ") or "").strip()[:120]
    tissue = str(payload.get("tissue") or "").strip()[:160]
    route = str(payload.get("route") or "").strip()[:120]
    vesicle = str(payload.get("vesicle_class") or "").strip()[:100]
    if not any((drug, organ, tissue, route, vesicle)):
        raise ValueError("Enter at least one search criterion: drug, organ, tissue, route or vesicle class.")
    if not _table_exists(con, "formulations"):
        return {"results": [], "message": "The formulation catalogue is unavailable."}
    rows = [dict(r) for r in con.execute("""SELECT Formulation_ID,Drug,drug_norm,Vesicle_Class,Vesicle_Subtype,Target_Organ,
        Target_Tissue_Cell_Original,Route,Stated_Ratio_Original,Ratio_Basis_Standardized,Surface_Modification,
        Drug_Loading_EE_DL,Size_PDI_Zeta,Preparation_Method,Key_Findings,Evidence_Level,Paper_Title,Year,DOI,URL
        FROM formulations GROUP BY Formulation_ID""").fetchall()]
    q = {"drug": _norm_match(drug), "organ": _norm_match(organ), "tissue": _norm_match(tissue),
         "route": _norm_match(route), "vesicle": _norm_match(vesicle)}
    ranked = []
    for row in rows:
        r = dict(row)
        score, reasons = 0, []
        values = {"drug": _norm_match(r.get("Drug")), "drug_norm": _norm_match(r.get("drug_norm")),
                  "organ": _norm_match(r.get("Target_Organ")), "tissue": _norm_match(r.get("Target_Tissue_Cell_Original")),
                  "route": _norm_match(r.get("Route")), "vesicle": _norm_match(r.get("Vesicle_Class"))}
        if q["drug"]:
            if q["drug"] in {values["drug"], values["drug_norm"]}: score += 5; reasons.append("drug exact")
            elif q["drug"] in values["drug"] or q["drug"] in values["drug_norm"]: score += 3; reasons.append("drug partial")
        if q["organ"]:
            if q["organ"] == values["organ"]: score += 5; reasons.append("organ exact")
            elif q["organ"] and values["organ"] and (q["organ"] in values["organ"] or values["organ"] in q["organ"]): score += 3; reasons.append("organ related")
        if q["tissue"] and q["tissue"] in values["tissue"]: score += 2; reasons.append("tissue match")
        if q["route"] and q["route"] in values["route"]: score += 3; reasons.append("route match")
        if q["vesicle"] and q["vesicle"] in values["vesicle"]: score += 4; reasons.append("vesicle class match")
        if score:
            r["match_score"] = score
            r["match_reasons"] = reasons
            r["evidence_relation"] = "Direct drug match" if "drug exact" in reasons else "Related formulation precedent"
            ranked.append(r)
    ranked.sort(key=lambda x: (x["match_score"], int(x.get("Year") or 0)), reverse=True)
    limit = max(1, min(int(payload.get("limit") or 10), 15))
    return {"query": {"drug": drug, "organ": organ, "tissue": tissue, "route": route, "vesicle_class": vesicle},
            "results": ranked[:limit], "total_matches": len(ranked),
            "method": "Transparent weighted field matching against published catalogue batches; not a trained ML prediction or evidence-quality score.",
            "disclaimer": "Review each cited paper and its original methods. Matching does not establish efficacy, targeting, compatibility or an optimized formulation."}

def _tokens(question: str) -> list[str]:
    toks = re.findall(r"[a-z0-9][a-z0-9.+-]{2,}", question.lower())
    return [t for t in dict.fromkeys(toks) if t not in STOP_WORDS][:12]


def retrieve_evidence(con, question: str, limit: int = 12) -> list[dict]:
    terms = _tokens(question)
    if not terms:
        return []
    candidates: list[dict] = []

    def score_record(source_type: str, source_id: str, text: str, data: dict):
        hay = " ".join([str(text or "").lower(), json.dumps(data, ensure_ascii=False, default=str).lower()])
        hits = [t for t in terms if t in hay]
        if hits:
            candidates.append({"source_type": source_type, "source_id": source_id, "score": len(hits), "matched_terms": hits, "text": text, "data": data})

    if _table_exists(con, "nanovesicle_knowledge"):
        for r in con.execute("SELECT * FROM nanovesicle_knowledge").fetchall():
            d = dict(r)
            score_record("Knowledge base", f"KB-{d.get('kb_id')}", d.get("statement"), d)
    if _table_exists(con, "organ_targeting_examples"):
        for r in con.execute("SELECT * FROM organ_targeting_examples").fetchall():
            d = dict(r)
            text = d.get("key_findings_as_reported_not_a_guarantee_of_targeting") or ""
            score_record("Organ-targeting example", f"ORG-{d.get('paper_id') or d.get('formulation_id') or d.get('doi')}", text, d)
    if _table_exists(con, "formulations"):
        for r in con.execute("""SELECT Formulation_ID,Paper_ID,Drug,Vesicle_Class,Vesicle_Subtype,Target_Organ,Target_Tissue_Cell_Original,Route,
            Stated_Ratio_Original,Size_PDI_Zeta,Drug_Loading_EE_DL,Preparation_Method,Key_Findings,Evidence_Level,Paper_Title,Year,DOI,URL
            FROM formulations GROUP BY Formulation_ID""").fetchall():
            d = dict(r)
            text = " | ".join(str(d.get(k) or "") for k in ("Drug", "Vesicle_Class", "Key_Findings", "Size_PDI_Zeta", "Drug_Loading_EE_DL"))
            score_record("Formulation batch", f"FORM-{d.get('Formulation_ID')}", text, d)
    if _table_exists(con, "organ_scientific_references"):
        for r in con.execute("SELECT * FROM organ_scientific_references").fetchall():
            d = dict(r)
            text = " | ".join(str(d.get(k) or "") for k in ("title", "authors", "journal"))
            score_record("Reference", f"REF-{d.get('ref_id')}", text, d)
    candidates.sort(key=lambda x: (x["score"], len(x["matched_terms"])), reverse=True)
    out, seen = [], set()
    for item in candidates:
        key = (item["source_type"], item["source_id"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _llm_answer(question: str, evidence: list[dict], con=None, user_key="local") -> dict:
    if con is None:
        return {"answer": None, "status": "controlled_context_required", "usage": None}
    return llm_explainer.explain_question(con, question, evidence, user_key)


def ask_assistant(con, question: str, user_key="local") -> dict:
    question = str(question or "").strip()[:1200]
    if len(question) < 4:
        raise ValueError("Enter a specific scientific question (at least 4 characters).")
    evidence = retrieve_evidence(con, question, 12)
    if evidence:
        synthesis = _llm_answer(question, evidence, con, user_key)
    else:
        synthesis = llm_explainer.generate(
            con, feature="research_assistant_hypothesis", user_key=user_key,
            system_prompt=(
                "You are a nanovesicle research brainstorming assistant. No local literature records were retrieved for this query. "
                "You may offer general scientific hypotheses and useful search/design directions, but clearly label them as hypotheses, not findings. "
                "Do not invent publications, DOI strings, citations, measured results, safety, efficacy or clinical claims. Do not add citation tags or URLs. "
                "State plainly that the installation did not retrieve supporting literature and identify what must be verified in primary sources."
            ),
            user_prompt="Research question: " + question + "\nProvide a useful, cautious exploratory answer even though no local records were retrieved.",
            evidence=[], max_output_tokens=600, allow_hypothesis_without_evidence=True,
        )
    answer = synthesis.get("answer")
    if evidence:
        mode = "Grounded language-model synthesis" if answer else "Evidence retrieval only"
    else:
        mode = "AI-generated hypothesis · no local literature retrieved" if answer else "No local evidence / AI synthesis unavailable"
    if not answer:
        if evidence:
            answer = ("Relevant records were retrieved from the supplied database. "
                      f"Generative synthesis was not returned (status: {synthesis.get('status', 'unavailable')}); "
                      "review the cited source records below. This is not evidence that no literature exists.")
        else:
            answer = ("No relevant records were retrieved from this installation. This is not evidence that no literature exists. "
                      f"Exploratory AI synthesis was unavailable (status: {synthesis.get('status', 'unavailable')}); "
                      "broaden the search or add source records.")
    public_evidence = [{"citation": f"[E{i}]", "source_type": x["source_type"], "source_id": x["source_id"],
                        "text": x["text"], "data": x["data"], "score": x["score"]}
                       for i, x in enumerate(evidence, start=1)]
    llm_cfg = llm_explainer.settings()
    return {"question": question, "answer": answer, "mode": mode,
            "llm_configured": llm_cfg["configured"], "llm_status": synthesis.get("status"),
            "llm_model": synthesis.get("model"), "llm_usage": synthesis.get("usage"),
            "llm_sources": synthesis.get("sources", []),
            "evidence_count": len(public_evidence), "evidence": public_evidence,
            "disclaimer": "Research use only. Retrieval ranking is lexical and is not a measure of evidence quality or efficacy."}

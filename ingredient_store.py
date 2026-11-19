"""Additive ingredient schema and lossless v4 data migration.

This module never guesses chemical identity. Legacy batch links are created only
when a canonical ingredient name is present exactly, or when a source label has
that exact canonical name followed by a short abbreviation in parentheses.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingredient (
    ingredient_id INTEGER PRIMARY KEY,
    preferred_name TEXT NOT NULL UNIQUE,
    normalized_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    review_status TEXT NOT NULL DEFAULT 'pending_review',
    material_type TEXT NOT NULL DEFAULT 'single_compound',
    molecular_weight_policy TEXT NOT NULL DEFAULT 'unknown',
    legacy_drug_name TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ingredient_name ON ingredient(normalized_name);

CREATE TABLE IF NOT EXISTS ingredient_form (
    form_id INTEGER PRIMARY KEY,
    ingredient_id INTEGER NOT NULL REFERENCES ingredient(ingredient_id),
    form_name TEXT NOT NULL,
    normalized_form TEXT NOT NULL,
    form_kind TEXT NOT NULL DEFAULT 'unspecified',
    review_status TEXT NOT NULL DEFAULT 'pending_review',
    source_record_id INTEGER,
    UNIQUE(ingredient_id, normalized_form)
);
CREATE INDEX IF NOT EXISTS idx_ingredient_form_name ON ingredient_form(normalized_form);

CREATE TABLE IF NOT EXISTS ingredient_alias (
    alias_id INTEGER PRIMARY KEY,
    ingredient_id INTEGER REFERENCES ingredient(ingredient_id),
    form_id INTEGER REFERENCES ingredient_form(form_id),
    product_id INTEGER,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    source_system TEXT,
    source_id TEXT,
    source_record_id INTEGER,
    review_status TEXT NOT NULL DEFAULT 'pending_review',
    provenance_note TEXT,
    CHECK ((ingredient_id IS NOT NULL) OR (form_id IS NOT NULL) OR (product_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_ingredient_alias_norm ON ingredient_alias(normalized_alias);

CREATE TABLE IF NOT EXISTS ingredient_identifier (
    identifier_id INTEGER PRIMARY KEY,
    ingredient_id INTEGER REFERENCES ingredient(ingredient_id),
    form_id INTEGER REFERENCES ingredient_form(form_id),
    identifier_system TEXT NOT NULL,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    source_record_id INTEGER,
    retrieved_at TEXT,
    review_status TEXT NOT NULL DEFAULT 'migrated_pending_review',
    CHECK ((ingredient_id IS NOT NULL) OR (form_id IS NOT NULL)),
    UNIQUE(identifier_system, identifier_type, identifier_value)
);

CREATE TABLE IF NOT EXISTS source_record (
    source_record_id INTEGER PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_id TEXT,
    record_kind TEXT NOT NULL,
    record_url TEXT,
    retrieved_at TEXT,
    licence_note TEXT,
    source_label TEXT,
    raw_response_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_system, source_id, record_kind)
);

CREATE TABLE IF NOT EXISTS property_observation (
    observation_id INTEGER PRIMARY KEY,
    ingredient_id INTEGER REFERENCES ingredient(ingredient_id),
    form_id INTEGER REFERENCES ingredient_form(form_id),
    property_code TEXT NOT NULL,
    property_value_json TEXT,
    unit TEXT,
    observation_kind TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_id TEXT,
    source_record_id INTEGER REFERENCES source_record(source_record_id),
    retrieved_at TEXT,
    method TEXT,
    status TEXT NOT NULL,
    source_text TEXT,
    CHECK ((ingredient_id IS NOT NULL) OR (form_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_property_observation_entity ON property_observation(ingredient_id, property_code);

CREATE TABLE IF NOT EXISTS product (
    product_id INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    product_type TEXT NOT NULL DEFAULT 'unknown',
    brand_name TEXT,
    identifier_system TEXT,
    identifier_value TEXT,
    source_record_id INTEGER REFERENCES source_record(source_record_id),
    review_status TEXT NOT NULL DEFAULT 'pending_review'
);
CREATE TABLE IF NOT EXISTS product_ingredient (
    product_id INTEGER NOT NULL REFERENCES product(product_id),
    ingredient_id INTEGER REFERENCES ingredient(ingredient_id),
    form_id INTEGER REFERENCES ingredient_form(form_id),
    declared_amount_value TEXT,
    declared_amount_unit TEXT,
    source_record_id INTEGER REFERENCES source_record(source_record_id),
    review_status TEXT NOT NULL DEFAULT 'pending_review',
    PRIMARY KEY(product_id, ingredient_id, form_id)
);

CREATE TABLE IF NOT EXISTS formulation_ingredient_link (
    link_id INTEGER PRIMARY KEY,
    formulation_id TEXT NOT NULL,
    raw_ingredient_name TEXT NOT NULL,
    ingredient_id INTEGER REFERENCES ingredient(ingredient_id),
    form_id INTEGER REFERENCES ingredient_form(form_id),
    matched_alias_id INTEGER REFERENCES ingredient_alias(alias_id),
    match_method TEXT,
    link_status TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending_review',
    unresolved_reason TEXT,
    UNIQUE(formulation_id, raw_ingredient_name)
);
CREATE INDEX IF NOT EXISTS idx_formulation_ingredient_link_entity ON formulation_ingredient_link(ingredient_id, form_id);

CREATE TABLE IF NOT EXISTS organ_ingredient_link (
    source_rowid INTEGER PRIMARY KEY,
    raw_ingredient_name TEXT NOT NULL,
    ingredient_id INTEGER REFERENCES ingredient(ingredient_id),
    form_id INTEGER REFERENCES ingredient_form(form_id),
    match_method TEXT,
    link_status TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending_review'
);
"""

PROPERTY_MAP: tuple[tuple[str, str | None, str, str | None], ...] = (
    ("mw", "g/mol", "calculated", "PubChem compound record; legacy field was labelled computed"),
    ("xlogp_computed", None, "calculated", "XLogP3 computed value as stored in v4"),
    ("tpsa", "Å²", "calculated", "PubChem structure descriptor as stored in v4"),
    ("hbd", None, "calculated", "Hydrogen-bond donor count as stored in v4"),
    ("hba", None, "calculated", "Hydrogen-bond acceptor count as stored in v4"),
    ("rotatable_bonds", None, "calculated", "Rotatable-bond count as stored in v4"),
    ("smiles", None, "retrieved", "SMILES string retained verbatim from the v4 drug profile"),
    ("logp_experimental", None, "unknown", None),
    ("logp_experimental_text", None, "reported_text", "Cited source text retained verbatim"),
    ("pka_experimental", None, "reported_text", "Cited source text retained verbatim; no numeric pKa inferred"),
    ("solubility_experimental", None, "reported_text", "Cited source text retained verbatim"),
    ("stability_experimental", None, "reported_text", "Cited source text retained verbatim"),
    ("cls", None, "reported_text", "Legacy classification retained; category review remains pending"),
)

SALT_SUFFIXES = {"sodium", "hydrochloride", "hcl", "potassium", "mesylate", "maleate",
                 "fumarate", "succinate", "tartrate", "citrate", "acetate", "besylate",
                 "tosylate", "phosphate", "bromide", "chloride", "sulfate", "sulphate",
                 "hydrogen", "disodium", "monohydrate", "dihydrate", "hydrate"}
UNSAFE_SUFFIXES = {"prodrug", "prodrugs", "conjugate", "extract", "mixture", "complex",
                   "product", "label", "metabolite", "derivative", "and", "plus"}


def normalize_name(value: Any) -> str:
    """Normalize spelling and punctuation without dropping parenthetical/salt words."""
    if value is None:
        return ""
    value = str(value).strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("β", "beta").replace("α", "alpha").replace("γ", "gamma")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()


def migrate_organ_example_links(con: sqlite3.Connection) -> int:
    """Link only exact normalized organ-example labels; ambiguous names stay unresolved."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organ_targeting_examples'").fetchone():
        return 0
    migrated=0
    for row in con.execute("SELECT rowid AS _source_rowid,drug FROM organ_targeting_examples"):
        rowid=int(row[0]); raw=str(row[1] or "").strip(); normalized=normalize_name(raw)
        ingredient_matches=con.execute("SELECT ingredient_id FROM ingredient WHERE normalized_name=?",(normalized,)).fetchall()
        form_matches=con.execute("SELECT form_id,ingredient_id FROM ingredient_form WHERE normalized_form=?",(normalized,)).fetchall()
        if len(ingredient_matches)==1 and not form_matches:
            ingredient_id=int(ingredient_matches[0][0]); form_id=None; method="exact_normalized_canonical"; status="matched_exact"
        elif len(form_matches)==1 and not ingredient_matches:
            form_id=int(form_matches[0][0]); ingredient_id=int(form_matches[0][1]); method="exact_normalized_form"; status="matched_exact"
        else:
            abbreviation=re.fullmatch(r"\s*(.*?)\s*\(([^()]*)\)\s*",raw)
            base_norm=normalize_name(abbreviation.group(1)) if abbreviation and re.fullmatch(r"[A-Za-z0-9αβγΑ-Ω-]{1,8}",abbreviation.group(2).strip()) else ""
            base_matches=con.execute("SELECT ingredient_id FROM ingredient WHERE normalized_name=?",(base_norm,)).fetchall() if base_norm else []
            if len(base_matches)==1 and not form_matches:
                ingredient_id=int(base_matches[0][0]);form_id=None;method="canonical_prefix_plus_short_abbreviation";status="matched_exact"
            else:
                ingredient_id=form_id=None; method=None; status="unresolved" if not ingredient_matches and not form_matches and not base_matches else "ambiguous"
        con.execute("INSERT INTO organ_ingredient_link(source_rowid,raw_ingredient_name,ingredient_id,form_id,match_method,link_status,review_status) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(source_rowid) DO UPDATE SET raw_ingredient_name=excluded.raw_ingredient_name,ingredient_id=excluded.ingredient_id,form_id=excluded.form_id,match_method=excluded.match_method,link_status=excluded.link_status,review_status=excluded.review_status "
                    "WHERE organ_ingredient_link.review_status NOT LIKE 'verified%'",
                    (rowid,raw,ingredient_id,form_id,method,status,"exact_source_name_pending_review" if ingredient_id else "pending_review"))
        migrated+=1
    return migrated


def _source_system(source: str | None) -> str:
    return "PubChem" if source and "pubchem" in source.lower() else "Legacy SmartNano v4"


def _source_id(row: sqlite3.Row) -> str | None:
    cid = row["pubchem_cid"]
    return str(cid) if cid is not None else None


def _source_url(source: str | None) -> str | None:
    if not source:
        return None
    match = re.search(r"https?://[^\s);]+", source)
    return match.group(0).rstrip(".,]") if match else None


def _insert_source(con: sqlite3.Connection, system: str, source_id: str | None,
                   kind: str, label: str | None, raw: dict[str, Any], url: str | None = None) -> int:
    con.execute(
        "INSERT OR IGNORE INTO source_record(source_system,source_id,record_kind,record_url,source_label,raw_response_json) VALUES(?,?,?,?,?,?)",
        (system, source_id, kind, url, label, json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)),
    )
    row = con.execute(
        "SELECT source_record_id FROM source_record WHERE source_system=? AND source_id IS ? AND record_kind=?",
        (system, source_id, kind),
    ).fetchone()
    return int(row[0])


def _insert_alias(con: sqlite3.Connection, ingredient_id: int, alias: str, alias_type: str,
                  review_status: str, source_system: str, source_id: str | None,
                  provenance_note: str | None = None) -> int:
    normalized = normalize_name(alias)
    row = con.execute(
        "SELECT alias_id FROM ingredient_alias WHERE ingredient_id=? AND form_id IS NULL AND alias=? AND alias_type=?",
        (ingredient_id, alias, alias_type),
    ).fetchone()
    if row:
        return int(row[0])
    cur = con.execute(
        "INSERT INTO ingredient_alias(ingredient_id,alias,normalized_alias,alias_type,source_system,source_id,review_status,provenance_note) VALUES(?,?,?,?,?,?,?,?)",
        (ingredient_id, alias, normalized, alias_type, source_system, source_id, review_status, provenance_note),
    )
    return int(cur.lastrowid)


def _insert_form(con: sqlite3.Connection, ingredient_id: int, form_name: str, form_kind: str,
                 source_record_id: int) -> int:
    normalized = normalize_name(form_name)
    con.execute(
        "INSERT OR IGNORE INTO ingredient_form(ingredient_id,form_name,normalized_form,form_kind,review_status,source_record_id) VALUES(?,?,?,?,?,?)",
        (ingredient_id, form_name, normalized, form_kind, "pending_review", source_record_id),
    )
    row = con.execute(
        "SELECT form_id FROM ingredient_form WHERE ingredient_id=? AND normalized_form=?",
        (ingredient_id, normalized),
    ).fetchone()
    return int(row[0])


def _insert_form_alias(con: sqlite3.Connection, form_id: int, alias: str,
                       source_record_id: int) -> int:
    normalized = normalize_name(alias)
    row = con.execute(
        "SELECT alias_id FROM ingredient_alias WHERE form_id=? AND alias=? AND alias_type='form_name'",
        (form_id, alias),
    ).fetchone()
    if row:
        return int(row[0])
    cur = con.execute(
        "INSERT INTO ingredient_alias(form_id,alias,normalized_alias,alias_type,source_system,source_id,source_record_id,review_status,provenance_note) VALUES(?,?,?,?,?,?,?,?,?)",
        (form_id, alias, normalized, "form_name", "legacy formulation catalogue", str(source_record_id),
         source_record_id, "pending_review", "Chemical form named explicitly in the legacy formulation batch label."),
    )
    return int(cur.lastrowid)


def initialize(con: sqlite3.Connection) -> dict[str, int]:
    """Create schema and idempotently migrate v4 profiles and conservative batch links."""
    con.executescript(SCHEMA_SQL)
    ingredient_columns = {row[1] for row in con.execute("PRAGMA table_info(ingredient)")}
    if "material_type" not in ingredient_columns:
        con.execute("ALTER TABLE ingredient ADD COLUMN material_type TEXT NOT NULL DEFAULT 'single_compound'")
    if "molecular_weight_policy" not in ingredient_columns:
        con.execute("ALTER TABLE ingredient ADD COLUMN molecular_weight_policy TEXT NOT NULL DEFAULT 'unknown'")
    con.commit()

    drugs = con.execute("SELECT * FROM drugs ORDER BY name").fetchall()
    ingredient_by_norm: dict[str, tuple[int, str] | None] = {}
    for row in drugs:
        name = str(row["name"])
        normalized = normalize_name(name)
        old = con.execute("SELECT ingredient_id FROM ingredient WHERE preferred_name=?", (name,)).fetchone()
        if old:
            ingredient_id = int(old[0])
        else:
            cur = con.execute(
                "INSERT INTO ingredient(preferred_name,normalized_name,category,review_status,legacy_drug_name) VALUES(?,?,?,?,?)",
                (name, normalized, "drug", "legacy_migrated_pending_review", name),
            )
            ingredient_id = int(cur.lastrowid)
        mapped = ingredient_by_norm.get(normalized)
        if mapped is None and normalized in ingredient_by_norm:
            pass  # A previous normalization collision remains ambiguous.
        elif mapped and mapped[0] != ingredient_id:
            ingredient_by_norm[normalized] = None
        else:
            ingredient_by_norm[normalized] = (ingredient_id, name)
        legacy = dict(row)
        source_text = row["source"]
        system = _source_system(source_text)
        source_id = _source_id(row)
        source_record_id = _insert_source(con, system, source_id, "legacy_drug_profile", source_text, legacy, _source_url(source_text))
        _insert_alias(con, ingredient_id, name, "canonical", "legacy_migrated", system, source_id,
                      "Preferred name copied verbatim from the v4 drugs table.")

        if row["pubchem_cid"] is not None:
            con.execute(
                "INSERT OR IGNORE INTO ingredient_identifier(ingredient_id,identifier_system,identifier_type,identifier_value,source_record_id,review_status) VALUES(?,?,?,?,?,?)",
                (ingredient_id, "PubChem", "CID", str(row["pubchem_cid"]), source_record_id, "legacy_migrated_pending_review"),
            )
        if row["cas_number"]:
            con.execute(
                "INSERT OR IGNORE INTO ingredient_identifier(ingredient_id,identifier_system,identifier_type,identifier_value,source_record_id,review_status) VALUES(?,?,?,?,?,?)",
                (ingredient_id, "CAS Registry Number", "CAS RN", str(row["cas_number"]), source_record_id, "legacy_migrated_pending_review"),
            )
        if row["smiles"]:
            con.execute(
                "INSERT OR IGNORE INTO ingredient_identifier(ingredient_id,identifier_system,identifier_type,identifier_value,source_record_id,review_status) VALUES(?,?,?,?,?,?)",
                (ingredient_id, "PubChem", "SMILES", str(row["smiles"]), source_record_id, "legacy_migrated_pending_review"),
            )

        field_names = set(row.keys())
        for field, unit, kind, method in PROPERTY_MAP:
            if field not in field_names:
                continue
            value = row[field]
            status = "missing" if value is None else "legacy_migrated_pending_review"
            if isinstance(value, str) and value.strip().lower().startswith(("not found", "not available", "not reported")):
                status = "not_available"
            con.execute(
                "INSERT INTO property_observation(ingredient_id,property_code,property_value_json,unit,observation_kind,source_system,source_id,source_record_id,retrieved_at,method,status,source_text) "
                "SELECT ?,?,?,?,?,?,?,?,NULL,?,?,? WHERE NOT EXISTS (SELECT 1 FROM property_observation WHERE ingredient_id=? AND property_code=? AND source_record_id=?)",
                (ingredient_id, field, json.dumps(value, ensure_ascii=False), unit, kind, system, source_id,
                 source_record_id, method, status, source_text, ingredient_id, field, source_record_id),
            )

    linked = unresolved = 0
    formulation_rows = con.execute("SELECT Formulation_ID,Paper_ID,Drug,DOI,URL FROM formulations ORDER BY Formulation_ID").fetchall()
    for row in formulation_rows:
        formulation_id = str(row["Formulation_ID"])
        raw_name = str(row["Drug"] or "").strip()
        if con.execute("SELECT 1 FROM formulation_ingredient_link WHERE formulation_id=? AND raw_ingredient_name=?", (formulation_id, raw_name)).fetchone():
            continue
        normalized = normalize_name(raw_name)
        match = ingredient_by_norm.get(normalized)
        method = "exact_normalized_canonical"
        form_id = None
        alias_id = None
        link_review = "legacy_migrated_pending_review"
        form_match = None
        if not match:
            for parent_norm, parent in sorted(ingredient_by_norm.items(), key=lambda pair: len(pair[0]), reverse=True):
                if not parent or not normalized.startswith(parent_norm + " "):
                    continue
                suffix = normalized[len(parent_norm):].strip()
                tokens = set(suffix.split())
                if tokens & UNSAFE_SUFFIXES or not tokens & SALT_SUFFIXES:
                    continue
                kind = "hydrate" if tokens & {"hydrate", "monohydrate", "dihydrate"} else "salt"
                form_match = (parent, kind)
                break
        if form_match:
            match, form_kind = form_match
            source_payload = {"Formulation_ID": formulation_id, "Paper_ID": row["Paper_ID"], "Drug": raw_name,
                              "DOI": row["DOI"], "URL": row["URL"]}
            formulation_source_id = _insert_source(
                con, "legacy formulation catalogue", formulation_id, "formulation_batch_label",
                str(row["Paper_ID"] or ""), source_payload, row["URL"])
            form_id = _insert_form(con, match[0], raw_name, form_kind, formulation_source_id)
            alias_id = _insert_form_alias(con, form_id, raw_name, formulation_source_id)
            method = "canonical_parent_plus_explicit_chemical_form"
            link_review = "pending_review"
        if not match:
            # A short parenthetical token is treated only as an abbreviation when
            # the full name before it exactly equals a canonical profile. Suffixes
            # such as salts, products, mixtures and prodrugs remain unresolved.
            m = re.fullmatch(r"\s*(.*?)\s*\(([^()]*)\)\s*", raw_name)
            if m and re.fullmatch(r"[A-Za-z0-9αβγΑ-Ω-]{1,8}", m.group(2).strip()):
                base_norm = normalize_name(m.group(1))
                match = ingredient_by_norm.get(base_norm)
                method = "canonical_name_plus_short_abbreviation"
                if match:
                    alias_id = _insert_alias(con, match[0], m.group(2).strip(), "abbreviation", "pending_review",
                                             "legacy formulation label", str(row["Paper_ID"] or formulation_id),
                                             f"Abbreviation appears parenthetically in batch label {formulation_id}; not yet independently reviewed.")
        if match:
            ingredient_id, canonical_name = match
            if alias_id is None and form_id is None:
                canonical_alias = con.execute(
                    "SELECT alias_id FROM ingredient_alias WHERE ingredient_id=? AND alias_type='canonical' ORDER BY alias_id LIMIT 1",
                    (ingredient_id,),
                ).fetchone()
                alias_id = int(canonical_alias[0]) if canonical_alias else None
            con.execute(
                "INSERT INTO formulation_ingredient_link(formulation_id,raw_ingredient_name,ingredient_id,form_id,matched_alias_id,match_method,link_status,review_status) VALUES(?,?,?,?,?,?,?,?)",
                (formulation_id, raw_name, ingredient_id, form_id, alias_id, method,
                 "matched_form" if form_id else "matched", link_review),
            )
            linked += 1
        else:
            reason = "Blank/no active ingredient recorded." if normalized.startswith("none blank") else "No exact canonical identity or reviewed chemical-form alias in the v4 ingredient profiles."
            con.execute(
                "INSERT INTO formulation_ingredient_link(formulation_id,raw_ingredient_name,match_method,link_status,review_status,unresolved_reason) VALUES(?,?,?,?,?,?)",
                (formulation_id, raw_name, None, "unresolved", "pending_review", reason),
            )
            unresolved += 1

    organ_examples_linked=migrate_organ_example_links(con)
    licence_notes_backfilled = backfill_source_licence_notes(con)
    con.commit()
    return {"profiles_migrated": len(drugs), "batches_linked_this_run": linked,
            "batches_unresolved_this_run": unresolved,
            "organ_examples_linked_this_run": organ_examples_linked,
            "batches_total": len(formulation_rows),
            "licence_notes_backfilled": licence_notes_backfilled}


def backfill_source_licence_notes(con: sqlite3.Connection) -> int:
    """Add cautious, non-granting reuse notes to migrated source rows missing metadata."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_record'").fetchone():
        return 0
    pubchem_note = ("PubChem aggregates third-party records; consult source-specific reuse terms before redistribution. "
                    "No blanket licence is assumed.")
    legacy_note = ("Legacy v4 source record; consult the associated source workbook and original publication rights. "
                   "This record does not grant downstream reuse rights.")
    cur = con.execute("""UPDATE source_record SET licence_note=CASE
        WHEN lower(source_system) LIKE '%pubchem%' THEN ?
        WHEN lower(source_system) LIKE '%legacy%' THEN ?
        ELSE licence_note END
        WHERE (licence_note IS NULL OR trim(licence_note)='')
          AND (lower(source_system) LIKE '%pubchem%' OR lower(source_system) LIKE '%legacy%')""",
        (pubchem_note, legacy_note))
    return max(0, int(cur.rowcount))


def catalogue_stats(con: sqlite3.Connection) -> dict[str, Any]:
    """Return counts without treating source-retrieved or migrated rows as verified."""
    def exists(name: str) -> bool:
        return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    if not exists("ingredient"):
        return {"total": 0, "verified": 0, "verified_share": 0.0, "categories": {}, "review_status_counts": {}}
    categories: dict[str, dict[str, Any]] = {}
    for row in con.execute("SELECT category,review_status,COUNT(*) AS n FROM ingredient GROUP BY category,review_status ORDER BY category,review_status"):
        category = str(row[0] or "other")
        item = categories.setdefault(category, {"total": 0, "verified": 0, "partially_verified": 0, "rejected": 0, "pending_review": 0, "verified_share": 0.0})
        status = str(row[1] or "pending_review").strip().lower()
        n = int(row[2])
        item["total"] += n
        if status.startswith("verified"):
            item["verified"] += n
        elif status.startswith("partially_verified"):
            item["partially_verified"] += n
        elif status.startswith("rejected"):
            item["rejected"] += n
        else:
            item["pending_review"] += n
    for item in categories.values():
        item["verified_share"] = round(item["verified"] / item["total"], 4) if item["total"] else 0.0
    review_status_counts = {str(r[0] or "pending_review"): int(r[1]) for r in con.execute("SELECT review_status,COUNT(*) FROM ingredient GROUP BY review_status ORDER BY review_status")}
    total = sum(x["total"] for x in categories.values())
    verified = sum(x["verified"] for x in categories.values())
    products = int(con.execute("SELECT COUNT(*) FROM product").fetchone()[0]) if exists("product") else 0
    product_links = int(con.execute("SELECT COUNT(*) FROM product_ingredient").fetchone()[0]) if exists("product_ingredient") else 0
    formulation_batches = int(con.execute("SELECT COUNT(DISTINCT Formulation_ID) FROM formulations").fetchone()[0]) if exists("formulations") else 0
    review_queue = int(con.execute("SELECT COUNT(*) FROM ingredient_review_queue").fetchone()[0]) if exists("ingredient_review_queue") else 0
    duplicate_flags = int(con.execute("SELECT COUNT(*) FROM ingredient_duplicate_flag WHERE status='pending_review'").fetchone()[0]) if exists("ingredient_duplicate_flag") else 0
    return {"total": total, "verified": verified, "verified_share": round(verified / total, 4) if total else 0.0,
            "categories": categories, "review_status_counts": review_status_counts,
            "products": products, "product_ingredient_links": product_links,
            "review_queue_items": review_queue, "pending_duplicate_flags": duplicate_flags,
            "formulation_batches": formulation_batches,
            "ingredient_catalogue_import_adds_formulation_batches": 0}

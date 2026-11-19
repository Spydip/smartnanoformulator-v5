"""Reproducible local-first ingredient and product seed importer.

Only resolver inputs are curated. Chemical records/properties are created from
source responses; ambiguous, mixed, family, extract, polymer and non-chemical
inputs are retained in a review queue without invented molecular weights.
"""
from __future__ import annotations
import csv
import hashlib
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import engine
import ingredient_store

IMPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalogue_import_run (
    run_id TEXT PRIMARY KEY,
    seed_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    report_json TEXT
);
CREATE TABLE IF NOT EXISTS catalogue_import_item (
    item_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES catalogue_import_run(run_id),
    category TEXT NOT NULL,
    input_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    material_type TEXT NOT NULL,
    status TEXT NOT NULL,
    ingredient_id INTEGER REFERENCES ingredient(ingredient_id),
    form_id INTEGER REFERENCES ingredient_form(form_id),
    product_id INTEGER REFERENCES product(product_id),
    source_record_id INTEGER REFERENCES source_record(source_record_id),
    source_system TEXT,
    source_id TEXT,
    retrieved_at TEXT,
    review_status TEXT NOT NULL,
    message TEXT,
    UNIQUE(run_id, category, normalized_name)
);
CREATE INDEX IF NOT EXISTS idx_catalogue_item_run ON catalogue_import_item(run_id,category,status);
CREATE TABLE IF NOT EXISTS ingredient_review_queue (
    queue_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    category TEXT NOT NULL,
    input_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    material_type TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    source_system TEXT,
    source_id TEXT,
    source_record_id INTEGER REFERENCES source_record(source_record_id),
    retrieved_at TEXT,
    candidate_json TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending_review',
    message TEXT,
    UNIQUE(run_id,category,normalized_name)
);
CREATE TABLE IF NOT EXISTS ingredient_candidate_result (
    candidate_result_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    category TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_id TEXT,
    source_record_id INTEGER REFERENCES source_record(source_record_id),
    retrieved_at TEXT,
    candidate_json TEXT,
    resolution_status TEXT NOT NULL,
    message TEXT,
    UNIQUE(run_id,category,normalized_name,source_system,source_id)
);
CREATE TABLE IF NOT EXISTS ingredient_duplicate_flag (
    duplicate_flag_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    input_name TEXT NOT NULL,
    duplicate_basis TEXT NOT NULL,
    existing_entity TEXT NOT NULL,
    candidate_entity TEXT,
    source_record_id INTEGER REFERENCES source_record(source_record_id),
    status TEXT NOT NULL DEFAULT 'pending_review',
    details_json TEXT,
    UNIQUE(run_id,input_name,duplicate_basis,existing_entity)
);
CREATE TABLE IF NOT EXISTS ingredient_selection_event (
    selection_id INTEGER PRIMARY KEY,
    run_id TEXT,
    query TEXT NOT NULL,
    candidate_key TEXT NOT NULL,
    source_system TEXT,
    source_id TEXT,
    source_record_id INTEGER REFERENCES source_record(source_record_id),
    entity_type TEXT,
    entity_id INTEGER,
    product_id INTEGER,
    selected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingredient_selection_query ON ingredient_selection_event(query,selected_at);
"""

PROPERTY_SPECS = (
    ("MolecularWeight", "mw", "g/mol", "calculated", "PubChem calculated molecular weight"),
    ("MolecularFormula", "molecular_formula", None, "retrieved", "PubChem molecular formula"),
    ("XLogP", "xlogp_computed", None, "calculated", "PubChem XLogP3 calculated descriptor"),
    ("TPSA", "tpsa", "Å²", "calculated", "PubChem calculated topological polar surface area"),
    ("HBondDonorCount", "hbd", None, "calculated", "PubChem calculated hydrogen-bond donor count"),
    ("HBondAcceptorCount", "hba", None, "calculated", "PubChem calculated hydrogen-bond acceptor count"),
    ("RotatableBondCount", "rotatable_bonds", None, "calculated", "PubChem calculated rotatable-bond count"),
)
IDENTIFIER_SPECS = (
    ("InChI", "InChI"),
    ("InChIKey", "InChIKey"),
)
STRUCTURE_PROPERTY_SPECS = (
    ("SMILES","smiles"),
    ("ConnectivitySMILES","connectivity_smiles"),
    ("CanonicalSMILES","canonical_smiles"),
    ("IsomericSMILES","isomeric_smiles"),
)


def initialize(con: sqlite3.Connection) -> None:
    con.executescript(IMPORT_SCHEMA)
    migrate_structure_strings(con)
    con.commit()


def migrate_structure_strings(con: sqlite3.Connection) -> int:
    """Move non-unique structure strings out of globally unique identifiers, preserving each source observation."""
    converted=0
    records=con.execute("SELECT source_record_id,source_id,retrieved_at,raw_response_json FROM source_record WHERE source_system='PubChem' AND record_kind LIKE 'compound_properties@%'").fetchall()
    for source_record_id,source_id,retrieved_at,raw_json in records:
        try:
            payload=json.loads(raw_json or "{}")
            properties=payload.get("PropertyTable",{}).get("Properties",[])
            record=properties[0] if properties else {}
        except (TypeError,json.JSONDecodeError):
            continue
        owner=_entity_for_identifier(con,"CID",str(source_id),"PubChem") if source_id else None
        if not owner:
            continue
        for api_field,property_code in STRUCTURE_PROPERTY_SPECS:
            value=record.get(api_field)
            if value is None:
                continue
            before=con.total_changes
            _property_observation(con,owner[0],owner[1],property_code,value,None,"retrieved",
                                  "PubChem PUG REST returned structure-string field", "PubChem",str(source_id),
                                  int(source_record_id),retrieved_at,json.dumps(value,ensure_ascii=False))
            converted+=int(con.total_changes>before)
    con.execute("DELETE FROM ingredient_identifier WHERE identifier_system='PubChem' AND identifier_type IN ('SMILES','ConnectivitySMILES','Canonical SMILES','Isomeric SMILES') AND source_record_id IN (SELECT source_record_id FROM source_record WHERE source_system='PubChem' AND record_kind LIKE 'compound_properties@%')")
    con.execute("UPDATE ingredient_duplicate_flag SET status='dismissed_nonunique_structure_string' WHERE duplicate_basis IN ('SMILES','ConnectivitySMILES','Canonical SMILES','Isomeric SMILES') AND status='pending_review'")
    return converted


def load_targets(con: sqlite3.Connection, target_csv: str | Path) -> list[dict[str, str]]:
    """Combine all distinct raw batch-name variants with the versioned target CSV."""
    targets: dict[tuple[str, str], dict[str, str]] = {}
    for (raw,) in con.execute(
        "SELECT DISTINCT trim(Drug) FROM formulations WHERE Drug IS NOT NULL AND trim(Drug)<>'' AND lower(trim(Drug)) NOT LIKE 'none%' ORDER BY trim(Drug)"
    ):
        name = str(raw)
        key = ("catalogue_batch", ingredient_store.normalize_name(name))
        explicit_form=con.execute("SELECT 1 FROM formulation_ingredient_link WHERE raw_ingredient_name=? AND form_id IS NOT NULL LIMIT 1",(name,)).fetchone()
        targets[key] = {"category":"catalogue_batch", "name":name,
                        "material_type":"chemical_form" if explicit_form else infer_material_type(name),
                        "scope_note":"Raw name from the supplied formulation catalogue; chemical identity is not inferred from a label."}
    with Path(target_csv).open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            category = (row.get("category") or "other").strip()
            name = (row.get("name") or "").strip()
            if not name:
                continue
            material_type = (row.get("material_type") or "single_compound").strip()
            key = (category, ingredient_store.normalize_name(name))
            targets[key] = {"category":category, "name":name, "material_type":material_type,
                            "scope_note":(row.get("scope_note") or "").strip()}
    return sorted(targets.values(), key=lambda x:(x["category"],ingredient_store.normalize_name(x["name"]),x["name"]))


def infer_material_type(name: str) -> str:
    text = str(name or "").strip().lower()
    if any(x in text for x in ("probiotic", "bacteria", "lactobacillus", "bifidobacterium")):
        return "non_chemical"
    if any(x in text for x in ("extract", "total flavonoid", "total polyphenol", "plant fraction", "silymarin")):
        return "extract"
    if any(x in text for x in ("collagen", "peptide", "hyaluronic", "chitosan", "siRNA", "plasmid", "interferon", "cell metabolite")):
        return "polymer_or_biologic"
    if any(x in text for x in ("prodrug", "conjugate", "quantum dot", "nanoparticle", "fluorescent probe", "fluorescent tracer", "imaging agent", "siRNA", "incorporated in", "co-encapsulated", "coencapsulated", "label", "complexes")):
        return "complex_or_conjugate"
    if re.search(r"\b(and|plus)\b|;", text):
        return "mixture"
    if re.search(r"\b(sodium|disodium|hydrochloride|hcl|mesylate|maleate|fumarate|succinate|tartrate|citrate|acetate|besylate|tosylate|phosphate|bromide|chloride|sulfate|sulphate|ethanosulfate|ethanesulfonate|monohydrate|dihydrate|hydrate)\b(?:\s*\([^)]*\))?$", text):
        return "chemical_form"
    if text in {"omega-3", "omega 3", "fish oil", "vitamin e", "vitamin b12", "vitamin c", "vitamin d"}:
        return "family_or_mixture"
    if text in {"tylenol", "panadol"}:
        return "product"
    return "single_compound"


def _entity_key(ingredient_id: int | None, form_id: int | None) -> str:
    return f"form:{form_id}" if form_id is not None else f"ingredient:{ingredient_id}"


def _source_record(con: sqlite3.Connection, provider: str, source_id: str | None,
                   kind: str, url: str | None, retrieved_at: str | None,
                   licence_note: str, source_label: str, raw: Any) -> int:
    raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    con.execute(
        "INSERT OR IGNORE INTO source_record(source_system,source_id,record_kind,record_url,retrieved_at,licence_note,source_label,raw_response_json) VALUES(?,?,?,?,?,?,?,?)",
        (provider, source_id, kind, url, retrieved_at, licence_note, source_label, raw_json),
    )
    row = con.execute(
        "SELECT source_record_id FROM source_record WHERE source_system=? AND source_id IS ? AND record_kind=?",
        (provider, source_id, kind),
    ).fetchone()
    return int(row[0])


def _dated_kind(kind: str, retrieved_at: str | None) -> str:
    day=(retrieved_at or "undated")[:10].replace("-", "")
    return f"{kind}@{day}"


def _insert_alias(con: sqlite3.Connection, name: str, ingredient_id: int | None,
                  form_id: int | None, provider: str, source_id: str | None,
                  source_record_id: int, review_status: str = "auto_retrieved_pending_review"):
    normalized = ingredient_store.normalize_name(name)
    if ingredient_id is None and form_id is None:
        return None
    existing = con.execute(
        "SELECT alias_id FROM ingredient_alias WHERE normalized_alias=? AND ingredient_id IS ? AND form_id IS ? AND alias_type='source_name'",
        (normalized, ingredient_id, form_id),
    ).fetchone()
    if existing:
        return int(existing[0])
    cur = con.execute(
        "INSERT INTO ingredient_alias(ingredient_id,form_id,alias,normalized_alias,alias_type,source_system,source_id,source_record_id,review_status,provenance_note) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (ingredient_id, form_id, name, normalized, "source_name", provider, source_id,
         source_record_id, review_status, "Source-resolved alias; pending human review."),
    )
    return int(cur.lastrowid)


def _local_entities(con: sqlite3.Connection, name: str) -> list[tuple[int | None, int | None]]:
    normalized = ingredient_store.normalize_name(name)
    values = set()
    for row in con.execute("SELECT ingredient_id,form_id FROM ingredient_alias WHERE normalized_alias=?", (normalized,)):
        values.add((int(row[0]) if row[0] is not None else None, int(row[1]) if row[1] is not None else None))
    for row in con.execute("SELECT ingredient_id,NULL FROM ingredient WHERE normalized_name=?", (normalized,)):
        values.add((int(row[0]), None))
    for row in con.execute("SELECT ingredient_id,form_id FROM formulation_ingredient_link WHERE raw_ingredient_name=? AND (ingredient_id IS NOT NULL OR form_id IS NOT NULL)", (name,)):
        values.add((int(row[0]) if row[0] is not None else None, int(row[1]) if row[1] is not None else None))
    return sorted(values, key=lambda x:(x[0] or 0,x[1] or 0))


def _local_cid(con: sqlite3.Connection, ingredient_id: int | None, form_id: int | None) -> str | None:
    if form_id is not None:
        row = con.execute("SELECT identifier_value FROM ingredient_identifier WHERE form_id=? AND identifier_system='PubChem' AND identifier_type='CID' LIMIT 1", (form_id,)).fetchone()
    else:
        row = con.execute("SELECT identifier_value FROM ingredient_identifier WHERE ingredient_id=? AND identifier_system='PubChem' AND identifier_type='CID' LIMIT 1", (ingredient_id,)).fetchone()
        if not row and ingredient_id is not None:
            row = con.execute("SELECT CAST(pubchem_cid AS TEXT) FROM drugs WHERE name=(SELECT preferred_name FROM ingredient WHERE ingredient_id=?) AND pubchem_cid IS NOT NULL", (ingredient_id,)).fetchone()
    return str(row[0]) if row and row[0] else None


def _entity_for_identifier(con: sqlite3.Connection, identifier_type: str, value: str, system: str = "PubChem"):
    row = con.execute(
        "SELECT ingredient_id,form_id FROM ingredient_identifier WHERE identifier_system=? AND identifier_type=? AND identifier_value=?",
        (system, identifier_type, value),
    ).fetchone()
    if row:
        return (int(row[0]) if row[0] is not None else None, int(row[1]) if row[1] is not None else None)
    return None


def _property_observation(con: sqlite3.Connection, ingredient_id: int | None, form_id: int | None,
                          code: str, value: Any, unit: str | None, kind: str, method: str,
                          provider: str, source_id: str, source_record_id: int,
                          retrieved_at: str | None, source_text: str | None):
    if ingredient_id is None and form_id is None:
        return
    con.execute(
        "INSERT INTO property_observation(ingredient_id,form_id,property_code,property_value_json,unit,observation_kind,source_system,source_id,source_record_id,retrieved_at,method,status,source_text) "
        "SELECT ?,?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM property_observation WHERE ingredient_id IS ? AND form_id IS ? AND property_code=? AND source_record_id=?)",
        (ingredient_id, form_id, code, json.dumps(value, ensure_ascii=False), unit, kind, provider,
         source_id, source_record_id, retrieved_at, method, "auto_retrieved_pending_review", source_text,
         ingredient_id, form_id, code, source_record_id),
    )


def _flag_duplicate(con: sqlite3.Connection, run_id: str, name: str, basis: str,
                    existing: tuple[int | None, int | None], candidate: tuple[int | None, int | None] | None,
                    source_record_id: int | None, details: dict[str, Any]):
    con.execute(
        "INSERT OR IGNORE INTO ingredient_duplicate_flag(run_id,input_name,duplicate_basis,existing_entity,candidate_entity,source_record_id,details_json) VALUES(?,?,?,?,?,?,?)",
        (run_id, name, basis, _entity_key(*existing), _entity_key(*candidate) if candidate else None, source_record_id,
         json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)),
    )


def _queue(con: sqlite3.Connection, run_id: str, target: dict[str, str], status: str,
           message: str, provider: str | None = None, source_id: str | None = None,
           source_record_id: int | None = None, retrieved_at: str | None = None,
           candidates: Any = None):
    normalized = ingredient_store.normalize_name(target["name"])
    con.execute(
        "INSERT INTO ingredient_review_queue(run_id,category,input_name,normalized_name,material_type,resolution_status,source_system,source_id,source_record_id,retrieved_at,candidate_json,review_status,message) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(run_id,category,normalized_name) DO UPDATE SET resolution_status=excluded.resolution_status,source_system=excluded.source_system,source_id=excluded.source_id,source_record_id=excluded.source_record_id,retrieved_at=excluded.retrieved_at,candidate_json=excluded.candidate_json,message=excluded.message",
        (run_id,target["category"],target["name"],normalized,target["material_type"],status,provider,source_id,
         source_record_id,retrieved_at,json.dumps(candidates,ensure_ascii=False,default=str) if candidates is not None else None,
         "pending_review",message),
    )
    if provider and candidates is not None:
        con.execute(
            "INSERT INTO ingredient_candidate_result(run_id,category,normalized_name,source_system,source_id,source_record_id,retrieved_at,candidate_json,resolution_status,message) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id,category,normalized_name,source_system,source_id) DO UPDATE SET source_record_id=excluded.source_record_id,retrieved_at=excluded.retrieved_at,candidate_json=excluded.candidate_json,resolution_status=excluded.resolution_status,message=excluded.message",
            (run_id,target["category"],normalized,provider,source_id,source_record_id,retrieved_at,
             json.dumps(candidates,ensure_ascii=False,default=str),status,message),
        )


def _save_item(con: sqlite3.Connection, run_id: str, target: dict[str, str], status: str,
               ingredient_id: int | None = None, form_id: int | None = None,
               product_id: int | None = None, source_record_id: int | None = None,
               source_system: str | None = None, source_id: str | None = None,
               retrieved_at: str | None = None, message: str = ""):
    con.execute(
        "INSERT INTO catalogue_import_item(run_id,category,input_name,normalized_name,material_type,status,ingredient_id,form_id,product_id,source_record_id,source_system,source_id,retrieved_at,review_status,message) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(run_id,category,normalized_name) DO UPDATE SET status=excluded.status,ingredient_id=excluded.ingredient_id,form_id=excluded.form_id,product_id=excluded.product_id,source_record_id=excluded.source_record_id,source_system=excluded.source_system,source_id=excluded.source_id,retrieved_at=excluded.retrieved_at,review_status=excluded.review_status,message=excluded.message",
        (run_id,target["category"],target["name"],ingredient_store.normalize_name(target["name"]),target["material_type"],status,
         ingredient_id,form_id,product_id,source_record_id,source_system,source_id,retrieved_at,
         "verified" if status=="verified" else "pending_review",message),
    )


def _enrich_rxnorm(con: sqlite3.Connection, run_id: str, target: dict[str, str],
                   entity: tuple[int | None, int | None], adapters: dict[str, Any], seen: set[str],
                   prefetched_rxnorm: dict[str,Any] | None = None):
    """Attach a source-backed RxCUI only after checking the RxNorm ingredient TTY."""
    adapter=adapters.get("rxnorm")
    if adapter is None or target["category"]!="catalogue_batch":
        return
    entity_key=_entity_key(*entity)
    if entity_key in seen:
        return
    seen.add(entity_key)
    if entity[1] is not None:
        row=con.execute("SELECT form_name FROM ingredient_form WHERE form_id=?",(entity[1],)).fetchone()
        query=str(row[0]) if row else target["name"]
        existing=con.execute("SELECT 1 FROM ingredient_identifier WHERE form_id=? AND identifier_system='RxNorm' AND identifier_type='RxCUI' LIMIT 1",(entity[1],)).fetchone()
    else:
        row=con.execute("SELECT preferred_name FROM ingredient WHERE ingredient_id=?",(entity[0],)).fetchone()
        query=str(row[0]) if row else target["name"]
        existing=con.execute("SELECT 1 FROM ingredient_identifier WHERE ingredient_id=? AND identifier_system='RxNorm' AND identifier_type='RxCUI' LIMIT 1",(entity[0],)).fetchone()
    if existing:
        return
    prefetched=(prefetched_rxnorm or {}).get(ingredient_store.normalize_name(query))
    search=prefetched["search"] if prefetched else adapter.search_name(query)
    query_id="query:"+ingredient_store.normalize_name(query)
    search_record_id=None
    if search.raw_response is not None:
        search_record_id=_source_record(con,"RxNorm",query_id,_dated_kind("name_search",search.retrieved_at),
                                        search.request_url,search.retrieved_at,search.licence_note,
                                        search.attribution,search.raw_response)
    if search.status!="found" or len(search.records)!=1:
        if search.records:
            status="rxnorm_ambiguous"
            message="RxNorm returned multiple concept IDs; no concept was selected."
            _queue(con,run_id,target,status,message,"RxNorm",query_id,search_record_id,
                   search.retrieved_at,search.records)
        return
    rxcui=str(search.records[0].get("rxcui") or "").strip()
    if not rxcui:
        return
    props=prefetched["concept"] if prefetched and prefetched.get("concept") else adapter.get_concept_properties(rxcui)
    property_record_id=None
    if props.raw_response is not None:
        property_record_id=_source_record(con,"RxNorm",rxcui,_dated_kind("concept_identity_properties",props.retrieved_at),
                                          props.request_url,props.retrieved_at,props.licence_note,
                                          props.attribution,props.raw_response)
    if props.status!="found":
        _queue(con,run_id,target,"rxnorm_property_error",props.error or "RxNorm concept properties were not returned.",
               "RxNorm",rxcui,property_record_id,props.retrieved_at,props.records)
        return
    tty=str(props.records[0].get("tty") or "").upper() if props.records else ""
    if tty!="IN":
        _queue(con,run_id,target,"rxnorm_non_ingredient_concept",
               "RxNorm concept type is not the ingredient term type IN; it was retained as source evidence, not attached as an ingredient ID.",
               "RxNorm",rxcui,property_record_id,props.retrieved_at,props.records)
        return
    owner=_entity_for_identifier(con,"RxCUI",rxcui,"RxNorm")
    if owner and owner!=entity:
        _flag_duplicate(con,run_id,query,"RxCUI",owner,entity,property_record_id or search_record_id,
                        {"rxcui":rxcui,"action":"RxCUI already belongs to a different ingredient/form; review required"})
        _queue(con,run_id,target,"rxnorm_identifier_conflict","RxCUI is already attached to another local entity.",
               "RxNorm",rxcui,property_record_id,props.retrieved_at,props.records)
        return
    con.execute("INSERT OR IGNORE INTO ingredient_identifier(ingredient_id,form_id,identifier_system,identifier_type,identifier_value,source_record_id,retrieved_at,review_status) VALUES(?,?,?,?,?,?,?,?)",
                (entity[0],entity[1],"RxNorm","RxCUI",rxcui,property_record_id or search_record_id,
                 props.retrieved_at or search.retrieved_at,"auto_retrieved_pending_review"))


def _save_product_candidates(con: sqlite3.Connection, target: dict[str, str], adapters: dict[str, Any]):
    name = target["name"]
    responses = [adapters["openfda"].search_brand(name), adapters["dailymed"].search_labels(name), adapters["dsld"].search_labels(name)]
    saved = []
    for result in responses:
        if result.status not in {"found", "ambiguous"}:
            continue
        for record in result.records:
            source_id = str(record.get("set_id") or record.get("setid") or record.get("id") or "") or None
            srid = _source_record(con, result.provider, source_id or "query:"+ingredient_store.normalize_name(name),
                                  _dated_kind("brand_product_candidate",result.retrieved_at), result.request_url, result.retrieved_at,
                                  result.licence_note, result.attribution, record)
            product_name = str(record.get("brand_name") or record.get("brandName") or record.get("title") or record.get("fullName") or name)
            con.execute(
                "INSERT INTO product(product_name,product_type,brand_name,identifier_system,identifier_value,source_record_id,review_status) "
                "SELECT ?,?,?,?,?,?,'auto_retrieved_pending_review' WHERE NOT EXISTS (SELECT 1 FROM product WHERE source_record_id=? AND product_name=?)",
                (product_name,"brand/product candidate",name,result.provider,source_id,srid,srid,product_name),
            )
            row = con.execute("SELECT product_id FROM product WHERE source_record_id=? AND product_name=? ORDER BY product_id LIMIT 1",(srid,product_name)).fetchone()
            if row:
                saved.append({"product_id":int(row[0]),"provider":result.provider,"source_id":source_id,"source_record_id":srid,"retrieved_at":result.retrieved_at})
    return saved, responses


def _process_product(con: sqlite3.Connection, run_id: str, target: dict[str, str], adapters: dict[str, Any]):
    saved, responses = _save_product_candidates(con,target,adapters)
    if saved:
        first=saved[0]
        _save_item(con,run_id,target,"product_candidates",product_id=first["product_id"],source_record_id=first["source_record_id"],
                   source_system=first["provider"],source_id=first["source_id"],retrieved_at=first["retrieved_at"],
                   message=f"{len(saved)} source-listed brand/product candidates; active ingredient links remain unreviewed.")
        _queue(con,run_id,target,"product_candidate_review",f"Review {len(saved)} source-listed products and their labels before linking active ingredients.",
               first["provider"],first["source_id"],first["source_record_id"],first["retrieved_at"],saved)
        return
    failures=[f"{r.provider}: {r.error or r.status}" for r in responses if r.status=="error"]
    if failures:
        status="product_source_error" if len(failures)==len(responses) else "product_sources_partial_error"
        message="Product lookup incomplete; source errors: "+"; ".join(failures)
    else:
        status="product_not_found"
        message="No product listing was returned by the checked label sources; no active ingredient was guessed."
    _queue(con,run_id,target,status,message)
    _save_item(con,run_id,target,status,message=message)


def _save_compound(con: sqlite3.Connection, run_id: str, target: dict[str, str],
                   entity: tuple[int | None, int | None] | None, cid: str,
                   property_result, source_label: str = "PubChem PUG REST"):
    record = property_result.records[0] if property_result.records else {}
    inchi_key = str(record.get("InChIKey") or "").strip()
    source_id = str(record.get("CID") or cid)
    srid = _source_record(con,"PubChem",source_id,_dated_kind("compound_properties",property_result.retrieved_at),property_result.request_url,
                          property_result.retrieved_at,property_result.licence_note,source_label,
                          property_result.raw_response)
    entity = entity or (None,None)
    ingredient_id, form_id = entity

    # Deduplicate/merge only on a single stable source identifier; conflicting local
    # name/identity evidence is retained as a review flag instead of being overwritten.
    for identifier_type, value in (("CID",source_id),("InChIKey",inchi_key)):
        if not value:
            continue
        owner = _entity_for_identifier(con,identifier_type,value)
        if owner and entity != (None,None) and owner != entity:
            _flag_duplicate(con,run_id,target["name"],identifier_type,owner,entity,srid,
                            {"identifier":value,"input":target["name"],"action":"identity conflict; do not merge automatically"})
            _queue(con,run_id,target,"identity_conflict_review","A stable identifier points to a different local entity; no automatic merge was made.",
                   "PubChem",source_id,srid,property_result.retrieved_at,record)
            _save_item(con,run_id,target,"identity_conflict_review",ingredient_id,form_id,source_record_id=srid,
                       source_system="PubChem",source_id=source_id,retrieved_at=property_result.retrieved_at,
                       message="Identifier conflict requires human review.")
            return
        if owner and entity == (None,None):
            entity = owner
            ingredient_id, form_id = entity
            _flag_duplicate(con,run_id,target["name"],identifier_type,owner,None,srid,
                            {"identifier":value,"action":"merged by identical stable PubChem identifier; alias remains pending review"})

    if entity == (None,None):
        # A normalized-name collision is not resolved by selecting the first row.
        named = con.execute("SELECT ingredient_id FROM ingredient WHERE normalized_name=?",(ingredient_store.normalize_name(target["name"]),)).fetchall()
        if len(named)>1:
            _queue(con,run_id,target,"normalized_name_collision","Multiple local canonical records share this normalized name.","PubChem",source_id,srid,property_result.retrieved_at,record)
            _save_item(con,run_id,target,"normalized_name_collision",source_record_id=srid,source_system="PubChem",source_id=source_id,retrieved_at=property_result.retrieved_at,message="Normalized-name collision requires review.")
            return
        if len(named)==1:
            ingredient_id=int(named[0][0])
        else:
            cur=con.execute("INSERT INTO ingredient(preferred_name,normalized_name,category,review_status,material_type,molecular_weight_policy) VALUES(?,?,?,?,?,?)",
                            (target["name"],ingredient_store.normalize_name(target["name"]),target["category"],
                             "auto_retrieved_pending_review","single_compound","single_compound_from_source"))
            ingredient_id=int(cur.lastrowid)

    _insert_alias(con,target["name"],ingredient_id,form_id,"PubChem",source_id,srid)
    for identifier_type,value in (("CID",source_id),)+tuple((kind,str(record[tag])) for tag,kind in IDENTIFIER_SPECS if record.get(tag)):
        if not value:
            continue
        owner=_entity_for_identifier(con,identifier_type,value)
        if owner and owner != (ingredient_id,form_id):
            _flag_duplicate(con,run_id,target["name"],identifier_type,owner,(ingredient_id,form_id),srid,
                            {"identifier":value,"action":"conflict; candidate retained for review"})
            continue
        con.execute("INSERT OR IGNORE INTO ingredient_identifier(ingredient_id,form_id,identifier_system,identifier_type,identifier_value,source_record_id,retrieved_at,review_status) VALUES(?,?,?,?,?,?,?,?)",
                    (ingredient_id,form_id,"PubChem",identifier_type,value,srid,property_result.retrieved_at,"auto_retrieved_pending_review"))
    # CAS is not silently inferred from this PUG REST property response; if a later
    # source provides a CAS RN, the same identifier collision checks apply.
    if form_id is not None:
        con.execute("UPDATE ingredient_form SET review_status='auto_retrieved_pending_review' WHERE form_id=?",(form_id,))
    else:
        con.execute("UPDATE ingredient SET review_status='auto_retrieved_pending_review',molecular_weight_policy=? WHERE ingredient_id=?",
                    ("single_compound_from_source" if record.get("MolecularWeight") is not None else "single_compound_mw_not_returned",ingredient_id))
    for api_tag,code,unit,kind,method in PROPERTY_SPECS:
        value=record.get(api_tag)
        if value is not None:
            _property_observation(con,ingredient_id,form_id,code,value,unit,kind,method,"PubChem",source_id,srid,
                                  property_result.retrieved_at,json.dumps(value,ensure_ascii=False))
    for api_tag,code in STRUCTURE_PROPERTY_SPECS:
        value=record.get(api_tag)
        if value is not None:
            _property_observation(con,ingredient_id,form_id,code,value,None,"retrieved",
                                  "PubChem PUG REST returned structure-string field","PubChem",source_id,srid,
                                  property_result.retrieved_at,json.dumps(value,ensure_ascii=False))
    has_mw=record.get("MolecularWeight") is not None
    _save_item(con,run_id,target,"auto_retrieved_pending_review",ingredient_id,form_id,
               source_record_id=srid,source_system="PubChem",source_id=source_id,
               retrieved_at=property_result.retrieved_at,
               message="Source properties retrieved; human verification required."+("" if has_mw else " Molecular weight was not returned."))


def _resolve_chemical(con: sqlite3.Connection, run_id: str, target: dict[str, str], adapters: dict[str, Any],
                      rxnorm_seen: set[str] | None = None, prefetched_pubchem: dict | None = None,
                      prefetched_rxnorm: dict | None = None):
    name=target["name"]
    entities=_local_entities(con,name)
    if len(entities)>1:
        _queue(con,run_id,target,"local_alias_ambiguous","Local exact alias points to multiple ingredients/forms; choose explicitly.",candidates=entities)
        _save_item(con,run_id,target,"local_alias_ambiguous",message="Multiple local exact candidates; no external result was auto-selected.")
        return
    entity=entities[0] if entities else None
    if entity and rxnorm_seen is not None:
        _enrich_rxnorm(con,run_id,target,entity,adapters,rxnorm_seen,prefetched_rxnorm)
    prepared=(prefetched_pubchem or {}).get(_target_key(target))
    cid=_local_cid(con,*entity) if entity else None
    if not entity and target["material_type"] != "single_compound":
        _queue(con,run_id,target,"no_single_compound_expected",target.get("scope_note") or "This material type has no single compound molecular weight.")
        _save_item(con,run_id,target,"no_single_compound_expected",message=target.get("scope_note") or "No single-compound MW was requested.")
        return
    if target["material_type"] != "single_compound" and not entity:
        _queue(con,run_id,target,"no_single_compound_expected",target.get("scope_note") or "No single chemical identity was assumed.")
        _save_item(con,run_id,target,"no_single_compound_expected",message=target.get("scope_note") or "No molecular weight assigned.")
        return
    if cid:
        if _source_properties_ready(con,cid):
            source=con.execute("SELECT source_record_id,retrieved_at FROM source_record WHERE source_system='PubChem' AND source_id=? AND record_kind LIKE 'compound_properties@%' ORDER BY retrieved_at DESC LIMIT 1",(cid,)).fetchone()
            _save_item(con,run_id,target,"resolved_existing",ingredient_id=entity[0] if entity else None,
                       form_id=entity[1] if entity else None,source_record_id=int(source[0]),source_system="PubChem",
                       source_id=cid,retrieved_at=source[1],message="Local source-complete PubChem record reused; no live refresh needed.")
            return
        lookup=(prepared or {}).get("properties") or adapters["pubchem"].get_properties(cid)
    else:
        resolved=(prepared or {}).get("search") or adapters["pubchem"].search_name(name)
        query_name=(prepared or {}).get("query_name") or name
        if resolved.status != "found" or len(resolved.records)!=1:
            candidates=resolved.records
            source_id="query:"+ingredient_store.normalize_name(query_name)
            search_record_id=None
            if resolved.raw_response is not None:
                search_record_id=_source_record(con,"PubChem",source_id,_dated_kind("name_search",resolved.retrieved_at),resolved.request_url,
                                                resolved.retrieved_at,resolved.licence_note,resolved.attribution,
                                                resolved.raw_response)
            status="ambiguous_external" if candidates else ("source_error" if resolved.status=="error" else "not_found")
            message=("Several PubChem CIDs were returned; none was selected." if candidates else
                     (resolved.error or "No PubChem CID was returned."))
            _queue(con,run_id,target,status,message,"PubChem",source_id,search_record_id,resolved.retrieved_at,candidates)
            _save_item(con,run_id,target,status,source_record_id=search_record_id,source_system="PubChem",
                       source_id=source_id,retrieved_at=resolved.retrieved_at,message=message)
            return
        cid=str(resolved.records[0]["cid"])
        lookup=(prepared or {}).get("properties") or adapters["pubchem"].get_properties(cid)
        if resolved.raw_response is not None:
            _source_record(con,"PubChem","query:"+ingredient_store.normalize_name(query_name),_dated_kind("name_search",resolved.retrieved_at),
                           resolved.request_url,resolved.retrieved_at,resolved.licence_note,
                           resolved.attribution,resolved.raw_response)
    if lookup.status!="found" or not lookup.records:
        status="source_error" if lookup.status=="error" else "property_record_missing"
        _queue(con,run_id,target,status,lookup.error or "The CID was resolved but the property record was unavailable.",
               "PubChem",cid, retrieved_at=lookup.retrieved_at,candidates=lookup.raw_response)
        _save_item(con,run_id,target,status,ingredient_id=entity[0] if entity else None,
                   form_id=entity[1] if entity else None,source_system="PubChem",source_id=cid,
                   retrieved_at=lookup.retrieved_at,message=lookup.error or "No property row returned.")
        return
    _save_compound(con,run_id,target,entity,cid,lookup)
    if rxnorm_seen is not None and target["category"]=="catalogue_batch":
        linked=_local_entities(con,name)
        if len(linked)==1:
            _enrich_rxnorm(con,run_id,target,linked[0],adapters,rxnorm_seen,prefetched_rxnorm)


def _source_properties_ready(con: sqlite3.Connection, cid: str) -> bool:
    return con.execute(
        "SELECT 1 FROM source_record s WHERE s.source_system='PubChem' AND s.source_id=? AND s.record_kind LIKE 'compound_properties@%' AND s.retrieved_at IS NOT NULL AND EXISTS (SELECT 1 FROM property_observation p WHERE p.source_record_id=s.source_record_id AND p.property_code='mw' AND p.status='auto_retrieved_pending_review') AND EXISTS (SELECT 1 FROM property_observation p WHERE p.source_record_id=s.source_record_id AND p.property_code='molecular_formula') AND EXISTS (SELECT 1 FROM ingredient_identifier i WHERE i.source_record_id=s.source_record_id AND i.identifier_type='InChIKey')",
        (cid,),
    ).fetchone() is not None


def _target_key(target: dict[str,str]) -> tuple[str,str]:
    return target["category"],ingredient_store.normalize_name(target["name"])


def _entity_name(con: sqlite3.Connection, entity: tuple[int | None,int | None], fallback: str) -> str:
    if entity[1] is not None:
        row=con.execute("SELECT form_name FROM ingredient_form WHERE form_id=?",(entity[1],)).fetchone()
    else:
        row=con.execute("SELECT preferred_name FROM ingredient WHERE ingredient_id=?",(entity[0],)).fetchone()
    return str(row[0]) if row else fallback


def prefetch_source_results(con: sqlite3.Connection, targets: list[dict[str,str]], adapters: dict[str,Any],
                            *, workers: int = 8, progress=None):
    """Fetch independent provider responses concurrently; never touch the v5 DB in worker threads."""
    workers=max(1,min(int(workers),12))
    pubchem=adapters.get("pubchem")
    pub_groups: dict[str,dict[str,Any]]={}
    target_to_job={}
    for target in targets:
        if target["material_type"]=="product":
            continue
        entities=_local_entities(con,target["name"])
        if len(entities)>1:
            continue
        entity=entities[0] if entities else None
        if entity is None and target["material_type"]!="single_compound":
            continue
        cid=_local_cid(con,*entity) if entity else None
        if cid and _source_properties_ready(con,cid):
            continue
        query_name=_entity_name(con,entity,target["name"]) if entity else target["name"]
        job_key="entity:"+_entity_key(*entity) if entity else "target:"+"|".join(_target_key(target))
        group=pub_groups.setdefault(job_key,{"query_name":query_name,"cid":cid,"keys":[]})
        group["keys"].append(_target_key(target))
        target_to_job[_target_key(target)]=job_key

    def fetch_pubchem(group):
        search=None
        try:
            if group["cid"]:
                properties=pubchem.get_properties(group["cid"])
            else:
                search=pubchem.search_name(group["query_name"])
                properties=pubchem.get_properties(search.records[0]["cid"]) if search.status=="found" and len(search.records)==1 else None
            return {"search":search,"properties":properties,"query_name":group["query_name"]}
        except Exception as exc:
            error=SimpleNamespace(status="error",records=[],error=f"{type(exc).__name__}: {exc}",raw_response=None,
                                  request_url=None,retrieved_at=None,licence_note="",attribution="PubChem",cache_hit=False)
            return {"search":search or error,"properties":error if group["cid"] or (search and search.status=="found") else None,
                    "query_name":group["query_name"]}

    pubchem_results={}
    pub_jobs=list(pub_groups.items())
    if pub_jobs:
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="smartnano-pubchem") as pool:
            futures={pool.submit(fetch_pubchem,group):(job_key,group) for job_key,group in pub_jobs}
            for index,future in enumerate(as_completed(futures),1):
                job_key,group=futures[future]
                result=future.result()
                for key in group["keys"]:
                    pubchem_results[key]=result
                if progress and (index%25==0 or index==len(futures)):
                    progress("PubChem",index,len(futures),group["query_name"])

    rxnorm_results={}
    rxnorm=adapters.get("rxnorm")
    rx_queries={}
    if rxnorm:
        for target in targets:
            if target["category"]!="catalogue_batch":
                continue
            entities=_local_entities(con,target["name"])
            if len(entities)!=1:
                continue
            entity=entities[0]
            if entity[1] is not None:
                exists=con.execute("SELECT 1 FROM ingredient_identifier WHERE form_id=? AND identifier_system='RxNorm' AND identifier_type='RxCUI' LIMIT 1",(entity[1],)).fetchone()
            else:
                exists=con.execute("SELECT 1 FROM ingredient_identifier WHERE ingredient_id=? AND identifier_system='RxNorm' AND identifier_type='RxCUI' LIMIT 1",(entity[0],)).fetchone()
            if exists:
                continue
            query=_entity_name(con,entity,target["name"])
            rx_queries.setdefault(ingredient_store.normalize_name(query),query)

    def fetch_rxnorm(query):
        search=None
        try:
            search=rxnorm.search_name(query)
            concept=rxnorm.get_concept_properties(str(search.records[0].get("rxcui"))) if search.status=="found" and len(search.records)==1 else None
            return {"search":search,"concept":concept,"query_name":query}
        except Exception as exc:
            error=SimpleNamespace(status="error",records=[],error=f"{type(exc).__name__}: {exc}",raw_response=None,
                                  request_url=None,retrieved_at=None,licence_note="",attribution="RxNorm",cache_hit=False)
            return {"search":search or error,"concept":None,"query_name":query}

    rx_jobs=list(rx_queries.items())
    if rx_jobs:
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="smartnano-rxnorm") as pool:
            futures={pool.submit(fetch_rxnorm,query):(key,query) for key,query in rx_jobs}
            for index,future in enumerate(as_completed(futures),1):
                key,_=futures[future]
                rxnorm_results[key]=future.result()
                if progress and (index%25==0 or index==len(futures)):
                    progress("RxNorm",index,len(futures),rxnorm_results[key]["query_name"])
    return pubchem_results,rxnorm_results


def _target_hash(targets: list[dict[str, str]]) -> str:
    canonical=json.dumps(targets,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_import(con: sqlite3.Connection, targets: list[dict[str, str]], adapters: dict[str, Any],
               *, progress=None, prefetched_pubchem: dict | None = None,
               prefetched_rxnorm: dict | None = None) -> dict[str, Any]:
    initialize(con)
    ingredients_before=int(con.execute("SELECT COUNT(*) FROM ingredient").fetchone()[0])
    has_legacy=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='drugs'").fetchone()
    legacy_profile_count=int(con.execute("SELECT COUNT(*) FROM drugs").fetchone()[0]) if has_legacy else ingredients_before
    seed_hash=_target_hash(targets)
    run_id=seed_hash[:24]
    from datetime import datetime, timezone
    started=datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("INSERT OR IGNORE INTO catalogue_import_run(run_id,seed_sha256,started_at) VALUES(?,?,?)",(run_id,seed_hash,started))
    started=str(con.execute("SELECT started_at FROM catalogue_import_run WHERE run_id=?",(run_id,)).fetchone()[0])
    rxnorm_seen=set()
    for index,target in enumerate(targets,1):
        if target["material_type"]=="product":
            _process_product(con,run_id,target,adapters)
        else:
            _resolve_chemical(con,run_id,target,adapters,rxnorm_seen,prefetched_pubchem,prefetched_rxnorm)
        con.commit()
        if progress and (index % 25 == 0 or index == len(targets)):
            progress(index,len(targets),target)
    categories={}
    for row in con.execute("SELECT category,status,COUNT(*) FROM catalogue_import_item WHERE run_id=? GROUP BY category,status ORDER BY category,status",(run_id,)):
        categories.setdefault(row[0],{})[row[1]]=int(row[2])
    entities={}
    for row in con.execute("SELECT DISTINCT category,ingredient_id,form_id FROM catalogue_import_item WHERE run_id=? AND (ingredient_id IS NOT NULL OR form_id IS NOT NULL)",(run_id,)):
        entities.setdefault(row[0],set()).add((row[1],row[2]))
    category_counts={}
    for category,items in categories.items():
        entity_set=entities.get(category,set())
        verified=sum(1 for ingredient_id,form_id in entity_set if (
            con.execute("SELECT review_status FROM ingredient WHERE ingredient_id=?",(ingredient_id,)).fetchone()[0]=="verified" if ingredient_id else
            con.execute("SELECT review_status FROM ingredient_form WHERE form_id=?",(form_id,)).fetchone()[0]=="verified"))
        pending=sum(items.get(k,0) for k in ("auto_retrieved_pending_review","product_candidates","resolved_existing","duplicate_merged_pending_review"))
        category_counts[category]={"targets":sum(items.values()),"statuses":items,"distinct_entities":len(entity_set),
                                   "verified_entities":verified,"verified_share":round(verified/len(entity_set),4) if entity_set else 0.0,
                                   "pending_review_targets":pending}
    all_entities=set().union(*entities.values()) if entities else set()
    all_verified=0
    for ingredient_id,form_id in all_entities:
        row=con.execute("SELECT review_status FROM ingredient WHERE ingredient_id=?",(ingredient_id,)).fetchone() if ingredient_id else con.execute("SELECT review_status FROM ingredient_form WHERE form_id=?",(form_id,)).fetchone()
        all_verified += int(bool(row and row[0]=="verified"))
    total_ingredients=int(con.execute("SELECT COUNT(*) FROM ingredient").fetchone()[0])
    no_mw=int(con.execute("SELECT COUNT(*) FROM ingredient i WHERE NOT EXISTS (SELECT 1 FROM property_observation p WHERE p.ingredient_id=i.ingredient_id AND p.property_code='mw' AND p.property_value_json IS NOT NULL AND p.status NOT IN ('missing','not_available'))").fetchone()[0])
    no_source_mw=int(con.execute("SELECT COUNT(*) FROM ingredient i WHERE NOT EXISTS (SELECT 1 FROM property_observation p JOIN source_record s ON s.source_record_id=p.source_record_id WHERE p.ingredient_id=i.ingredient_id AND p.property_code='mw' AND s.source_system='PubChem' AND p.retrieved_at IS NOT NULL)").fetchone()[0])
    report={"run_id":run_id,"seed_sha256":seed_hash,"started_at":started,
            "targets_total":len(targets),"categories":category_counts,
            "historical_batch_names_raw":sum(1 for x in targets if x["category"]=="catalogue_batch"),
            "historical_batch_names_normalized":len({engine.norm_exact(x["name"]) for x in targets if x["category"]=="catalogue_batch"}),
            "unique_entities_touched":len(all_entities),"verified_entities":all_verified,
            "verified_share":round(all_verified/len(all_entities),4) if all_entities else 0.0,
            "ingredient_records_total":total_ingredients,"ingredient_records_without_single_compound_source_mw":no_mw,
            "ingredient_records_baseline_v4_profiles":legacy_profile_count,
            "ingredient_records_at_import_restart":ingredients_before,
            "ingredient_records_added":max(0,total_ingredients-legacy_profile_count),
            "ingredient_records_without_source_mw":no_source_mw,
            "review_queue_items":int(con.execute("SELECT COUNT(*) FROM ingredient_review_queue WHERE run_id=?",(run_id,)).fetchone()[0]),
            "duplicate_flags":int(con.execute("SELECT COUNT(*) FROM ingredient_duplicate_flag WHERE run_id=? AND status='pending_review'",(run_id,)).fetchone()[0]),
            "formulation_batches_before":int(con.execute("SELECT COUNT(*) FROM formulations").fetchone()[0]),
            "formulation_batches_after":int(con.execute("SELECT COUNT(*) FROM formulations").fetchone()[0]),
            "formulation_batches_added":0,
            "source_terms":{"PubChem":"https://pubchem.ncbi.nlm.nih.gov/docs/data-sources","RxNorm":"https://lhncbc.nlm.nih.gov/RxNav/TermsofService.html","DailyMed":"https://dailymed.nlm.nih.gov/dailymed/about-dailymed.cfm","openFDA":"https://open.fda.gov/terms/","DSLD":"https://dsld.od.nih.gov/about"}}
    finished=datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("UPDATE catalogue_import_run SET finished_at=?,report_json=? WHERE run_id=?",(finished,json.dumps(report,ensure_ascii=False,sort_keys=True),run_id))
    con.commit()
    report["finished_at"]=finished
    return report

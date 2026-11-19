"""Local-first ingredient resolver with explicit ambiguity and source attribution.

Names never silently convert a brand, salt, mixture, or family into a canonical
single-compound record. Imported and selected source observations remain pending
human review until a separate review process verifies them.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import catalogue_import
import ingredient_store

AMBIGUOUS_FORM_QUERIES = {"magnesium glycinate", "magnesium bisglycinate"}
FUZZY_THRESHOLD = 0.80
FUZZY_MARGIN = 0.10
MAX_QUERY = 160
SOURCE_NAMES = {"pubchem":"PubChem","rxnorm":"RxNorm","dailymed":"DailyMed",
                "openfda":"openFDA","dsld":"DSLD","pubmed":"PubMed","europepmc":"Europe PMC"}


def _source_name(provider: str) -> str:
    return SOURCE_NAMES.get(str(provider).lower(), str(provider))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def initialize(con: sqlite3.Connection, project_dir: str | Path | None = None) -> dict[str, int]:
    """Create importer tables and idempotently attach the curated source-listed aliases."""
    catalogue_import.initialize(con)
    root = Path(project_dir) if project_dir else Path(__file__).resolve().parent
    alias_count = seed_pubchem_synonyms(con, root / "seed" / "acetaminophen_synonym_extract.json")
    return {"synonym_aliases_seeded_or_present": alias_count}


def seed_pubchem_synonyms(con: sqlite3.Connection, path: str | Path) -> int:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    source_system = str(payload["source_system"])
    source_id = str(payload["source_id"])
    entity = catalogue_import._entity_for_identifier(con, "CID", source_id, source_system)
    if not entity or entity[0] is None:
        return 0
    raw = payload["raw_response_excerpt"]
    con.execute(
        "INSERT OR IGNORE INTO source_record(source_system,source_id,record_kind,record_url,retrieved_at,licence_note,source_label,raw_response_json) VALUES(?,?,?,?,?,?,?,?)",
        (source_system, source_id, payload["record_kind"], payload["record_url"], payload["retrieved_at"],
         payload["licence_note"], payload["source_label"], json.dumps(raw, ensure_ascii=False, sort_keys=True)),
    )
    source = con.execute(
        "SELECT source_record_id FROM source_record WHERE source_system=? AND source_id=? AND record_kind=?",
        (source_system, source_id, payload["record_kind"]),
    ).fetchone()
    source_record_id = int(source[0])
    for item in payload["aliases"]:
        alias = str(item["alias"]).strip()
        normalized = ingredient_store.normalize_name(alias)
        exists = con.execute(
            "SELECT 1 FROM ingredient_alias WHERE ingredient_id=? AND form_id IS NULL AND normalized_alias=? AND alias_type='source_synonym'",
            (entity[0], normalized),
        ).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO ingredient_alias(ingredient_id,alias,normalized_alias,alias_type,source_system,source_id,source_record_id,review_status,provenance_note) VALUES(?,?,?,?,?,?,?,?,?)",
                (entity[0], alias, normalized, str(item["alias_type"]), source_system, source_id,
                 source_record_id, "auto_retrieved_pending_review",
                 "Exact source-listed synonym from PubChem CID 1983; pending human review. Brand synonyms do not establish a current product identity."),
            )
    con.commit()
    return len(payload["aliases"])


def _json_value(raw: str | None):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


def _properties(con: sqlite3.Connection, ingredient_id: int | None, form_id: int | None) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT p.property_code,p.property_value_json,p.unit,p.observation_kind,p.source_system,p.source_id,p.retrieved_at,p.method,p.status,p.source_text,s.record_url,s.licence_note,s.source_label,s.record_kind "
        "FROM property_observation p LEFT JOIN source_record s ON s.source_record_id=p.source_record_id "
        "WHERE p.ingredient_id IS ? AND p.form_id IS ? AND p.property_value_json IS NOT NULL "
        "ORDER BY CASE WHEN p.retrieved_at IS NULL THEN 1 ELSE 0 END,p.retrieved_at DESC,p.observation_id DESC",
        (ingredient_id, form_id),
    ).fetchall()
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row["property_code"])
        if code in selected:
            continue
        selected[code] = {
            "code": code, "value": _json_value(row["property_value_json"]), "unit": row["unit"],
            "kind": row["observation_kind"], "source": row["source_system"], "source_id": row["source_id"],
            "retrieved_at": row["retrieved_at"], "method": row["method"], "review_status": row["status"],
            "source_text": row["source_text"], "source_url": row["record_url"],
            "licence_note": row["licence_note"], "attribution": row["source_label"],
            "source_record_kind": row["record_kind"],
        }
    return list(selected.values())


def _candidate(con: sqlite3.Connection, ingredient_id: int | None, form_id: int | None,
               *, match_type: str, matched_alias: str | None = None, score: float = 1.0) -> dict[str, Any] | None:
    if form_id is not None:
        row = con.execute(
            "SELECT f.form_id,f.ingredient_id,f.form_name,f.form_kind,f.review_status,i.preferred_name,i.category,i.review_status AS ingredient_review,i.material_type,i.molecular_weight_policy "
            "FROM ingredient_form f JOIN ingredient i ON i.ingredient_id=f.ingredient_id WHERE f.form_id=?", (form_id,)
        ).fetchone()
        if not row:
            return None
        name, kind = str(row["form_name"]), "form"
        iid = int(row["ingredient_id"])
        review = str(row["review_status"])
        parent = str(row["preferred_name"])
        material = str(row["material_type"])
        policy = str(row["molecular_weight_policy"])
    else:
        row = con.execute(
            "SELECT ingredient_id,preferred_name,category,review_status,material_type,molecular_weight_policy FROM ingredient WHERE ingredient_id=?", (ingredient_id,)
        ).fetchone()
        if not row:
            return None
        iid = int(row["ingredient_id"])
        name, kind = str(row["preferred_name"]), "ingredient"
        review = str(row["review_status"])
        parent = None
        material = str(row["material_type"])
        policy = str(row["molecular_weight_policy"])
    if form_id is None:
        evidence = int(con.execute(
            "SELECT COUNT(DISTINCT formulation_id) FROM formulation_ingredient_link WHERE ingredient_id=? AND form_id IS NULL AND link_status='matched'", (iid,)
        ).fetchone()[0])
    else:
        evidence = int(con.execute(
            "SELECT COUNT(DISTINCT formulation_id) FROM formulation_ingredient_link WHERE form_id=? AND link_status='matched_form'", (form_id,)
        ).fetchone()[0])
    aliases = [str(r[0]) for r in con.execute(
        "SELECT alias FROM ingredient_alias WHERE ingredient_id IS ? AND form_id IS ? ORDER BY CASE alias_type WHEN 'canonical' THEN 0 WHEN 'source_synonym' THEN 1 ELSE 2 END,alias LIMIT 40",
        (iid, form_id),
    )]
    forms = []
    if form_id is None:
        forms = [{"form_id": int(r[0]), "form_name": str(r[1]), "form_kind": str(r[2]),
                  "review_status": str(r[3])}
                 for r in con.execute("SELECT form_id,form_name,form_kind,review_status FROM ingredient_form WHERE ingredient_id=? ORDER BY form_name", (iid,))]
    ids = [dict(r) for r in con.execute(
        "SELECT identifier_system,identifier_type,identifier_value,retrieved_at,review_status,source_record_id FROM ingredient_identifier WHERE ingredient_id IS ? AND form_id IS ? ORDER BY identifier_system,identifier_type",
        (iid, form_id),
    )]
    return {
        "candidate_key": f"local:{kind}:{form_id if form_id is not None else iid}",
        "entity_type": kind, "ingredient_id": iid, "form_id": form_id, "name": name,
        "canonical_name": parent or name, "category": row["category"], "material_type": material,
        "molecular_weight_policy": policy, "review_status": review, "match_type": match_type,
        "matched_alias": matched_alias, "score": round(float(score), 3), "aliases": aliases,
        "forms": forms, "identifiers": ids, "properties": _properties(con, iid, form_id),
        "formulation_evidence_count": evidence,
        "has_formulation_evidence": evidence > 0,
    }


def _local_exact(con: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    norm = ingredient_store.normalize_name(query)
    rows: dict[tuple[int | None, int | None], dict[str, Any]] = {}
    for r in con.execute(
        "SELECT a.ingredient_id,a.form_id,a.alias,a.alias_type,a.review_status,i.normalized_name,f.normalized_form "
        "FROM ingredient_alias a LEFT JOIN ingredient i ON i.ingredient_id=a.ingredient_id "
        "LEFT JOIN ingredient_form f ON f.form_id=a.form_id WHERE a.normalized_alias=?", (norm,)
    ):
        iid = int(r["ingredient_id"]) if r["ingredient_id"] is not None else None
        fid = int(r["form_id"]) if r["form_id"] is not None else None
        key = (iid, fid)
        if fid is not None:
            match_type = "form" if r["normalized_form"] == norm else "synonym"
        elif iid is not None and r["normalized_name"] == norm:
            match_type = "canonical"
        else:
            match_type = "synonym"
        prior = rows.get(key)
        priority = {"canonical": 0, "form": 1, "synonym": 2}
        if not prior or priority[match_type] < priority[prior["match_type"]]:
            rows[key] = {"match_type": match_type, "matched_alias": str(r["alias"])}
    for r in con.execute("SELECT ingredient_id FROM ingredient WHERE normalized_name=?", (norm,)):
        key = (int(r[0]), None)
        rows.setdefault(key, {"match_type": "canonical", "matched_alias": None})
    out=[]
    for (iid,fid), meta in sorted(rows.items(), key=lambda p:(p[0][0] or 0,p[0][1] or 0)):
        item=_candidate(con,iid,fid,match_type=meta["match_type"],matched_alias=meta["matched_alias"])
        if item: out.append(item)
    return out


def _local_fuzzy(con: sqlite3.Connection, query: str, limit: int = 8) -> list[dict[str, Any]]:
    norm = ingredient_store.normalize_name(query)
    if len(norm) < 4:
        return []
    candidates: dict[tuple[int | None, int | None], tuple[float,str]] = {}
    for r in con.execute("SELECT ingredient_id,preferred_name,normalized_name FROM ingredient"):
        score=SequenceMatcher(None,norm,str(r["normalized_name"])).ratio()
        key=(int(r["ingredient_id"]),None)
        if score > candidates.get(key,(0.0,""))[0]: candidates[key]=(score,str(r["preferred_name"]))
    for r in con.execute("SELECT a.ingredient_id,a.form_id,a.alias,a.normalized_alias FROM ingredient_alias a WHERE a.ingredient_id IS NOT NULL OR a.form_id IS NOT NULL"):
        score=SequenceMatcher(None,norm,str(r["normalized_alias"])).ratio()
        key=(int(r["ingredient_id"]) if r["ingredient_id"] is not None else None,
             int(r["form_id"]) if r["form_id"] is not None else None)
        if score > candidates.get(key,(0.0,""))[0]: candidates[key]=(score,str(r["alias"]))
    ordered=sorted(candidates.items(),key=lambda x:(-x[1][0],x[0][0] or 0,x[0][1] or 0))
    out=[]
    for (iid,fid),(score,alias) in ordered[:limit]:
        if score < FUZZY_THRESHOLD: break
        item=_candidate(con,iid,fid,match_type="spelling_suggestion",matched_alias=alias,score=score)
        if item: out.append(item)
    return out


def _products(con: sqlite3.Connection, query: str, limit: int = 18) -> list[dict[str, Any]]:
    norm=ingredient_store.normalize_name(query)
    if not norm: return []
    rows=con.execute(
        "SELECT p.product_id,p.product_name,p.product_type,p.brand_name,p.identifier_system,p.identifier_value,p.source_record_id,p.review_status,s.source_system,s.record_url,s.retrieved_at,s.licence_note,s.source_label,s.raw_response_json "
        "FROM product p JOIN source_record s ON s.source_record_id=p.source_record_id ORDER BY s.retrieved_at DESC,p.product_id DESC"
    ).fetchall()
    out=[]; seen=set()
    for r in rows:
        brand=ingredient_store.normalize_name(r["brand_name"] or "")
        pname=ingredient_store.normalize_name(r["product_name"] or "")
        if brand != norm and norm not in pname: continue
        key=(r["source_system"],r["identifier_value"],r["product_name"])
        if key in seen: continue
        seen.add(key)
        raw=_json_value(r["raw_response_json"])
        raw=raw if isinstance(raw,dict) else {}
        out.append({"candidate_key":f"product:{r['source_system']}:{r['product_id']}","product_id":int(r["product_id"]),"source_record_id":int(r["source_record_id"]),
                    "name":r["product_name"],"brand_name":r["brand_name"],"product_type":r["product_type"],
                    "source":r["source_system"],"source_id":r["identifier_value"],"source_url":r["record_url"],
                    "retrieved_at":r["retrieved_at"],"review_status":r["review_status"],
                    "active_ingredient_text":raw.get("active_ingredient") or raw.get("active_ingredients") or raw.get("activeIngredient"),
                    "generic_name":raw.get("generic_name") or raw.get("genericName"),
                    "label_title":raw.get("title"),"licence_note":r["licence_note"],"attribution":r["source_label"]})
        if len(out)>=limit: break
    return out


def _source_status(result) -> dict[str, Any]:
    return {"source":result.provider,"status":result.status,"retrieved_at":result.retrieved_at,
            "error":result.error,"request_url":result.request_url,"cache_hit":bool(result.cache_hit),
            "record_count":len(result.records)}


def _store_search_run(con: sqlite3.Connection, query: str) -> str:
    run_id="resolver-"+uuid.uuid4().hex
    seed_hash=hashlib.sha256(query.encode("utf-8")).hexdigest()
    con.execute("INSERT INTO catalogue_import_run(run_id,seed_sha256,started_at) VALUES(?,?,?)",(run_id,seed_hash,_now()))
    return run_id


def _store_provider_result(con: sqlite3.Connection, run_id: str, query: str, result,
                           *, kind: str, candidate_id: str | None = None, candidate: Any = None) -> int | None:
    source_id=candidate_id or ("query:"+ingredient_store.normalize_name(query))
    source_name=_source_name(result.provider)
    record_id=None
    if result.raw_response is not None:
        record_id=catalogue_import._source_record(
            con,source_name,source_id,catalogue_import._dated_kind(kind,result.retrieved_at),
            result.request_url,result.retrieved_at,result.licence_note,result.attribution,result.raw_response)
    normalized=ingredient_store.normalize_name(query)
    candidate_row_id = f"{source_id}|{kind}" if candidate_id else source_id
    con.execute(
        "INSERT OR IGNORE INTO ingredient_candidate_result(run_id,category,normalized_name,source_system,source_id,source_record_id,retrieved_at,candidate_json,resolution_status,message) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (run_id,"interactive_search",normalized,source_name,candidate_row_id,record_id,result.retrieved_at,
         json.dumps(candidate if candidate is not None else result.records,ensure_ascii=False,sort_keys=True,default=str),
         result.status,result.error or "Source candidate retained; human selection/review required."),
    )
    return record_id


def _store_product_records(con: sqlite3.Connection, run_id: str, query: str, result,
                           record_kind: str = "brand_product_candidate") -> list[dict[str, Any]]:
    out=[]
    for record in result.records[:20]:
        raw_id=record.get("set_id") or record.get("setid") or record.get("id")
        source_id=str(raw_id) if raw_id is not None else None
        source_id=source_id or ("query:"+ingredient_store.normalize_name(query))
        source_name=_source_name(result.provider)
        _store_provider_result(con,run_id,query,result,kind=record_kind,candidate_id=source_id,candidate=record)
        product_record_id=catalogue_import._source_record(
            con,source_name,source_id,catalogue_import._dated_kind(record_kind+"_item",result.retrieved_at),
            result.request_url,result.retrieved_at,result.licence_note,result.attribution,record)
        product_name=str(record.get("brand_name") or record.get("brandName") or record.get("title") or record.get("fullName") or query)
        con.execute(
            "INSERT INTO product(product_name,product_type,brand_name,identifier_system,identifier_value,source_record_id,review_status) "
            "SELECT ?,?,?,?,?,?,'auto_retrieved_pending_review' WHERE NOT EXISTS(SELECT 1 FROM product WHERE source_record_id=? AND product_name=?)",
            (product_name,"brand/product candidate",query,source_name,source_id,product_record_id,product_record_id,product_name),
        )
        row=con.execute("SELECT product_id FROM product WHERE source_record_id=? AND product_name=? ORDER BY product_id DESC LIMIT 1",(product_record_id,product_name)).fetchone()
        if row:
            out.append({"candidate_key":f"product:{source_name}:{int(row[0])}","product_id":int(row[0]),
                        "name":product_name,"brand_name":query,"source":source_name,"source_id":source_id,
                        "source_record_id":product_record_id,"source_url":result.request_url,"retrieved_at":result.retrieved_at,
                        "active_ingredient_text":record.get("active_ingredient") or record.get("active_ingredients"),
                        "generic_name":record.get("generic_name"),"review_status":"auto_retrieved_pending_review",
                        "label_title":record.get("title"),"licence_note":result.licence_note,"attribution":result.attribution})
    return out


def _run_external(con: sqlite3.Connection, query: str, adapters: dict[str,Any]) -> dict[str,Any]:
    """Query identity and product sources only after no strong local result exists."""
    tasks={}
    if adapters.get("pubchem"):
        tasks["pubchem"]=(adapters["pubchem"].search_name,(query,))
    if adapters.get("rxnorm"):
        tasks["rxnorm"]=(adapters["rxnorm"].search_name,(query,))
    if adapters.get("openfda"):
        tasks["openfda_ingredient"]=(adapters["openfda"].search_ingredient,(query,))
        tasks["openfda_brand"]=(adapters["openfda"].search_brand,(query,))
    if adapters.get("dailymed"):
        tasks["dailymed_ingredient"]=(adapters["dailymed"].search_ingredients,(query,))
        tasks["dailymed_label"]=(adapters["dailymed"].search_labels,(query,))
    if adapters.get("dsld"):
        tasks["dsld_label"]=(adapters["dsld"].search_labels,(query,))
        tasks["dsld_ingredient"]=(adapters["dsld"].ingredient_group_search,(query,))

    results={}
    def call(task_key,method,args):
        try: return method(*args)
        except Exception as exc:
            from types import SimpleNamespace
            provider=getattr(getattr(method,"__self__",None),"provider",None)
            if not provider:
                provider=("PubChem" if task_key=="pubchem" else "RxNorm" if task_key=="rxnorm" else
                          "OpenFDA" if task_key.startswith("openfda") else "DailyMed" if task_key.startswith("dailymed") else
                          "NIH DSLD" if task_key.startswith("dsld") else "unknown")
            return SimpleNamespace(provider=provider,status="error",query=query,records=[],retrieved_at=None,
                                  attribution=provider,licence_note="",request_url=None,cache_hit=False,
                                  raw_response=None,error=f"{type(exc).__name__}: {exc}")
    with ThreadPoolExecutor(max_workers=min(8,max(1,len(tasks))),thread_name_prefix="smartnano-resolver") as pool:
        future_map={pool.submit(call,key,method,args):key for key,(method,args) in tasks.items()}
        for future in as_completed(future_map):
            results[future_map[future]]=future.result()

    run_id=_store_search_run(con,query)
    source_statuses=[]
    pubchem_result=results.get("pubchem")
    pubchem_candidates=[]
    if pubchem_result:
        search_record_id=_store_provider_result(con,run_id,query,pubchem_result,kind="name_search")
        source_statuses.append(_source_status(pubchem_result))
        for item in pubchem_result.records[:8]:
            cid=str(item.get("cid") or "").strip()
            if not cid.isdigit(): continue
            try:
                props=adapters["pubchem"].get_properties(cid)
                synonyms=adapters["pubchem"].get_synonyms(cid)
            except Exception as exc:
                from types import SimpleNamespace
                props=SimpleNamespace(status="error",records=[],raw_response=None,retrieved_at=None,provider="PubChem",request_url=None,
                                     licence_note="",attribution="PubChem",cache_hit=False,error=f"{type(exc).__name__}: {exc}")
                synonyms=props
            source_statuses.extend([_source_status(props),_source_status(synonyms)])
            property_record_id=_store_provider_result(con,run_id,query,props,kind="compound_properties",candidate_id=cid,
                                                       candidate=props.records[0] if props.records else {"error":props.error})
            synonym_record_id=_store_provider_result(con,run_id,query,synonyms,kind="compound_synonyms",candidate_id=cid,
                                                      candidate=synonyms.records[0] if synonyms.records else {"error":synonyms.error})
            prop=props.records[0] if props.records else {}
            synlist=synonyms.records[0].get("Synonym",[]) if synonyms.records else []
            matching_names=[str(x) for x in synlist if ingredient_store.normalize_name(x)==ingredient_store.normalize_name(query)]
            display_name=matching_names[0] if matching_names else query
            generic_family=ingredient_store.normalize_name(query) in AMBIGUOUS_FORM_QUERIES
            candidate={"candidate_key":f"pubchem:{cid}","candidate_type":"specific_compound_record","name":display_name,
                       "query":query,"cid":cid,"molecular_formula":prop.get("MolecularFormula"),
                       "molecular_weight":prop.get("MolecularWeight"),"molecular_weight_unit":"g/mol" if prop.get("MolecularWeight") is not None else None,
                       "properties_record_id":property_record_id,"synonyms_record_id":synonym_record_id,
                       "synonyms":[str(x) for x in synlist if x][:30],"review_status":"auto_retrieved_pending_review",
                       "specificity_notice":"PubChem CID-specific record; not a generic family value." if generic_family else "Source-retrieved candidate; human verification required.",
                       "selection_required":True,"suppress_molecular_weight_until_selected":generic_family,
                       "search_record_id":search_record_id,
                       "source":"PubChem","source_url":props.request_url or pubchem_result.request_url,
                       "retrieved_at":props.retrieved_at or pubchem_result.retrieved_at}
            _store_provider_result(con,run_id,query,pubchem_result,kind="compound_candidate",candidate_id=cid,candidate=candidate)
            pubchem_candidates.append(candidate)

    rxnorm_result=results.get("rxnorm")
    rxnorm_candidates=[]
    if rxnorm_result:
        _store_provider_result(con,run_id,query,rxnorm_result,kind="name_search")
        source_statuses.append(_source_status(rxnorm_result))
        for item in rxnorm_result.records[:8]:
            rxcui=str(item.get("rxcui") or "").strip()
            if not rxcui: continue
            try:
                concept=adapters["rxnorm"].get_concept_properties(rxcui)
            except Exception as exc:
                from types import SimpleNamespace
                concept=SimpleNamespace(provider="RxNorm",status="error",query=rxcui,records=[],retrieved_at=None,
                                        attribution="RxNorm",licence_note="",request_url=None,cache_hit=False,
                                        raw_response=None,error=f"{type(exc).__name__}: {exc}")
            source_statuses.append(_source_status(concept))
            record_id=_store_provider_result(con,run_id,query,concept,kind="concept_identity",candidate_id=rxcui,
                                             candidate=concept.records[0] if concept.records else {"error":concept.error})
            data=concept.records[0] if concept.records else {}
            candidate={"candidate_key":f"rxnorm:{rxcui}","candidate_type":"RxNorm_concept","rxcui":rxcui,
                       "name":item.get("name") or data.get("name"),"tty":data.get("tty"),
                       "source_record_id":record_id,"source":"RxNorm","request_url":concept.request_url,
                       "retrieved_at":concept.retrieved_at,"review_status":"auto_retrieved_pending_review",
                       "selection_required":str(data.get("tty") or "").upper()=="IN"}
            _store_provider_result(con,run_id,query,rxnorm_result,kind="concept_candidate",candidate_id=rxcui,candidate=candidate)
            rxnorm_candidates.append(candidate)

    product_candidates=[]
    for key in ("openfda_brand","openfda_ingredient","dailymed_label","dailymed_ingredient","dsld_label","dsld_ingredient"):
        result=results.get(key)
        if result is None: continue
        source_statuses.append(_source_status(result))
        product_candidates.extend(_store_product_records(con,run_id,query,result,record_kind="brand_product_candidate_"+key))
    con.execute(
        "INSERT OR IGNORE INTO ingredient_review_queue(run_id,category,input_name,normalized_name,material_type,resolution_status,retrieved_at,candidate_json,review_status,message) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (run_id,"interactive_search",query,ingredient_store.normalize_name(query),"user_search",
         "external_candidates_pending_review" if pubchem_candidates or rxnorm_candidates or product_candidates else "not_found_or_source_error",
         _now(),json.dumps({"pubchem":pubchem_candidates,"rxnorm":rxnorm_candidates,"products":product_candidates},ensure_ascii=False,sort_keys=True,default=str),
         "pending_review","External candidates are source-listed only; no identity or product-ingredient relationship was auto-selected."),
    )
    con.commit()
    identity_found=bool(pubchem_candidates or any(c.get("tty")=="IN" for c in rxnorm_candidates))
    any_product=bool(product_candidates)
    any_error=any(s["status"]=="error" for s in source_statuses)
    return {"run_id":run_id,"source_statuses":source_statuses,"external_ingredient_candidates":pubchem_candidates+rxnorm_candidates,
            "product_candidates":product_candidates,"identity_found":identity_found,"product_found":any_product,
            "status":"external_candidates_pending_review" if identity_found else ("product_candidates_found" if any_product else ("source_errors" if any_error else "not_found")),
            "message":"Source candidates require explicit user selection and human review." if identity_found else
                      ("Source-listed product-label candidates found; active ingredients remain separate and unlinked." if any_product else
                       ("No candidate was returned by the checked sources; some sources failed." if any_error else "No local or source-listed candidate was found."))}


def suggest(con: sqlite3.Connection, query: str, adapters: dict[str,Any] | None = None,
            *, force_external: bool = False) -> dict[str,Any]:
    query=str(query or "").strip()[:MAX_QUERY]
    norm=ingredient_store.normalize_name(query)
    if not norm:
        return {"query":"","status":"empty_query","ingredient_candidates":[],"product_candidates":[],"source_statuses":[],"message":"Enter an ingredient, chemical form, or product name."}
    exact=_local_exact(con,query)
    ambiguous_form=norm in AMBIGUOUS_FORM_QUERIES
    fuzzy=[] if exact else _local_fuzzy(con,query)
    if ambiguous_form and not exact:
        fuzzy=[]
    fuzzy_decisive=bool(fuzzy and fuzzy[0]["score"]>=FUZZY_THRESHOLD and
                        (len(fuzzy)==1 or fuzzy[0]["score"]-fuzzy[1]["score"]>=FUZZY_MARGIN))
    products=_products(con,query)
    if not (force_external and adapters) and (exact or fuzzy_decisive or products or not adapters):
        candidates=exact or (fuzzy[:1] if fuzzy_decisive else fuzzy)
        if ambiguous_form:
            for item in candidates:
                item["suppress_molecular_weight_until_selected"]=True
        if ambiguous_form:
            status="ambiguous_form_requires_selection"
        elif len(exact)>1 or (fuzzy and not fuzzy_decisive):
            status="ambiguous_candidates"
        elif len(exact)==1 and exact[0]["match_type"]=="synonym":
            status="synonym_match"
        elif len(exact)==1 and exact[0]["entity_type"]=="form":
            status="form_match"
        elif fuzzy:
            status="spelling_suggestion"
        elif products and not exact:
            status="product_candidates_found"
        elif candidates and candidates[0]["has_formulation_evidence"]:
            status="formulation_evidence_found"
        elif candidates:
            status="ingredient_found_no_formulation_evidence"
        else:
            status="ingredient_not_found"
        if ambiguous_form and len(candidates)>1:
            status="ambiguous_form_requires_selection"
        return {"query":query,"status":status,"ingredient_candidates":candidates,
                "product_candidates":products,"source_statuses":[{"source":"local library","status":"exact" if exact else ("fuzzy" if fuzzy else "no_match"),"error":None}],
                "message":("This broad magnesium-glycinate term can refer to source-specific chelate or hydrate forms. Choose an exact compound or label record; no generic molecular weight is assigned." if ambiguous_form else
                           ("Select a candidate to use it. Imported data remain pending human review." if candidates else
                            ("Brand/product records are separate from active ingredients." if products else "No local ingredient candidate found."))),
                "requires_selection":bool(candidates or products),"generic_molecular_weight":None if ambiguous_form else None}
    if adapters and force_external:
        external=_run_external(con,query,adapters)
        candidates=exact or fuzzy
        if ambiguous_form:
            for item in candidates:
                item["suppress_molecular_weight_until_selected"]=True
            for item in external["external_ingredient_candidates"]:
                item["suppress_molecular_weight_until_selected"]=True
        if ambiguous_form:
            status="ambiguous_form_requires_selection"
        elif len(exact)>1:
            status="ambiguous_candidates"
        elif len(exact)==1 and exact[0]["match_type"]=="synonym":
            status="synonym_match"
        elif len(exact)==1 and exact[0]["entity_type"]=="form":
            status="form_match"
        elif len(exact)==1:
            status="formulation_evidence_found" if exact[0]["has_formulation_evidence"] else "ingredient_found_no_formulation_evidence"
        elif candidates:
            top=candidates[0]
            if top["match_type"]=="synonym": status="synonym_match"
            elif top.get("score",0)>=FUZZY_THRESHOLD: status="spelling_suggestion"
            elif top["has_formulation_evidence"]: status="formulation_evidence_found"
            else: status="ingredient_found_no_formulation_evidence"
        elif external["identity_found"]: status="external_candidates_pending_review"
        elif external["product_found"]: status="product_candidates_found"
        else: status="ingredient_not_found"
        combined_products=[]; seen_products=set()
        for product in products+external["product_candidates"]:
            key=(product.get("source"),product.get("source_id"),product.get("name"))
            if key not in seen_products:
                seen_products.add(key);combined_products.append(product)
        source_statuses=external["source_statuses"]
        if exact or fuzzy:
            source_statuses=[{"source":"local library","status":"exact" if exact else "fuzzy","error":None}]+source_statuses
        message=("Local identity candidate retained; external candidates are separately source-listed and require review." if exact else
                 external["message"])
        return {"query":query,"status":status,"ingredient_candidates":candidates,
                "external_ingredient_candidates":external["external_ingredient_candidates"],
                "product_candidates":combined_products,"source_statuses":source_statuses,
                "message":message,"run_id":external["run_id"],
                "requires_selection":bool(candidates or external["external_ingredient_candidates"] or combined_products),
                "generic_molecular_weight":None if ambiguous_form else None}
    return {"query":query,"status":"ingredient_not_found","ingredient_candidates":[],"product_candidates":[],"source_statuses":[],"message":"No local ingredient match."}


def _record_selection(con: sqlite3.Connection, *, run_id: str | None, query: str,
                      candidate_key: str, source_system: str | None, source_id: str | None,
                      source_record_id: int | None, entity_type: str | None = None,
                      entity_id: int | None = None, product_id: int | None = None,
                      status: str, message: str) -> None:
    con.execute("INSERT INTO ingredient_selection_event(run_id,query,candidate_key,source_system,source_id,source_record_id,entity_type,entity_id,product_id,selected_at,status,message) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id,str(query or "")[:MAX_QUERY],str(candidate_key)[:120],source_system,source_id,
                 source_record_id,entity_type,entity_id,product_id,_now(),status,message))


def select_candidate(con: sqlite3.Connection, candidate_key: str, adapters: dict[str,Any], query: str = "") -> dict[str,Any]:
    candidate_key=str(candidate_key or "")[:120]
    if candidate_key.startswith("local:ingredient:") or candidate_key.startswith("local:form:"):
        parts=candidate_key.split(":")
        if len(parts)!=3 or not parts[2].isdigit():
            raise ValueError("Invalid local candidate key.")
        entity_id=int(parts[2])
        item=_candidate(con,entity_id if parts[1]=="ingredient" else None,
                        entity_id if parts[1]=="form" else None,match_type="selected")
        if not item: raise ValueError("Ingredient candidate no longer exists.")
        run=con.execute("SELECT run_id FROM ingredient_candidate_result WHERE normalized_name=? ORDER BY candidate_result_id DESC LIMIT 1",
                        (ingredient_store.normalize_name(query),)).fetchone()
        source=con.execute("SELECT s.source_system,s.source_id,p.source_record_id FROM property_observation p JOIN source_record s ON s.source_record_id=p.source_record_id WHERE p.ingredient_id=? AND p.form_id IS ? ORDER BY CASE WHEN p.retrieved_at IS NULL THEN 1 ELSE 0 END,p.retrieved_at DESC,p.observation_id DESC LIMIT 1",
                           (item["ingredient_id"],item["form_id"])).fetchone()
        _record_selection(con,run_id=run[0] if run else None,query=query,candidate_key=candidate_key,
                          source_system=source["source_system"] if source else None,source_id=source["source_id"] if source else None,
                          source_record_id=int(source["source_record_id"]) if source and source["source_record_id"] else None,
                          entity_type=item["entity_type"],entity_id=int(item["form_id"] if item["entity_type"]=="form" else item["ingredient_id"]),
                          status="selected_pending_review",message="User selected this local ingredient/form; imported data remain pending human review.")
        con.commit()
        return {"selected":item,"status":"selected_pending_review","message":"Selected source-backed/local candidate. Check every property source and review status before use."}
    if candidate_key.startswith("product:"):
        parts=candidate_key.split(":")
        if len(parts)!=3 or not parts[2].isdigit(): raise ValueError("Invalid product candidate key.")
        product_id=int(parts[2])
        product=next((p for p in _products(con,query,limit=100) if p["product_id"]==product_id),None)
        if not product: raise ValueError("Product candidate not found for this query.")
        _record_selection(con,run_id=None,query=query,candidate_key=candidate_key,source_system=product.get("source"),
                          source_id=product.get("source_id"),source_record_id=product.get("source_record_id"),product_id=product_id,
                          status="product_selected_no_ingredient_link",message="User selected a product/label record; active ingredient remains separate and unlinked.")
        con.commit()
        return {"selected_product":product,"status":"product_selected_no_ingredient_link","message":"Product label selected. No active ingredient was auto-linked; use its source label to review ingredients."}
    if candidate_key.startswith("pubchem:"):
        cid=candidate_key.split(":",1)[1]
        if not cid.isdigit(): raise ValueError("Invalid PubChem CID candidate.")
        row=con.execute("SELECT candidate_json,run_id,source_record_id FROM ingredient_candidate_result WHERE source_system='PubChem' AND source_id=? AND category='interactive_search' ORDER BY candidate_result_id DESC LIMIT 1",(f"{cid}|compound_candidate",)).fetchone()
        if not row: raise ValueError("PubChem candidate was not present in the recorded search results.")
        candidate=_json_value(row[0]) or {}
        if str(candidate.get("cid"))!=cid: raise ValueError("PubChem candidate identity mismatch.")
        result=adapters["pubchem"].get_properties(cid)
        if result.status!="found" or not result.records: raise ValueError(result.error or "PubChem property record unavailable.")
        query=str(query or candidate.get("name") or candidate.get("query") or "").strip()[:MAX_QUERY]
        if ingredient_store.normalize_name(query) in AMBIGUOUS_FORM_QUERIES:
            parent_name=f"{query.title()} (unspecified form)"
            parent=con.execute("SELECT ingredient_id FROM ingredient WHERE preferred_name=?",(parent_name,)).fetchone()
            if parent:
                parent_id=int(parent[0])
            else:
                parent_id=int(con.execute("INSERT INTO ingredient(preferred_name,normalized_name,category,review_status,material_type,molecular_weight_policy) VALUES(?,?,?,?,?,?)",
                    (parent_name,ingredient_store.normalize_name(parent_name),"mineral","auto_retrieved_pending_review","ambiguous_form_family","no_single_molecular_weight")).lastrowid)
            form_name=f"{query.title()} — PubChem CID {cid}"
            form_norm=ingredient_store.normalize_name(form_name)
            form=con.execute("SELECT form_id FROM ingredient_form WHERE ingredient_id=? AND normalized_form=?",(parent_id,form_norm)).fetchone()
            if form: form_id=int(form[0])
            else:
                form_id=int(con.execute("INSERT INTO ingredient_form(ingredient_id,form_name,normalized_form,form_kind,review_status) VALUES(?,?,?,?,?)",
                    (parent_id,form_name,form_norm,"specific PubChem compound record","auto_retrieved_pending_review")).lastrowid)
            entity=(parent_id,form_id)
        else:
            entity=None
        target={"category":"interactive_search","name":query,"material_type":"single_compound","scope_note":"User explicitly selected this source candidate; pending human verification."}
        con.execute("INSERT OR IGNORE INTO catalogue_import_run(run_id,seed_sha256,started_at) VALUES(?,?,?)",
                    (row[1],hashlib.sha256(query.encode()).hexdigest(),_now()))
        catalogue_import._save_compound(con,str(row[1]),target,entity,cid,result,source_label="PubChem PUG REST user-selected candidate")
        if entity and entity[1] is not None:
            source_row=con.execute("SELECT source_record_id FROM source_record WHERE source_system='PubChem' AND source_id=? AND record_kind LIKE 'compound_properties@%' ORDER BY retrieved_at DESC LIMIT 1",(cid,)).fetchone()
            if source_row:
                con.execute("UPDATE ingredient_form SET source_record_id=? WHERE form_id=?",(int(source_row[0]),entity[1]))
            con.execute("UPDATE ingredient SET review_status='auto_retrieved_pending_review',molecular_weight_policy='no_single_molecular_weight' WHERE ingredient_id=?",(entity[0],))
        local_after=catalogue_import._local_entities(con,query)
        selected_entity=entity if entity and entity[1] is not None else (local_after[0] if len(local_after)==1 else None)
        property_source=con.execute("SELECT source_record_id FROM source_record WHERE source_system='PubChem' AND source_id=? AND record_kind LIKE 'compound_properties@%' ORDER BY retrieved_at DESC LIMIT 1",(cid,)).fetchone()
        _record_selection(con,run_id=row[1],query=query,candidate_key=candidate_key,source_system="PubChem",source_id=cid,
                          source_record_id=int(property_source[0]) if property_source else (int(row[2]) if row[2] else None),
                          entity_type=("form" if selected_entity and selected_entity[1] is not None else "ingredient") if selected_entity else None,
                          entity_id=int(selected_entity[1] if selected_entity and selected_entity[1] is not None else selected_entity[0]) if selected_entity else None,
                          status="selected_pending_review",message="User explicitly selected the PubChem CID; source observations remain pending human review.")
        con.commit()
        if entity and entity[1] is not None:
            selected=_candidate(con,entity[0],entity[1],match_type="selected_specific_form")
            return {"selected":selected,"status":"selected_pending_review","generic_molecular_weight":None,
                    "message":"Only the explicitly selected PubChem compound form carries this source-reported value. The unspecified family has no single molecular weight."}
        local=catalogue_import._local_entities(con,query)
        selected=_candidate(con,*local[0],match_type="selected_source_candidate") if len(local)==1 else None
        return {"selected":selected,"status":"selected_pending_review","message":"Selected PubChem CID properties were saved with provenance and remain pending human verification."}
    if candidate_key.startswith("rxnorm:"):
        rxcui=candidate_key.split(":",1)[1]
        if not rxcui.isdigit(): raise ValueError("Invalid RxNorm CUI.")
        row=con.execute("SELECT candidate_json,run_id,source_record_id,retrieved_at FROM ingredient_candidate_result WHERE source_system='RxNorm' AND source_id=? AND category='interactive_search' ORDER BY candidate_result_id DESC LIMIT 1",(f"{rxcui}|concept_candidate",)).fetchone()
        if not row: raise ValueError("RxNorm candidate was not present in the recorded search results.")
        data=_json_value(row[0]) or {}
        if str(data.get("rxcui"))!=rxcui or str(data.get("tty") or "").upper()!="IN":
            raise ValueError("Only an RxNorm concept with TTY=IN can be selected as an ingredient.")
        entities=catalogue_import._local_entities(con,query) if query else []
        entity=entities[0] if len(entities)==1 else None
        link_status="selected_unlinked_pending_review"
        message="RxNorm IN concept selection recorded; no unique local ingredient/form match was available, so no identifier link was created."
        if entity:
            ingredient_id,form_id=entity
            prior=con.execute("SELECT ingredient_id,form_id FROM ingredient_identifier WHERE identifier_system='RxNorm' AND identifier_type='RxCUI' AND identifier_value=?",(rxcui,)).fetchone()
            if prior and (prior[0]!=ingredient_id or prior[1]!=form_id):
                link_status="selected_identifier_conflict_pending_review"
                message="RxNorm IN concept selection recorded, but its RxCUI is already attached elsewhere; no reassignment was made."
            else:
                if not prior:
                    con.execute("INSERT INTO ingredient_identifier(ingredient_id,form_id,identifier_system,identifier_type,identifier_value,source_record_id,retrieved_at,review_status) VALUES(?,?,?,?,?,?,?,?)",
                                (ingredient_id,form_id,"RxNorm","RxCUI",rxcui,row[2],row[3],"auto_retrieved_pending_review"))
                link_status="selected_linked_pending_review"
                message="RxNorm TTY=IN was selected and attached to one exact local ingredient/form. The identifier remains pending review and supplies no molecular weight."
        _record_selection(con,run_id=row[1],query=query,candidate_key=candidate_key,source_system="RxNorm",source_id=rxcui,
                          source_record_id=int(row[2]) if row[2] else None,
                          entity_type=("form" if entity and entity[1] is not None else "ingredient") if entity else "candidate",
                          entity_id=int(entity[1] if entity and entity[1] is not None else entity[0]) if entity else None,
                          status=link_status,message=message)
        con.commit()
        return {"status":link_status,"candidate":data,"message":message}
    raise ValueError("Unsupported ingredient candidate type.")


def search_literature(con: sqlite3.Connection, query: str, adapters: dict[str,Any]) -> dict[str,Any]:
    """Search bibliographic providers on demand and retain source records for review.

    The UI only exposes licensed OA abstract text; PubMed abstract content is
    retained in the private application DB but not copied into the result card.
    """
    query=str(query or "").strip()[:MAX_QUERY]
    if not query:
        return {"query":"","pubmed":[],"europepmc":[],"source_statuses":[],"error":"Enter a literature search term."}
    run_id=_store_search_run(con,"literature:"+query)
    pubmed=adapters.get("pubmed")
    europe=adapters.get("europepmc")
    pm_search=pm_summary=pm_xml=epmc_result=None
    provider_errors={}
    # The two provider searches are independent; the PubMed detail calls remain
    # sequential so the adapter's documented pacing is respected.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={}
        if pubmed: futures["pubmed_search"]=pool.submit(pubmed.search,query,10)
        if europe: futures["europepmc_search"]=pool.submit(europe.search_chemical,query,10,"core")
        for name,future in futures.items():
            try:
                result=future.result(timeout=45)
            except Exception as exc:
                result=None
                error=f"{type(exc).__name__}: {exc}"[:300]
                provider_errors[name]=error
            else:
                error=None
            if name=="pubmed_search": pm_search=result
            else: epmc_result=result
            if result:
                _store_provider_result(con,run_id,query,result,kind="literature_search",candidate_id="search",
                                      candidate={"records":result.records,"provider_status":result.status})
            elif error:
                con.execute("INSERT OR IGNORE INTO ingredient_candidate_result(run_id,category,normalized_name,source_system,source_id,retrieved_at,candidate_json,resolution_status,message) VALUES(?,?,?,?,?,?,?,?,?)",
                            (run_id,"interactive_search",ingredient_store.normalize_name(query),
                             "PubMed" if name.startswith("pubmed") else "Europe PMC","search|literature_search",_now(),"[]","error",error))
    pmids=[str(r.get("pmid")) for r in (pm_search.records if pm_search else []) if str(r.get("pmid","")).isdigit()][:10]
    if pubmed and pmids:
        try:
            pm_summary=pubmed.summary(pmids)
            _store_provider_result(con,run_id,query,pm_summary,kind="literature_summary",candidate_id="summary",
                                  candidate={"records":pm_summary.records,"provider_status":pm_summary.status})
        except Exception as exc:
            provider_errors["pubmed_summary"]=f"{type(exc).__name__}: {exc}"[:300]
        try:
            pm_xml=pubmed.fetch_xml(pmids)
            _store_provider_result(con,run_id,query,pm_xml,kind="literature_abstract_xml",candidate_id="abstract_xml",
                                  candidate={"pmids":pmids,"provider_status":pm_xml.status})
        except Exception as exc:
            provider_errors["pubmed_abstracts"]=f"{type(exc).__name__}: {exc}"[:300]
    pm_meta={str(r.get("pmid")):r for r in (pm_summary.records if pm_summary else [])}
    pm_abstract={str(r.get("pmid")):r for r in (pm_xml.records if pm_xml else [])}
    pubmed_cards=[]
    for pmid in pmids:
        meta=pm_meta.get(pmid,{})
        article=pm_abstract.get(pmid,{})
        authors=meta.get("authors") or []
        author_names=[]
        for author in authors[:4]:
            if isinstance(author,dict): name=author.get("name") or author.get("authtype") or ""
            else: name=str(author)
            if name: author_names.append(str(name))
        article_ids=meta.get("articleids") or meta.get("articleIdList") or []
        doi=next((str(i.get("value")) for i in article_ids if isinstance(i,dict) and str(i.get("idtype","")).lower()=="doi" and i.get("value")),None)
        pubmed_cards.append({"source":"PubMed","pmid":pmid,"title":article.get("title") or meta.get("title"),
                             "authors":", ".join(author_names)+(" et al." if len(authors)>4 else ""),
                             "journal":meta.get("fulljournalname") or meta.get("source"),
                             "year":str(meta.get("pubdate") or "")[:4] or None,"doi":doi,
                             "source_url":f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/","abstract":None,
                             "licence_note":"PubMed citation metadata; abstract text is not reproduced here. Verify study design and rights at the source."})
    epmc_cards=[]
    for record in (epmc_result.records if epmc_result else [])[:10]:
        pmid=str(record.get("pmid") or "")
        doi=str(record.get("doi") or "")
        source=str(record.get("source") or "MED")
        external_id=str(record.get("id") or record.get("pmcid") or pmid)
        if pmid: url=f"https://europepmc.org/article/MED/{pmid}"
        elif external_id: url=f"https://europepmc.org/article/{source}/{external_id}"
        elif doi: url=f"https://doi.org/{doi}"
        else: url=None
        licence=str(record.get("license") or "").strip()
        abstract=str(record.get("abstractText") or "").strip()
        licensed_abstract=abstract if abstract and licence.upper().startswith(("CC ","CC0")) else None
        authors=str(record.get("authorString") or "")
        epmc_cards.append({"source":"Europe PMC","pmid":pmid or None,"title":record.get("title"),
                           "authors":authors,"journal":record.get("journalTitle"),
                           "year":str(record.get("pubYear") or "") or None,"doi":doi or None,
                           "source_url":url,"abstract":licensed_abstract,
                           "licence_note":(f"Europe PMC record licence: {licence}. Abstract excerpt shown only for a stated Creative Commons/CC0 licence." if licence else
                               "Europe PMC metadata. No abstract reproduced because the record has no explicit open licence."),
                           "is_open_access":record.get("isOpenAccess"),"licence":licence})
    source_statuses=[]
    if pm_search: source_statuses.append(_source_status(pm_search))
    if pm_summary: source_statuses.append(_source_status(pm_summary))
    if pm_xml: source_statuses.append(_source_status(pm_xml))
    if epmc_result: source_statuses.append(_source_status(epmc_result))
    for name,error in provider_errors.items():
        label="Europe PMC" if name.startswith("europepmc") else ("PubMed summary" if name=="pubmed_summary" else ("PubMed abstracts" if name=="pubmed_abstracts" else "PubMed"))
        source_statuses.append({"source":label,"status":"error","error":error,"record_count":0})
    if not pubmed: source_statuses.append({"source":"PubMed","status":"not_configured","error":"Adapter not initialized."})
    if not europe: source_statuses.append({"source":"Europe PMC","status":"not_configured","error":"Adapter not initialized."})
    con.commit()
    return {"query":query,"run_id":run_id,"pubmed":pubmed_cards,"europepmc":epmc_cards,
            "source_statuses":source_statuses,
            "message":"Bibliographic results are research leads, not evidence-quality or efficacy assessments. Open the source and review the original study."}

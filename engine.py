"""Evidence-aware SmartNanoFormulator decision engine.

Literature retrieval in this package is extractive/structured and uses explicit
rules. No numerical ML prediction is made unless validated model artifacts are
installed; none were present in the uploaded project archive.
"""
from __future__ import annotations
import re, sqlite3, statistics, json
from collections import defaultdict
from typing import Any
import unicodedata
import ingredient_store

SALT_TERMS = {"sodium", "hydrochloride", "hydrochloric acid", "hcl", "potassium", "mesylate", "maleate", "fumarate", "succinate", "tartrate", "citrate", "acetate", "besylate", "tosylate", "phosphate", "bromide", "chloride", "sulfate", "sulphate", "hydrate", "monohydrate", "dihydrate"}

def norm_exact(value: object) -> str:
    """Normalize spelling/punctuation while preserving meaningful chemical salts."""
    if value is None:
        return ""
    value = str(value).strip().lower()
    def paren_repl(match):
        inside = match.group(1).strip().lower()
        return " " + inside + " " if any(term in inside for term in SALT_TERMS) else " "
    value = re.sub(r"\(([^()]*)\)", paren_repl, value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("β", "beta").replace("α", "alpha").replace("γ", "gamma")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()

ORGAN_ALIASES = {
    "Knee joint": "Joints (knee)",
    "Joints (knee)": "Knee joint",
}

def _organ_names(name):
    if not name or name == "Any organ":
        return []
    alias = ORGAN_ALIASES.get(name)
    return [name, alias] if alias else [name]

def _organ_clause(name, column="Target_Organ"):
    names = _organ_names(name)
    if not names:
        return "1=1", ()
    if "." in column:
        table_alias, field = column.split(".", 1)
        quoted = f'{table_alias}."{field}"'
    else:
        quoted = f'"{column}"'
    if len(names) == 1:
        return f"{quoted}=?", (names[0],)
    return f"{quoted} IN (?,?)", tuple(names)


CARRIERS = ["Liposome", "Niosome", "Transfersome", "Ethosome", "Phytosome"]
RELEASE_PROFILES = ["Immediate", "Sustained", "Controlled", "Not specified"]
SKIP_COMPONENT_CATEGORIES = {"drug / active", "aqueous medium / buffer"}
INSUFFICIENT = "Insufficient scientific evidence"
DISCLAIMER = ("Research decision-support prototype only. It is not clinically validated, does not provide dosing advice, "
              "and does not establish that a formulation is safe, effective, optimized, or capable of reaching a target tissue. "
              "All candidates require independent scientific review and experimental validation.")
ML_NOT_AVAILABLE = ("No executable trained-model package was included with the uploaded project. The ML branch is withheld in this installation. "
                     "Benchmark figures from the project abstract are shown as reported context only; they are not run-time predictions.")


def connect(path="smartnano.db"):
    con = sqlite3.connect(path, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def _table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def lookup_drug(con, name):
    key = norm_exact(name)
    if not key: return None
    r = con.execute("SELECT * FROM drugs WHERE norm_exact=? LIMIT 1", (key,)).fetchone()
    return dict(r) if r else None


def database_stats(con):
    def count(sql): return int(con.execute(sql).fetchone()[0])
    carrier_counts = _rows(con, """
        SELECT Vesicle_Class AS carrier, COUNT(DISTINCT Formulation_ID) AS formulations,
               COUNT(DISTINCT Paper_ID) AS papers
        FROM formulations WHERE Vesicle_Class IN (?,?,?,?,?)
        GROUP BY Vesicle_Class ORDER BY Vesicle_Class
    """, tuple(CARRIERS))
    target_counts = _rows(con, """
        SELECT Target_Organ AS organ, COUNT(DISTINCT Formulation_ID) AS formulations
        FROM formulations GROUP BY Target_Organ ORDER BY Target_Organ
    """)
    organ_stats = {}
    for table, key in (("organ_tissue", "organ_records"), ("nanovesicle_knowledge", "knowledge_statements"),
                       ("modification_matrix", "modification_records"), ("organ_vesicle_evidence", "organ_evidence_rows"),
                       ("organ_targeting_examples", "organ_targeting_examples"), ("organ_scientific_references", "organ_reference_records")):
        organ_stats[key] = count(f'SELECT COUNT(*) FROM "{table}"') if _table_exists(con, table) else 0
    organ_names = [r[0] for r in con.execute("SELECT DISTINCT organ FROM organ_tissue WHERE level='Organ' ORDER BY organ")] if _table_exists(con, "organ_tissue") else []
    return {
        "formulations": count("SELECT COUNT(DISTINCT Formulation_ID) FROM formulations"),
        "papers": count("SELECT COUNT(DISTINCT Paper_ID) FROM formulations"),
        "ingredient_records": count("SELECT COUNT(*) FROM ingredients"),
        "drug_profiles": count("SELECT COUNT(*) FROM drugs"),
        "formulation_drug_names": count("SELECT COUNT(DISTINCT Drug) FROM formulations WHERE Drug IS NOT NULL AND lower(Drug) NOT LIKE 'none%'"),
        "excipient_entries": count("SELECT COUNT(*) FROM excipient_catalogue") if _table_exists(con, "excipient_catalogue") else 0,
        "reference_entries": count("SELECT COUNT(*) FROM scientific_references") if _table_exists(con, "scientific_references") else count("SELECT COUNT(DISTINCT DOI) FROM formulations"),
        "carriers": carrier_counts,
        "organs": organ_names or [r["organ"] for r in target_counts],
        "target_counts": target_counts,
        **organ_stats,
        "routes": [r[0] for r in con.execute("SELECT DISTINCT Route FROM formulations WHERE Route IS NOT NULL ORDER BY Route")],
        "ml_models_available": False,
        "ml_status": ML_NOT_AVAILABLE,
    }


def _parse_property_number(value, code=None):
    """Parse common numeric source text without discarding the original observation."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).replace(",", "")
        if code == "pka_experimental":
            match = re.search(r"p\s*k\s*a\s*(?:=|:)?\s*(-?\d+(?:\.\d+)?)", text, re.I)
            return float(match.group(1)) if match else None
        if code == "solubility_experimental":
            match = re.search(r"(\d+(?:\.\d+)?)\s*(mg\s*/\s*mL|mg\s*/\s*L|g\s*/\s*L|g\s*/\s*mL)", text, re.I)
            if not match:
                return None
            number, unit = float(match.group(1)), re.sub(r"\s+", "", match.group(2).lower())
            return number / 1000 if unit == "mg/l" else number if unit in {"mg/ml", "g/l"} else number * 1000
        return None


def validate(inp, con=None):
    errors, out = [], {}
    out["drug_name"] = str(inp.get("drug_name", "")).strip()[:180]
    if not out["drug_name"]: errors.append("Drug name is required.")
    out["ingredient_id"] = None
    out["form_id"] = None
    try:
        raw_ingredient_id=inp.get("ingredient_id")
        if raw_ingredient_id not in (None, ""):
            out["ingredient_id"]=int(raw_ingredient_id)
            if out["ingredient_id"]<1: raise ValueError
    except (TypeError,ValueError):
        errors.append("Ingredient selection is invalid.")
        out["ingredient_id"]=None
    try:
        raw_form_id=inp.get("form_id")
        if raw_form_id not in (None, ""):
            out["form_id"]=int(raw_form_id)
            if out["form_id"]<1: raise ValueError
    except (TypeError,ValueError):
        errors.append("Chemical-form selection is invalid.")
        out["form_id"]=None
    if out["form_id"] is not None and out["ingredient_id"] is None:
        errors.append("A chemical form must belong to a selected ingredient.")
    if con is not None and out["ingredient_id"] is not None:
        if not _table_exists(con,"ingredient"):
            errors.append("Ingredient catalogue is not initialized.")
        else:
            identity=con.execute("SELECT preferred_name FROM ingredient WHERE ingredient_id=?",(out["ingredient_id"],)).fetchone()
            if not identity:
                errors.append("Selected ingredient no longer exists; search and select it again.")
            else:
                expected=identity[0]
                if out["form_id"] is not None:
                    form=con.execute("SELECT form_name FROM ingredient_form WHERE form_id=? AND ingredient_id=?",
                                     (out["form_id"],out["ingredient_id"])).fetchone()
                    if not form:
                        errors.append("Selected chemical form does not belong to this ingredient.")
                    else:
                        expected=form[0]
                if norm_exact(expected)!=norm_exact(out["drug_name"]):
                    errors.append("Ingredient name does not match the selected catalogue identity; select it again.")
    for key, lo, hi, unit in (("mw",1,5000,"g/mol"),("logp",-10,15,""),("pka",-5,20,""),
                              ("solubility_mg_ml",0,100000,"mg/mL"),("release_target_24h",0,100,"%")):
        raw = inp.get(key)
        if raw in (None, ""):
            out[key] = None
            continue
        try: value = float(raw)
        except (TypeError, ValueError):
            errors.append(f"{key} must be numeric."); out[key] = None; continue
        if not lo <= value <= hi: errors.append(f"{key} must be between {lo} and {hi} {unit}.")
        out[key] = value
    for key, lo, hi, unit in (("tpsa", 0, 2000, "Å²"), ("hbd", 0, 100, "count"), ("hba", 0, 100, "count")):
        raw = inp.get(key)
        if raw in (None, ""):
            out[key] = None
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            errors.append(f"{key} must be numeric."); out[key] = None; continue
        if not lo <= value <= hi:
            errors.append(f"{key} must be between {lo} and {hi} {unit}.")
        out[key] = value
    out["stability"] = str(inp.get("stability") or "").strip()[:500]
    out["ionization"] = str(inp.get("ionization") or "Neutral").strip()
    if out["ionization"] not in {"Neutral", "Weak acid", "Weak base", "Amphoteric", "Unknown"}:
        out["ionization"] = "Unknown"
    out["target_organ"] = str(inp.get("target_organ") or "Skin").strip()
    out["target_tissue"] = str(inp.get("target_tissue") or "Not specified").strip()[:180]
    out["route"] = str(inp.get("route") or "Any route").strip()
    out["release_profile"] = str(inp.get("release_profile") or "Not specified").strip()
    if out["release_profile"] not in RELEASE_PROFILES:
        errors.append("Release profile must be Immediate, Sustained, Controlled or Not specified.")
    out["release_goal"] = str(inp.get("release_goal") or "").strip()[:500]
    out["process_settings"] = str(inp.get("process_settings") or "").strip()[:1000]
    out["notes"] = str(inp.get("notes") or "").strip()[:1000]
    raw_carriers = inp.get("carriers", CARRIERS)
    if raw_carriers is None: raw_carriers = CARRIERS
    if isinstance(raw_carriers, str): raw_carriers = [x.strip() for x in raw_carriers.split(",") if x.strip()]
    out["carriers"] = [x for x in CARRIERS if x in set(raw_carriers)]
    if not out["carriers"]: errors.append("Select at least one supported carrier class.")
    if con is not None:
        organs = {r[0] for r in con.execute("SELECT DISTINCT Target_Organ FROM formulations")}
        if _table_exists(con, "organ_tissue"):
            organs.update(r[0] for r in con.execute("SELECT DISTINCT organ FROM organ_tissue WHERE level='Organ'"))
        # Custom routes are accepted too. If the numerical design engine does not
        # recognize a route, the fallback remains qualitative instead of blocking users.
    return out, errors


def _route_match(requested, observed):
    req = re.sub(r"[^a-z0-9]+", " ", (requested or "").lower()).strip()
    obs = re.sub(r"[^a-z0-9]+", " ", (observed or "").lower()).strip()
    if requested == "Any route": return "route unrestricted"
    if req == obs: return "exact route"
    if req in {"topical", "transdermal"} and obs in {"topical transdermal", "transdermal topical"}:
        return "combined topical/transdermal; not separable"
    return None


def _paper_count(rows):
    return len({str(r.get("Paper_ID")) for r in rows if r.get("Paper_ID") not in (None, "")})


def _record_summary(r):
    fields = ("Formulation_ID","Paper_ID","Batch_ID_Original","Drug","Vesicle_Class","Vesicle_Subtype",
              "Target_Organ","Target_Tissue_Cell_Original","Route","Stated_Ratio_Original","Ratio_Basis_Standardized",
              "Standardization_Status","Surface_Modification","Drug_Loading_EE_DL","Size_PDI_Zeta","Preparation_Method",
              "Key_Findings","Evidence_Level","Evidence_Location","Paper_Title","Year","DOI","URL","composition")
    return {k:r.get(k) for k in fields}


def _catalogue_map(con):
    if not _table_exists(con, "excipient_catalogue"): return {}
    return {norm_exact(r.get("Excipient_Standardized_Name")):r for r in _rows(con,"SELECT * FROM excipient_catalogue")
            if r.get("Excipient_Standardized_Name")}


def _evidence_for_carrier(con, carrier, inp, ingredient_id, form_id, drug_class, catalogue):
    organ_clause, organ_args = _organ_clause(inp["target_organ"], "f.Target_Organ")
    all_rows = _rows(con, f"""
        SELECT f.*, d.cls AS matched_drug_class
        FROM formulations f LEFT JOIN drugs d ON f.drug_norm=d.norm_exact
        WHERE f.Vesicle_Class=? AND {organ_clause}
    """, (carrier,) + organ_args)
    exact_ids=set()
    if ingredient_id is not None and _table_exists(con,"formulation_ingredient_link"):
        exact_ids={str(r[0]) for r in con.execute(
            "SELECT DISTINCT formulation_id FROM formulation_ingredient_link WHERE ingredient_id=? AND form_id IS ? AND link_status IN ('matched','matched_form')",
            (ingredient_id,form_id))}
    exact_identity = [r for r in all_rows if str(r.get("Formulation_ID")) in exact_ids]
    def split_route(rows):
        matched, mismatch = [], []
        for r in rows:
            relationship = _route_match(inp["route"], str(r.get("Route") or ""))
            if relationship:
                r["_route_relation"] = relationship
                matched.append(r)
            else: mismatch.append(r)
        return matched, mismatch
    direct, direct_other_route = split_route(exact_identity)
    analog_all = [r for r in all_rows if drug_class and r not in exact_identity and r.get("matched_drug_class") == drug_class]
    analog, analog_other_route = split_route(analog_all)
    other_all=[r for r in all_rows if r not in exact_identity and r not in analog_all]
    other_context,other_context_other_route=split_route(other_all)

    if direct:
        pool, basis = direct, "Exact drug records"
        if any(r["_route_relation"] == "combined topical/transdermal; not separable" for r in direct):
            status = "Direct drug match; source combines topical and transdermal"
        elif inp["target_organ"] == "Any organ" and inp["route"] == "Any route": status = "Direct drug match; target organ and route unrestricted"
        elif inp["target_organ"] == "Any organ": status = "Direct drug and route match; target organ unrestricted"
        elif inp["route"] == "Any route": status = "Direct drug and target-organ match; route unrestricted"
        else: status = "Direct drug, target-organ and route match"
    elif analog:
        pool, basis = analog, "Same drug-class records (indirect evidence)"
        if any(r["_route_relation"] == "combined topical/transdermal; not separable" for r in analog):
            status = "Same-class evidence; source combines topical and transdermal"
        elif inp["target_organ"] == "Any organ" and inp["route"] == "Any route": status = "Same-class evidence; target organ and route unrestricted"
        elif inp["target_organ"] == "Any organ": status = "Same-class and route evidence; target organ unrestricted"
        elif inp["route"] == "Any route": status = "Same-class and target-organ evidence; route unrestricted"
        else: status = "Same-class, target-organ and route evidence"
    elif direct_other_route:
        pool, basis, status = [], "Exact drug records with different route", "Direct drug evidence exists, but route does not match"
    elif analog_other_route:
        pool, basis, status = [], "Same-class records with different route", "Same-class evidence exists, but route does not match"
    else:
        pool, basis, status = [], "No drug-specific matched evidence", "No matched drug-specific evidence"

    # Year order is only for browsing; no efficacy ranking is implied.
    shown = sorted(pool, key=lambda r:(-(int(r.get("Year") or 0)), str(r.get("Formulation_ID") or "")))[:5]
    candidate_summaries=[]
    direct_ids={str(r.get("Formulation_ID")) for r in direct}
    analog_ids={str(r.get("Formulation_ID")) for r in analog}
    link_by_form={}
    if shown and ingredient_id is not None and _table_exists(con,"formulation_ingredient_link"):
        marks=",".join("?" for _ in shown)
        link_by_form={str(row["formulation_id"]):dict(row) for row in con.execute(
            f"SELECT formulation_id,raw_ingredient_name,match_method,review_status FROM formulation_ingredient_link WHERE ingredient_id=? AND form_id IS ? AND formulation_id IN ({marks})",
            (ingredient_id,form_id)+tuple(str(r.get("Formulation_ID")) for r in shown))}
    try:
        catalogue_update_date=con.execute("SELECT MAX(finished_at) FROM catalogue_import_run").fetchone()[0]
    except sqlite3.OperationalError:
        catalogue_update_date=None
    for row in shown:
        item=_record_summary(row); fid=str(row.get("Formulation_ID")); link=link_by_form.get(fid)
        if fid in direct_ids:
            explanation=(f"Direct match: source batch ingredient {link.get('raw_ingredient_name')!r} is linked to selected ingredient ID {ingredient_id}"+
                        (f" and form ID {form_id}" if form_id is not None else "")) if link else f"Direct source-row link to selected ingredient ID {ingredient_id}."
            item.update({"match_class":"direct","why_this_matched":explanation,"match_method":link.get("match_method") if link else "ingredient-ID link",
                         "link_review_status":link.get("review_status") if link else None})
        elif fid in analog_ids:
            item.update({"match_class":"related_same_class","why_this_matched":f"Related evidence only: the source batch belongs to the same legacy drug class ({drug_class}); it is not a direct ingredient match.","match_method":"same drug class"})
        item["catalogue_updated_at"]=catalogue_update_date
        candidate_summaries.append(item)
    result = {
        "carrier":carrier, "status":status, "evidence_basis":basis,
        "direct_batches":len({r["Formulation_ID"] for r in direct}), "direct_papers":_paper_count(direct),
        "analogue_batches":len({r["Formulation_ID"] for r in analog}), "analogue_papers":_paper_count(analog),
        "other_context_batches":len({r["Formulation_ID"] for r in other_context}), "other_context_papers":_paper_count(other_context),
        "other_context_other_route_batches":len({r["Formulation_ID"] for r in other_context_other_route}),
        "direct_other_route_batches":len({r["Formulation_ID"] for r in direct_other_route}),
        "analogue_other_route_batches":len({r["Formulation_ID"] for r in analog_other_route}),
        "catalogue_context_batches":len(all_rows), "catalogue_context_papers":_paper_count(all_rows),
        "candidates":candidate_summaries, "has_matched_evidence":bool(direct or analog),
        "has_direct_evidence":bool(direct),
        "catalogue_updated_at":catalogue_update_date,
    }
    return result, pool


def _summarize_excipients(con, rows, catalogue):
    ids = list(dict.fromkeys(r.get("Formulation_ID") for r in rows if r.get("Formulation_ID")))
    if not ids: return [], []
    placeholders = ",".join("?" for _ in ids)
    exclude = tuple(SKIP_COMPONENT_CATEGORIES)
    found = _rows(con, f"""
        SELECT Name_Standardized, Component_Category, COUNT(DISTINCT Formulation_ID) AS batches,
               COUNT(DISTINCT Paper_ID) AS papers
        FROM ingredients WHERE Formulation_ID IN ({placeholders})
          AND lower(COALESCE(Component_Category,'')) NOT IN (?,?)
        GROUP BY Name_Standardized, Component_Category
        ORDER BY papers DESC, batches DESC, Name_Standardized LIMIT 40
    """, tuple(ids)+exclude)
    excipients=[]
    for r in found:
        c=catalogue.get(norm_exact(r["Name_Standardized"])) or {}
        excipients.append({
            "ingredient":r["Name_Standardized"], "function":r["Component_Category"],
            "batches":r["batches"], "papers":r["papers"],
            "catalogue_function":c.get("Function (as stated in papers + dataset category)"),
            "compatibility_evidence":c.get("Compatibility_Evidence (reviewed sources; not universal)"),
            "identity":c.get("Chemical_Identity (PubChem formula; MW; IUPAC)"),
            "evidence_note":"Observed in retrieved publications; frequency is not proof of drug-specific compatibility."
        })

    observations=_rows(con, f"""
        SELECT Name_Standardized AS ingredient, Unit_Original AS unit, Unit_Class AS unit_class,
               Quantity_Numeric AS value, Quantity_Numeric_High AS value_high,
               Quantity_Original AS original, Formulation_ID, Paper_ID, Ingredient_Record_ID
        FROM ingredients WHERE Formulation_ID IN ({placeholders}) AND Quantity_Numeric IS NOT NULL
          AND lower(COALESCE(Component_Category,'')) NOT IN (?,?)
    """, tuple(ids)+exclude)
    groups=defaultdict(list)
    for r in observations: groups[(r["ingredient"],r["unit"],r["unit_class"])].append(r)
    ratios=[]
    for (ingredient,unit,unit_class),records in groups.items():
        vals=[float(r["value"]) for r in records if r["value"] is not None]
        if not vals: continue
        papers=len({r["Paper_ID"] for r in records})
        ratios.append({"ingredient":ingredient,"unit":unit,"unit_class":unit_class,
            "min":min(vals),"median":statistics.median(vals),"max":max(vals),
            "batches":len({r["Formulation_ID"] for r in records}),"papers":papers,
            "observations":len(records),"records_with_upper_range":sum(r.get("value_high") is not None for r in records),
            "note":"One publication only; descriptive example, not an optimized or generalizable range." if papers<2 else "Descriptive published values only; not an optimized formulation range."})
    ratios.sort(key=lambda x:(-x["papers"],-x["batches"],x["ingredient"],str(x["unit"])))
    return excipients[:30],ratios[:60]


def _references(con, rows):
    dois=list(dict.fromkeys(r.get("DOI") for r in rows if r.get("DOI") not in (None,"","nan")))
    source_rows=[]
    if dois and _table_exists(con,"scientific_references"):
        marks=",".join("?" for _ in dois)
        source_rows=_rows(con,f"SELECT * FROM scientific_references WHERE DOI IN ({marks})",tuple(dois))
    by_doi={str(r.get("DOI")):r for r in source_rows}
    for r in rows:
        doi=str(r.get("DOI") or "")
        if doi and doi not in by_doi:
            by_doi[doi]={"Title":r.get("Paper_Title"),"Year":r.get("Year"),"DOI":doi,
                "DOI_Link":f"https://doi.org/{doi}","Full_Text_URL":r.get("URL"),"Evidence_Level":r.get("Evidence_Level")}
    out=[]
    for doi,r in by_doi.items():
        out.append({"title":r.get("Title"),"authors":r.get("Authors"),"journal":r.get("Journal"),
            "year":r.get("Year"),"doi":doi,"doi_url":r.get("DOI_Link") or f"https://doi.org/{doi}",
            "url":r.get("Full_Text_URL"),"evidence_level":r.get("Evidence_Level (formulation papers)") or r.get("Evidence_Level"),
            "verification":r.get("DOI_Verification")})
    out.sort(key=lambda r:(-(int(r.get("year") or 0)),str(r.get("title") or "")))
    return out


def _endpoint_report(candidate_rows):
    unique={r.get("Formulation_ID"):r for r in candidate_rows}
    rows=list(unique.values())
    size=[r for r in rows if r.get("Size_PDI_Zeta") not in (None,"","Not reported")]
    ee=[r for r in rows if r.get("Drug_Loading_EE_DL") not in (None,"","Not reported")]
    release=[r for r in rows if "release" in str(r.get("Key_Findings") or "").lower()]
    def samples(src,field):
        return [{"formulation_id":r.get("Formulation_ID"),"reported_value":r.get(field),
            "paper_title":r.get("Paper_Title"),"year":r.get("Year"),"doi":r.get("DOI"),
            "evidence_location":r.get("Evidence_Location"),"route":r.get("Route"),
            "target_tissue":r.get("Target_Tissue_Cell_Original")} for r in src[:8]]
    return [
        {"endpoint":"Particle size / PDI / zeta potential","ml_status":"Withheld - no executable model package",
         "literature_status":"Published observations available" if size else INSUFFICIENT,
         "note":"The supplied source stores these measurements together as originally reported; no unsupported parsing or normalization is performed.","examples":samples(size,"Size_PDI_Zeta")},
        {"endpoint":"Entrapment efficiency / drug loading","ml_status":"Withheld - no executable model package",
         "literature_status":"Published observations available" if ee else INSUFFICIENT,
         "note":"Values are shown in source wording and units; they are not predictions for the requested drug unless the batch is an exact match.","examples":samples(ee,"Drug_Loading_EE_DL")},
        {"endpoint":"24-hour release","ml_status":"Withheld - no executable model package",
         "literature_status":"Related release statements retrieved" if release else INSUFFICIENT,
         "note":"No dedicated standardized 24-hour release field is supplied. Key-findings text is surfaced only when it mentions release; it is not converted into a numerical estimate.","examples":samples(release,"Key_Findings")}
    ]


def _organ_context(con, inp, ingredient_id, form_id, drug_class):
    """Retrieve organ biology and organ-targeting examples as a separate evidence branch."""
    if inp["target_organ"] == "Any organ" or not _table_exists(con, "organ_tissue"):
        return {"available": False, "note": "No single target organ was selected; organ-specific evidence is not collapsed across organs."}
    aliases = _organ_names(inp["target_organ"])
    marks = ",".join("?" for _ in aliases)
    biology = _rows(con, f"SELECT * FROM organ_tissue WHERE organ IN ({marks}) ORDER BY CASE level WHEN 'Organ' THEN 0 WHEN 'Tissue / structure' THEN 1 WHEN 'Cell type' THEN 2 ELSE 3 END, tissue_or_structure", tuple(aliases))
    # Keep a concise but source-linked set for the analysis report.
    requested_tissue = norm_exact(inp.get("target_tissue"))
    if requested_tissue and requested_tissue != norm_exact("Not specified"):
        exact = [r for r in biology if requested_tissue == norm_exact(r.get("tissue_or_structure"))]
        partial = [r for r in biology if requested_tissue in norm_exact(r.get("tissue_or_structure", "")) or norm_exact(r.get("tissue_or_structure", "")) in requested_tissue]
        unique = {}
        for r in exact + partial + [r for r in biology if r.get("level") == "Organ"]:
            unique[r.get("record_id") or repr(r)] = r
        relevant = list(unique.values())
    else:
        relevant = biology
    biology_fields = ("record_id", "level", "organ", "tissue_or_structure", "parent_tissue", "cell_types", "anatomy", "biological_environment", "barriers", "receptors_and_markers", "charge_and_ecm", "local_vs_systemic_delivery", "administration_routes", "challenges", "targeting_strategies", "relevant_nanovesicles", "excipient_and_surface_considerations", "evidence_level", "source_ids", "source_citations")
    biology_out = [{k:r.get(k) for k in biology_fields} for r in relevant[:20]]
    matrix = _rows(con, f"SELECT * FROM organ_vesicle_evidence WHERE target_organ IN ({marks}) ORDER BY vesicle_class", tuple(aliases))
    organ_links={int(r[0]):dict(r) for r in con.execute("SELECT source_rowid,ingredient_id,form_id,link_status FROM organ_ingredient_link")} if _table_exists(con,"organ_ingredient_link") else {}
    class_by_ingredient={int(r[0]):r[1] for r in con.execute("SELECT i.ingredient_id,d.cls FROM ingredient i LEFT JOIN drugs d ON d.name=i.legacy_drug_name")}
    examples_by_carrier = []
    all_refs = {}
    selected_carriers = inp.get("carriers") or CARRIERS
    for carrier in selected_carriers:
        rows = _rows(con, f"SELECT rowid AS _source_rowid,* FROM organ_targeting_examples WHERE target_organ IN ({marks}) AND vesicle_class=?", tuple(aliases) + (carrier,))
        route_rows, route_mismatch = [], []
        for row in rows:
            relation = _route_match(inp["route"], str(row.get("route") or ""))
            if relation:
                row["_route_relation"] = relation
                route_rows.append(row)
            else:
                route_mismatch.append(row)
        direct, analog, context = [], [], []
        for row in route_rows:
            link=organ_links.get(int(row.get("_source_rowid") or 0),{})
            if (ingredient_id is not None and link.get("link_status")=="matched_exact" and
                int(link.get("ingredient_id") or 0)==int(ingredient_id) and link.get("form_id")==form_id):
                row["_match_reason"]=f"Direct organ-context example: its source label is linked to selected ingredient ID {ingredient_id}; this remains separate from formulation-batch evidence."
                direct.append(row)
                continue
            if (drug_class and link.get("link_status")=="matched_exact" and
                class_by_ingredient.get(int(link.get("ingredient_id") or 0))==drug_class):
                row["_match_reason"]=f"Related organ-context evidence only: the linked ingredient shares drug class {drug_class}; it is not a direct match."
                analog.append(row)
            else:
                row["_match_reason"]="Other-ingredient organ context; not a direct or same-class match."
                context.append(row)
        def sample(row):
            fields = ("target_organ", "target_tissue_or_cell_as_stated_in_paper", "vesicle_class", "drug", "route", "surface_modification", "evidence_level", "key_findings_as_reported_not_a_guarantee_of_targeting", "formulation_id", "paper_id", "paper_title", "year", "doi", "evidence_location")
            out = {k:row.get(k) for k in fields}
            out["route_relation"] = row.get("_route_relation")
            out["match_reason"] = row.get("_match_reason")
            return out
        sort_key = lambda r: (-(int(float(r.get("year") or 0)) if str(r.get("year") or "").replace(".", "", 1).isdigit() else 0), str(r.get("paper_title") or ""))
        direct.sort(key=sort_key); analog.sort(key=sort_key); context.sort(key=sort_key)
        for row in direct + analog:
            doi = str(row.get("doi") or "").strip()
            if doi:
                all_refs[doi] = {"title": row.get("paper_title"), "year": row.get("year"), "doi": doi,
                                 "doi_url": "https://doi.org/" + doi, "url": None, "evidence_level": row.get("evidence_level"),
                                 "verification": "See source workbook scientific reference record"}
        examples_by_carrier.append({
            "carrier": carrier,
            "direct_examples": len(direct), "same_class_examples": len(analog), "other_drug_context_examples": len(context),
            "route_mismatch_examples": len(route_mismatch),
            "direct": [sample(r) for r in direct[:5]],
            "same_class": [sample(r) for r in analog[:5]],
            "context": [sample(r) for r in context[:3]],
            "note": "Organ-targeting examples are a separate literature branch. They are not automatically drug-specific formulation matches, evidence of efficacy, or proof that the carrier reaches the target in a new formulation."
        })
    if all_refs and _table_exists(con, "organ_scientific_references"):
        dois = list(all_refs)
        ref_marks = ",".join("?" for _ in dois)
        ref_rows = _rows(con, f"SELECT * FROM organ_scientific_references WHERE doi IN ({ref_marks})", tuple(dois))
        verified_map = {str(r.get("doi")): r for r in ref_rows}
        for doi, fallback in list(all_refs.items()):
            ref = verified_map.get(doi)
            if ref:
                all_refs[doi] = {"title": ref.get("title"), "authors": ref.get("authors"), "journal": ref.get("journal"),
                                 "year": ref.get("year"), "doi": doi, "doi_url": ref.get("doi_link") or "https://doi.org/" + doi,
                                 "url": ref.get("full_text_url"), "evidence_level": ref.get("evidence_level_formulation_papers"),
                                 "verification": ref.get("doi_verification")}
    mods = _rows(con, "SELECT * FROM modification_matrix WHERE nanovesicle_system IN (%s) ORDER BY nanovesicle_system, modification" % ",".join("?" for _ in selected_carriers), tuple(selected_carriers)) if selected_carriers and _table_exists(con, "modification_matrix") else []
    return {
        "available": True,
        "organ": aliases[0], "organ_aliases": aliases,
        "biology_records": biology_out,
        "organ_evidence_matrix": matrix,
        "targeting_examples_by_carrier": examples_by_carrier,
        "modification_context": mods,
        "references": list(all_refs.values()),
        "data_quality_note": "The organ-specific workbook describes full-text extraction as LLM-assisted. Evidence types and source links are retained, but primary papers should be checked before relying on detailed mechanistic statements. Counts describe the curated source catalogue, not treatment success.",
    }


def analyze(con, raw_input):
    inp,errors=validate(raw_input,con)
    if errors: raise ValueError(" ".join(errors))
    identity=None
    if inp.get("ingredient_id") is not None:
        identity=con.execute("SELECT * FROM ingredient WHERE ingredient_id=?",(inp["ingredient_id"],)).fetchone()
        identity=dict(identity) if identity else None
    selected_name=inp["drug_name"]
    if identity and inp.get("form_id") is not None:
        selected_form=con.execute("SELECT * FROM ingredient_form WHERE form_id=?",(inp["form_id"],)).fetchone()
        selected_name=selected_form["form_name"] if selected_form else selected_name
    elif identity:
        selected_name=identity["preferred_name"]
    drug=lookup_drug(con,(identity or {}).get("legacy_drug_name") or (identity or {}).get("preferred_name")) if identity else None
    drug_key=norm_exact(inp["drug_name"])
    drug_class=drug.get("cls") if drug else None
    identity_properties={}
    if identity and _table_exists(con,"property_observation"):
        for row in _rows(con,"""SELECT p.property_code,p.property_value_json,p.unit,p.observation_kind,p.source_system,p.source_id,
                                      p.source_record_id,p.retrieved_at,p.status,p.source_text,s.record_url,s.licence_note,s.source_label
                               FROM property_observation p LEFT JOIN source_record s ON s.source_record_id=p.source_record_id
                               WHERE p.ingredient_id=? AND p.form_id IS ?
                               ORDER BY CASE WHEN p.retrieved_at IS NULL THEN 1 ELSE 0 END,p.retrieved_at DESC,p.observation_id DESC""",
                            (inp["ingredient_id"],inp.get("form_id"))):
            code=row["property_code"]
            if code not in identity_properties and row["property_value_json"] is not None:
                try: row["value"]=json.loads(row["property_value_json"])
                except (TypeError,json.JSONDecodeError): row["value"]=row["property_value_json"]
                identity_properties[code]=row
    values,provenance={},{}
    property_codes={"mw":"mw","logp":"xlogp_computed","pka":"pka_experimental","solubility_mg_ml":"solubility_experimental"}
    property_sources={}
    for key,code in property_codes.items():
        observation=identity_properties.get(code)
        raw_value=observation.get("value") if observation else None
        source_value = _parse_property_number(raw_value, code)
        user_value=inp.get(key)
        if observation:
            property_sources[key]={k:observation.get(k) for k in ("unit","observation_kind","source_system","source_id","source_record_id","retrieved_at","status","source_text","record_url","licence_note","source_label")}
            property_sources[key]["value"]=raw_value
        if user_value is not None and source_value is not None and abs(float(user_value)-source_value)<1e-9:
            values[key]=source_value
            provenance[key]=f"{observation['source_system']} {observation.get('source_id') or ''}; {observation['observation_kind']}; {observation.get('retrieved_at') or 'retrieval date n/r'}; {observation['status']}".strip()
        elif user_value is not None:
            values[key]=user_value
            provenance[key]=("User-provided; context only, not used for prediction" if key in ("pka","solubility_mg_ml") else
                              "User-provided; not substituted for an identity-specific source observation")
        elif source_value is not None:
            values[key]=source_value
            provenance[key]=f"{observation['source_system']} {observation.get('source_id') or ''}; {observation['observation_kind']}; {observation.get('retrieved_at') or 'retrieval date n/r'}; {observation['status']}".strip()
        else:
            values[key]=None
            provenance[key]=("No source observation for this exact form; parent properties are not inherited" if inp.get("form_id") else "Not available for the selected catalogue identity")

    for key in ("tpsa", "hbd", "hba"):
        entered = inp.get(key)
        try:
            legacy_value = float(drug.get(key)) if drug and drug.get(key) not in (None, "") else None
        except (TypeError, ValueError):
            legacy_value = None
        if entered is not None:
            values[key] = entered
            provenance[key] = "User-provided or auto-filled form value; see the legacy drug profile for source context"
        elif legacy_value is not None:
            values[key] = legacy_value
            provenance[key] = "Local legacy drug-profile table; source context retained in drug profile"
        else:
            values[key] = None
            provenance[key] = "Not available for selected catalogue identity"

    catalogue=_catalogue_map(con)
    carrier_results,all_candidate_rows=[],[]
    for carrier in inp["carriers"]:
        result,pool=_evidence_for_carrier(con,carrier,inp,inp.get("ingredient_id"),inp.get("form_id"),drug_class,catalogue)
        exc,ratios=_summarize_excipients(con,pool,catalogue)
        result["excipients"],result["ratios"]=exc,ratios
        carrier_results.append(result)
        all_candidate_rows.extend(pool)
    # Preserve a fixed display order; evidence volume must not imply a carrier ranking.
    carrier_results.sort(key=lambda r:(CARRIERS.index(r["carrier"]) if r["carrier"] in CARRIERS else len(CARRIERS), r["carrier"]))
    refs=_references(con,all_candidate_rows)
    endpoints=_endpoint_report(all_candidate_rows)
    organ_context = _organ_context(con, inp, inp.get("ingredient_id"), inp.get("form_id"), drug_class)
    organ_rows = organ_context.get("biology_records", [])
    organ_example_rows = organ_context.get("targeting_examples_by_carrier", [])
    if inp["target_organ"] != "Any organ" and not organ_rows and not any(
        int(x.get("direct_examples") or 0) + int(x.get("same_class_examples") or 0) + int(x.get("other_drug_context_examples") or 0) > 0
        for x in organ_example_rows
    ):
        organ_context["note"] = (f"No local organ-specific records were retrieved for {inp['target_organ']}. "
                                  "You can still explore AI-assisted formulation hypotheses; these are not evidence of organ targeting.")
    organ_direct = sum(x["direct_examples"] for x in organ_context.get("targeting_examples_by_carrier", []))
    organ_analog = sum(x["same_class_examples"] for x in organ_context.get("targeting_examples_by_carrier", []))
    for rr in organ_context.get("references", []):
        if rr.get("doi") and all(x.get("doi") != rr["doi"] for x in refs): refs.append(rr)
    refs.sort(key=lambda r: (-(int(r.get("year") or 0) if str(r.get("year") or "").isdigit() else 0), str(r.get("title") or "")))
    has_direct=any(c["direct_papers"] for c in carrier_results)
    has_analog=any(c["analogue_papers"] for c in carrier_results)
    if has_direct:
        decision="Direct drug-specific literature candidates were found. Evidence counts help navigate them but do not establish an optimal carrier."
        decision_status="direct_evidence"
    elif organ_direct:
        decision=("The formulation catalogue has no direct drug-specific batch match, but the separate organ-targeting example table contains exact-drug examples. "
                  "Review these as published context; they do not establish a new optimized formulation or a validated carrier choice.")
        decision_status="organ_example_direct"
    elif has_analog or organ_analog:
        decision="Only same-class literature candidates were found. Treat these as indirect hypotheses, not drug-specific recommendations."
        decision_status="analogue_evidence"
    else:
        decision=(f"No direct drug-specific formulation match was retrieved from the included catalogue for {inp['drug_name']} at {inp['target_organ']} by {inp['route']}. "
                  "This is a limitation of the indexed data, not proof that no literature exists. Exploratory AI-assisted candidates are provided separately when available.")
        decision_status="insufficient_evidence"

    gaps=[]
    if not drug: gaps.append("The exact drug identity is not in the physicochemical profile sheet; drug-class analogue matching is unavailable unless an exact class can be established from the literature records.")
    if inp["target_organ"] == "Any organ":
        gaps.append("The request leaves target organ unrestricted; retrieved records span different organs and should not be interpreted as organ-specific matches.")
    if inp["target_tissue"] not in ("", "Not specified"):
        gaps.append("The target-tissue text is retained as a design input, but the supplied dataset's structured matching field is target organ; tissue-level matching is not asserted.")
    if inp["route"] in ("Topical","Transdermal") and any("combined topical/transdermal" in c["status"] for c in carrier_results):
        gaps.append("Some retrieved skin records combine topical and transdermal routes; the source data cannot distinguish them.")
    if not has_direct and has_analog: gaps.append("No direct drug-specific batch was found in the batch catalogue; same-class evidence is indirect.")
    if not has_direct and not has_analog and organ_direct: gaps.append("Exact-drug examples were found only in the separate organ-targeting workbook. These examples have not been merged into the batch-level formulation catalogue.")
    if not has_direct and not has_analog and not organ_direct and not organ_analog: gaps.append("No matched direct or same-class carrier evidence was retrieved from the included catalogue. Any separate design candidates are exploratory hypotheses, not literature-backed recommendations.")
    if inp["release_target_24h"] is not None or inp["release_goal"]:
        gaps.append("The requested release target is a user-defined objective, not an observed measurement. No model or standardized matched 24-hour release endpoint is available in this installation.")
    gaps.append(ML_NOT_AVAILABLE)

    return {
        "input":inp,"drug_profile":({"name":selected_name,"cls":drug_class,"ingredient_id":inp.get("ingredient_id"),"form_id":inp.get("form_id"),**{k:values.get(k2) for k,k2 in (("mw","mw"),("xlogp_computed","logp"),("pka_experimental","pka"),("solubility_experimental","solubility_mg_ml"))},"tpsa":values.get("tpsa"),"hbd":values.get("hbd"),"hba":values.get("hba"),"stability":drug.get("stability_experimental") if drug else None,"source":provenance} if identity else None),
        "values_used":values,"provenance":provenance,"property_sources":property_sources,"drug_class":drug_class,
        "ml":{"available":False,"status":"Withheld","reason":ML_NOT_AVAILABLE,
              "reported_benchmark":{"dataset":"928 screened microfluidic liposome runs (as stated in the supplied abstract)",
                  "particle_size_mae_nm":{"model":17.42,"median_baseline":46.35},
                  "pdi_mae":{"model":0.0376,"median_baseline":0.0518},
                  "scope":"Reported liposome microfluidic benchmark only; not executable in this app build."}},
        "decision":decision,"decision_status":decision_status,"carrier_results":carrier_results,
        "organ_context":organ_context,
        "endpoint_results":endpoints,"references":refs,"gaps":gaps,"disclaimer":DISCLAIMER,
        "method_note":"Carrier cards are organized by direct/analogue batch-evidence counts for navigation only. Organ-targeting examples are shown in a separate evidence branch. The app does not calculate an efficacy score, select a unique optimum, predict optimized ratios, or infer clinical performance."
    }


def save(con,res):
    cur=con.execute("INSERT INTO analyses(title,input,result) VALUES(?,?,?)",
        (f"{res['input']['drug_name']} / {res['input']['target_organ']} / {res['input']['route']}",
         json.dumps(res["input"],ensure_ascii=False),json.dumps(res,ensure_ascii=False)))
    con.commit()
    return cur.lastrowid

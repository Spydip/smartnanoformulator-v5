"""SmartNanoFormulator v5.0.3 — evidence-aware nanovesicle research studio."""
from __future__ import annotations
import base64, hmac, json, os, re, sqlite3, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit
import engine, report, hybrid, design_engine, ingredient_store, ingredient_resolver, sources, analysis_views, llm_explainer, drug_catalogue, design_fallback

HERE = Path(__file__).resolve().parent


def load_local_env_file():
    """Load simple KEY=VALUE settings from .env without overriding real environment variables."""
    path = HERE / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
            value = value[1:-1]
        os.environ[key] = value


load_local_env_file()
DB = os.environ.get("SMARTNANO_DB", str(HERE / "smartnano.db"))
MAX_BODY = 8_000_000
ACCESS_PASSWORD = os.environ.get("APP_ACCESS_PASSWORD", "").strip()
ACCESS_USER = os.environ.get("APP_ACCESS_USER", "mates").strip() or "mates"
SOURCE_CACHE_CON = None
SOURCE_ADAPTERS = {}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def query_one(qs, key, default=""):
    return qs.get(key, [default])[0]


def _llm_user_key(handler):
    """Return a non-secret per-user budget key; the LLM module stores only its hash."""
    raw = handler.headers.get("Authorization", "")
    if raw.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(raw.split(None, 1)[1], validate=True).decode("utf-8")
            username = decoded.split(":", 1)[0].strip()
            if username:
                return "user:" + username
        except (ValueError, UnicodeDecodeError, IndexError):
            pass
    host = handler.client_address[0] if getattr(handler, "client_address", None) else "local"
    return "host:" + str(host)


def parse_library_limit(raw, default=80):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, 1), 180)


def credentials_match(user, password, expected_user, expected_password):
    return hmac.compare_digest(user.encode("utf-8"), expected_user.encode("utf-8")) and hmac.compare_digest(
        password.encode("utf-8"), expected_password.encode("utf-8"))


def hosted_password_error(port_env, password):
    """Require nontrivial authentication whenever a platform PORT implies public binding."""
    if not str(port_env or "").strip():
        return None
    value = str(password or "").strip()
    if len(value) < 16:
        return "Hosted startup requires APP_ACCESS_PASSWORD with at least 16 characters."
    return None


def post_request_error(headers):
    content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return 415, "POST requests require Content-Type: application/json."
    host = headers.get("Host", "").strip()
    if not host:
        return 403, "POST requests require a same-origin Host header."
    origin = headers.get("Origin", "").strip()
    if origin:
        try:
            parsed = urlsplit(origin)
            request = urlsplit("//" + host)
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            request_port = request.port or origin_port
            origin_authority = (parsed.hostname or "").lower().rstrip(".")
            request_authority = (request.hostname or "").lower().rstrip(".")
            forwarded_proto = headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                    or origin_authority != request_authority or origin_port != request_port
                    or (forwarded_proto in ("http", "https") and parsed.scheme != forwarded_proto)):
                return 403, "Cross-origin POST requests are not allowed."
        except ValueError:
            return 403, "Invalid Origin header."
    elif headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return 403, "Cross-origin POST requests are not allowed."
    return None


def organ_aliases(name):
    return engine._organ_names(name)


EXTENDED_ORGANS = [
    "Adipose tissue", "Adrenal glands", "Appendix", "Arteries", "Bladder", "Blood",
    "Blood-brain barrier", "Bone", "Bone marrow", "Brain", "Breast", "Cartilage",
    "Cervix", "Colon", "Cornea", "Ear", "Endometrium", "Esophagus", "Eye",
    "Eye / ocular tissues", "Fallopian tubes", "Gallbladder", "GI tract", "Heart",
    "Immune system", "Intestine", "Kidneys", "Knee joint", "Liver", "Lungs",
    "Lymph nodes", "Lymphatic system", "Mammary gland", "Muscle", "Nasal mucosa",
    "Nervous system", "Oral mucosa", "Ovary", "Pancreas", "Peripheral nerve",
    "Peritoneum", "Placenta", "Prostate", "Rectum", "Retina", "Reproductive system",
    "Salivary glands", "Skeletal muscle", "Skin", "Small intestine", "Spinal cord",
    "Spleen", "Stomach", "Testis", "Thymus", "Thyroid", "Tongue", "Tonsils",
    "Tumors", "Ureter", "Urethra", "Uterus", "Urinary bladder", "Vagina",
    "Vascular system", "Veins", "Wound tissue",
]


def library_query(con, term="", carrier="", organ="", limit=100):
    where, args = [], []
    if term:
        like = f"%{term}%"
        where.append("(Drug LIKE ? OR Vesicle_Class LIKE ? OR Paper_Title LIKE ? OR DOI LIKE ? OR Formulation_ID LIKE ? OR Key_Findings LIKE ? OR composition LIKE ? OR Target_Tissue_Cell_Original LIKE ? OR Preparation_Method LIKE ?)")
        args.extend([like] * 9)
    if carrier:
        where.append("Vesicle_Class=?"); args.append(carrier)
    if organ and organ != "Any organ":
        names = organ_aliases(organ)
        where.append("Target_Organ IN (" + ",".join("?" for _ in names) + ")")
        args.extend(names)
    sql = """SELECT Formulation_ID,Paper_ID,Batch_ID_Original,Drug,Vesicle_Class,Vesicle_Subtype,
      Target_Organ,Target_Tissue_Cell_Original,Route,Stated_Ratio_Original,Ratio_Basis_Standardized,
      Standardization_Status,Surface_Modification,Drug_Loading_EE_DL,Size_PDI_Zeta,Preparation_Method,
      Key_Findings,Evidence_Level,Evidence_Location,Paper_Title,Year,DOI,URL,composition
      FROM formulations"""
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CAST(Year AS INTEGER) DESC, Formulation_ID LIMIT ?"
    args.append(limit)
    return engine._rows(con, sql, tuple(args))


class Handler(BaseHTTPRequestHandler):
    server_version = "SmartNanoFormulator/5.0.3"

    def _headers(self, code, ctype, length, download_name=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        if download_name:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", download_name)
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.end_headers()

    def send_data(self, code, body, ctype="application/json; charset=utf-8", download_name=None):
        data = body if isinstance(body, bytes) else json_bytes(body)
        self._headers(code, ctype, len(data), download_name)
        self.wfile.write(data)

    def authorized(self):
        if not ACCESS_PASSWORD:
            return True
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(hdr.split(" ", 1)[1]).decode("utf-8")
            user, password = decoded.split(":", 1)
        except Exception:
            return False
        return credentials_match(user, password, ACCESS_USER, ACCESS_PASSWORD)

    def guard(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return True
        if self.authorized():
            return True
        body = b'{"error":"Authentication required."}'
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="SmartNanoFormulator", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        return False

    def _open_db(self):
        if not Path(DB).exists():
            raise FileNotFoundError(f"Database not found: {DB}")
        return engine.connect(DB)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/health":
            return self.send_data(200, {"ok": True, "app": "SmartNanoFormulator", "version": "5.0.3"})
        if not self.guard(): return
        if url.path == "/api/export/history":
            con = self._open_db()
            try:
                payload = {"exported_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                           "notice": "Render Free runtime history is disposable and may be lost on restart.",
                           "analyses": engine._rows(con, "SELECT id,created,title,input,result FROM analyses ORDER BY id"),
                           "design_lab_runs": [], "microfluidic_runs": [],
                           "catalogue_import_runs": [], "catalogue_import_items": [],
                           "ingredient_review_queue": [], "ingredient_candidate_results": [],
                           "ingredient_duplicate_flags": [], "ingredient_selection_events": [],
                           "ingredients": [], "forms": [], "aliases": [], "identifiers": [],
                           "property_observations": [], "products": [], "product_ingredients": [], "organ_ingredient_links": [],
                           "source_record_metadata": []}
                if engine._table_exists(con, "design_lab_runs"):
                    payload["design_lab_runs"] = engine._rows(con, "SELECT * FROM design_lab_runs ORDER BY id")
                if engine._table_exists(con, "microfluidic_runs"):
                    payload["microfluidic_runs"] = engine._rows(con, "SELECT * FROM microfluidic_runs ORDER BY id")
                export_queries = {
                    "catalogue_import_runs": "SELECT * FROM catalogue_import_run ORDER BY started_at,run_id",
                    "catalogue_import_items": "SELECT * FROM catalogue_import_item ORDER BY item_id",
                    "ingredient_review_queue": "SELECT queue_id,run_id,category,input_name,normalized_name,material_type,resolution_status,source_system,source_id,source_record_id,retrieved_at,review_status,message FROM ingredient_review_queue ORDER BY queue_id",
                    "ingredient_candidate_results": "SELECT candidate_result_id,run_id,category,normalized_name,source_system,source_id,source_record_id,retrieved_at,resolution_status,message FROM ingredient_candidate_result ORDER BY candidate_result_id",
                    "ingredient_duplicate_flags": "SELECT duplicate_flag_id,run_id,input_name,duplicate_basis,existing_entity,candidate_entity,source_record_id,status FROM ingredient_duplicate_flag ORDER BY duplicate_flag_id",
                    "ingredient_selection_events": "SELECT * FROM ingredient_selection_event ORDER BY selection_id",
                    "ingredients": "SELECT * FROM ingredient ORDER BY ingredient_id",
                    "forms": "SELECT * FROM ingredient_form ORDER BY form_id",
                    "aliases": "SELECT * FROM ingredient_alias ORDER BY alias_id",
                    "identifiers": "SELECT * FROM ingredient_identifier ORDER BY identifier_id",
                    "property_observations": "SELECT * FROM property_observation ORDER BY observation_id",
                    "products": "SELECT * FROM product ORDER BY product_id",
                    "product_ingredients": "SELECT * FROM product_ingredient ORDER BY product_id,ingredient_id,form_id",
                    "organ_ingredient_links": "SELECT * FROM organ_ingredient_link ORDER BY source_rowid",
                    "source_record_metadata": "SELECT source_record_id,source_system,source_id,record_kind,record_url,retrieved_at,licence_note,created_at FROM source_record ORDER BY source_record_id",
                    "llm_usage": "SELECT usage_id,feature,model,input_tokens,output_tokens,estimated_cost_usd,reserved_cost_usd,latency_ms,status,citation_status,price_list_version,created_at,completed_at FROM llm_usage ORDER BY usage_id",
                }
                for key, sql in export_queries.items():
                    table = sql.split(" FROM ",1)[1].split()[0]
                    if engine._table_exists(con, table):
                        payload[key] = engine._rows(con, sql)
                return self.send_data(200, payload, "application/json; charset=utf-8", "smartnano_history_export.json")
            finally:
                con.close()
        if url.path in ("/", "/index.html"):
            return self.send_data(200, (HERE / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
        if url.path == "/favicon.svg":
            path = HERE / "web" / "favicon.svg"
            if path.exists(): return self.send_data(200, path.read_bytes(), "image/svg+xml")
            return self.send_data(404, {"error": "Not found"})
        if url.path == "/web/ingredients.js":
            path = HERE / "web" / "ingredients.js"
            if path.exists(): return self.send_data(200, path.read_bytes(), "application/javascript; charset=utf-8")
            return self.send_data(404, {"error": "Not found"})
        if url.path == "/web/results.js":
            path = HERE / "web" / "results.js"
            if path.exists(): return self.send_data(200, path.read_bytes(), "application/javascript; charset=utf-8")
            return self.send_data(404, {"error": "Not found"})
        try:
            con = self._open_db()
            try:
                qs = parse_qs(url.query)
                if url.path == "/api/ml/template":
                    template = ("run_id,study_id,lab_id,operator_id,chip_type,aqueous_medium,ESM (mg/mL),HSPC (mg/mL),CHOL (mg/mL),PEG (mg/mL),TFR (mL/min),FRR,SIZE (nm),PDI,formation_status,is_synthetic\n"
                                "run-001,study-001,lab-A,operator-1,Micromixer,MQ,1.0,2.0,3.0,0.2,5.0,3.0,120.0,0.12,1,false\n")
                    return self.send_data(200, template.encode("utf-8"), "text/csv; charset=utf-8", "microfluidic_dataset_template.csv")
                if url.path == "/api/ml/status":
                    return self.send_data(200, hybrid.status(con, HERE))
                if url.path == "/api/design/config":
                    k = design_engine.K
                    return self.send_data(200, {
                        "vesicles": k.VESICLES, "routes": {r: list(sites.keys()) for r, sites in k.SITES.items()},
                        "releases": k.RELEASES, "ionization": k.IONIZATION,
                        "calibration_source": k.CALIBRATION_SOURCE, "calibration_counts": k.CALIBRATION_COUNTS,
                        "size_base_nm": k.SIZE_BASE, "pdi_base": k.PDI_BASE, "prior_sd": k.PRIOR_SD,
                        "warning": "Heuristic, literature-calibrated priors; not validated clinical predictions. GP corrections learn only from measured lab entries."})
                if url.path == "/api/design/history":
                    design_engine.ensure_lab_table(con)
                    qs = parse_qs(url.query)
                    summary = design_engine.lab_summary(con, query_one(qs, "drug") or None, query_one(qs, "vesicle") or None)
                    rows = [dict(r) for r in con.execute(
                        "SELECT run_id,created_at,drug,vesicle,route,site,target_organ,release_profile,modifier_pct,lipid_conc_pct,drug_to_lipid,size_nm,pdi,zeta_mv,ee_pct,round_no,notes FROM design_lab_runs ORDER BY id DESC LIMIT 200").fetchall()]
                    return self.send_data(200, {"summary": summary, "rows": rows})
                if url.path == "/api/meta":
                    metadata = engine.database_stats(con)
                    metadata["ingredient_catalogue"] = ingredient_store.catalogue_stats(con)
                    metadata["llm"] = llm_explainer.settings()
                    return self.send_data(200, metadata)
                if url.path == "/api/drugs":
                    return self.send_data(200, [r[0] for r in con.execute("SELECT preferred_name FROM ingredient ORDER BY preferred_name")])
                if url.path == "/api/drugs/search":
                    term = query_one(qs, "q").strip()
                    return self.send_data(200, drug_catalogue.suggest(con, term))
                if url.path == "/api/ingredients/suggest":
                    term=query_one(qs,"q").strip()[:ingredient_resolver.MAX_QUERY]
                    external=query_one(qs,"external").lower() in {"1","true","yes"}
                    adapters=SOURCE_ADAPTERS if external else None
                    return self.send_data(200,ingredient_resolver.suggest(con,term,adapters,force_external=external))
                if url.path == "/api/ingredients/literature":
                    term=query_one(qs,"q").strip()[:ingredient_resolver.MAX_QUERY]
                    return self.send_data(200,ingredient_resolver.search_literature(con,term,SOURCE_ADAPTERS))
                if url.path == "/api/ingredients/library":
                    term=query_one(qs,"q").strip()[:160]
                    limit=parse_library_limit(query_one(qs,"limit","80"),default=80)
                    args=[]; where=""
                    if term:
                        like=f"%{ingredient_store.normalize_name(term)}%"
                        where="WHERE normalized_name LIKE ?"; args.append(like)
                    rows=engine._rows(con,"SELECT ingredient_id,preferred_name,category,material_type,review_status,molecular_weight_policy FROM ingredient "+where+" ORDER BY preferred_name LIMIT ?",tuple(args+[limit]))
                    return self.send_data(200,rows)
                if url.path == "/api/drug":
                    d = engine.lookup_drug(con, query_one(qs, "name"))
                    if not d: return self.send_data(200, {})
                    fields = ("name","cls","mw","xlogp_computed","tpsa","hbd","hba","pka_experimental","solubility_experimental","stability_experimental","source","pubchem_cid","smiles")
                    return self.send_data(200, {k:d.get(k) for k in fields})
                if url.path == "/api/organs":
                    names = [r[0] for r in con.execute("SELECT DISTINCT organ FROM organ_tissue WHERE level='Organ' ORDER BY organ")]
                    names = list(dict.fromkeys(names + EXTENDED_ORGANS))
                    out = []
                    for name in names:
                        aliases = organ_aliases(name)
                        marks = ",".join("?" for _ in aliases)
                        n_tissues = con.execute(f"SELECT COUNT(*) FROM organ_tissue WHERE organ IN ({marks})", aliases).fetchone()[0]
                        n_examples = con.execute(f"SELECT COUNT(*) FROM organ_targeting_examples WHERE target_organ IN ({marks})", aliases).fetchone()[0]
                        out.append({"name": name, "dataset_name": aliases[-1] if len(aliases)>1 else name,
                                    "tissue_records": n_tissues, "targeting_examples": n_examples,
                                    "has_local_evidence": bool(n_tissues or n_examples)})
                    return self.send_data(200, out)
                if url.path == "/api/organ":
                    name = query_one(qs, "name")
                    aliases = organ_aliases(name)
                    if not aliases: return self.send_data(400, {"error": "Choose a target organ."})
                    marks = ",".join("?" for _ in aliases)
                    tissues = engine._rows(con, f"SELECT * FROM organ_tissue WHERE organ IN ({marks}) ORDER BY CASE level WHEN 'Organ' THEN 0 WHEN 'Tissue / structure' THEN 1 WHEN 'Cell type' THEN 2 ELSE 3 END, tissue_or_structure", tuple(aliases))
                    evidence = engine._rows(con, f"SELECT * FROM organ_vesicle_evidence WHERE target_organ IN ({marks}) ORDER BY vesicle_class", tuple(aliases))
                    examples = engine._rows(con, f"SELECT * FROM organ_targeting_examples WHERE target_organ IN ({marks}) ORDER BY CAST(year AS INTEGER) DESC, paper_title LIMIT 80", tuple(aliases))
                    doi_list = list({str(x.get("doi")) for x in examples if x.get("doi")})
                    if doi_list:
                        dm = ",".join("?" for _ in doi_list)
                        ref_rows = engine._rows(con, f"SELECT doi,doi_verification,full_text_url FROM organ_scientific_references WHERE doi IN ({dm})", tuple(doi_list))
                        ref_map = {str(x.get("doi")): x for x in ref_rows}
                        for x in examples:
                            ref = ref_map.get(str(x.get("doi")))
                            if ref:
                                x["doi_verification"] = ref.get("doi_verification")
                                x["full_text_url"] = ref.get("full_text_url")
                    return self.send_data(200, {"organ": name, "aliases": aliases, "tissues": tissues, "evidence": evidence, "examples": examples})
                if url.path == "/api/knowledge":
                    q = query_one(qs, "q").strip()[:160]
                    carrier = query_one(qs, "carrier")
                    topic = query_one(qs, "topic")
                    where, args = [], []
                    if q:
                        like=f"%{q}%"; where.append("(statement LIKE ? OR topic LIKE ? OR source_citations LIKE ? OR basis LIKE ?)"); args += [like]*4
                    if carrier: where.append("nanovesicle_system=?"); args.append(carrier)
                    if topic: where.append("topic=?"); args.append(topic)
                    sql="SELECT * FROM nanovesicle_knowledge"
                    if where: sql += " WHERE " + " AND ".join(where)
                    sql += " ORDER BY nanovesicle_system, topic, kb_id LIMIT 180"
                    rows=engine._rows(con,sql,tuple(args))
                    topics=[r[0] for r in con.execute("SELECT DISTINCT topic FROM nanovesicle_knowledge ORDER BY topic")]
                    return self.send_data(200,{"results":rows,"topics":topics,"total_returned":len(rows)})
                if url.path == "/api/modifications":
                    carrier=query_one(qs,"carrier")
                    where="WHERE nanovesicle_system=?" if carrier else ""
                    args=(carrier,) if carrier else ()
                    return self.send_data(200,engine._rows(con,f"SELECT * FROM modification_matrix {where} ORDER BY nanovesicle_system, modification",args))
                if url.path == "/api/references":
                    q=query_one(qs,"q").strip()[:180]
                    where=""; args=()
                    if q:
                        like=f"%{q}%"; where="WHERE (title LIKE ? OR authors LIKE ? OR journal LIKE ? OR doi LIKE ? OR ref_id LIKE ?)"; args=(like,)*5
                    rows=engine._rows(con,f"SELECT * FROM organ_scientific_references {where} ORDER BY CAST(year AS INTEGER) DESC, title LIMIT 250",args)
                    return self.send_data(200,rows)
                if url.path == "/api/library":
                    term=query_one(qs,"q").strip()[:160]
                    carrier=query_one(qs,"carrier").strip()
                    organ=query_one(qs,"organ").strip()
                    limit=parse_library_limit(query_one(qs,"limit","80"))
                    return self.send_data(200,library_query(con,term,carrier,organ,limit))
                if url.path == "/api/analyses":
                    rows=engine._rows(con,"SELECT id,created,title FROM analyses ORDER BY id DESC LIMIT 100")
                    return self.send_data(200,rows)
                detail_match=re.fullmatch(r"/api/analysis/(\d+)/details",url.path)
                if detail_match:
                    row=con.execute("SELECT id,created,result FROM analyses WHERE id=?",(int(detail_match.group(1)),)).fetchone()
                    if not row: return self.send_data(404,{"error":"Analysis not found."})
                    result=json.loads(row["result"])
                    section=query_one(qs,"section")
                    try:
                        return self.send_data(200,analysis_views.detail(result,section))
                    except ValueError as exc:
                        return self.send_data(400,{"error":str(exc)})
                match=re.fullmatch(r"/api/analysis/(\d+)(/pdf)?",url.path)
                if match:
                    row=con.execute("SELECT id,created,input,result FROM analyses WHERE id=?",(int(match.group(1)),)).fetchone()
                    if not row: return self.send_data(404,{"error":"Analysis not found."})
                    result=json.loads(row["result"])
                    if match.group(2):
                        return self.send_data(200,report.pdf(result,row["id"],row["created"]),"application/pdf",f"SmartNanoFormulator_Report_{row['id']}.pdf")
                    result["analysis_id"]=row["id"]; result["created"]=row["created"]
                    return self.send_data(200,analysis_views.summary(result,row["id"],row["created"]))
                return self.send_data(404,{"error":"Endpoint not found."})
            finally:
                con.close()
        except FileNotFoundError as e:
            return self.send_data(503,{"error":str(e)})
        except (ValueError,sqlite3.Error) as e:
            return self.send_data(500,{"error":f"Request could not be completed: {e}"})

    def do_POST(self):
        if not self.guard(): return
        request_error = post_request_error(self.headers)
        if request_error:
            return self.send_data(request_error[0], {"error": request_error[1]})
        path = urlparse(self.path).path
        if path not in {"/api/analyze", "/api/ml/import", "/api/ml/train", "/api/ml/predict", "/api/ml/precedents", "/api/assistant", "/api/design/recommend", "/api/design/save-results", "/api/ingredients/select"} and not re.fullmatch(r"/api/analysis/\d+/explain", path):
            return self.send_data(404,{"error":"Endpoint not found."})
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length<=0 or length>MAX_BODY: return self.send_data(413,{"error":"Request body is empty or too large (maximum 8 MB)."})
            payload=json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload,dict): return self.send_data(400,{"error":"Request body must be a JSON object."})
            con=self._open_db()
            try:
                explain_match = re.fullmatch(r"/api/analysis/(\d+)/explain", path)
                if explain_match:
                    analysis_id = int(explain_match.group(1))
                    row = con.execute("SELECT id,result FROM analyses WHERE id=?", (analysis_id,)).fetchone()
                    if not row:
                        return self.send_data(404, {"error": "Analysis not found."})
                    saved = json.loads(row["result"])
                    explanation = llm_explainer.explain_analysis(con, analysis_id, saved, _llm_user_key(self))
                    if explanation.get("status") not in {"disabled", "missing_provider_configuration", "model_has_no_maintained_price", "usage_cap_is_zero"}:
                        saved.update({
                            "llm_explanation": explanation.get("answer"),
                            "llm_model": explanation.get("model"),
                            "llm_generated_at": explanation.get("generated_at"),
                            "llm_citation_status": explanation.get("citation_status"),
                            "llm_status": explanation.get("status"),
                            "llm_usage": explanation.get("usage"),
                            "llm_sources": explanation.get("sources", []) if explanation.get("answer") else [],
                        })
                        con.execute("UPDATE analyses SET result=? WHERE id=?", (json.dumps(saved, ensure_ascii=False), analysis_id))
                        con.commit()
                    explanation["analysis_id"] = analysis_id
                    return self.send_data(200, explanation)
                if path == "/api/ingredients/select":
                    candidate_key=str(payload.get("candidate_key") or "")
                    query=str(payload.get("query") or "")
                    selected=ingredient_resolver.select_candidate(con,candidate_key,SOURCE_ADAPTERS,query)
                    return self.send_data(200,selected)
                if path == "/api/analyze":
                    # A catalogue identity improves matching, but manual/free-text names remain usable.
                    try:
                        result=engine.analyze(con,payload)
                    except ValueError as exc:
                        return self.send_data(400,{"error":str(exc)})
                    result["design_hypotheses"] = design_fallback.build_hypotheses(
                        con, payload, result, _llm_user_key(self)
                    )
                    result["analysis_id"]=engine.save(con,result)
                    return self.send_data(200,analysis_views.summary(result,result["analysis_id"]))
                if path == "/api/ml/import":
                    filename = str(payload.get("filename") or "microfluidic_upload.csv")
                    mark_as_holdout = bool(payload.get("mark_as_holdout"))
                    source_group = str(payload.get("source_group") or "")
                    source_group_type = str(payload.get("source_group_type") or "lab_id")
                    concentration_unit = str(payload.get("concentration_unit") or "mg/mL")
                    flow_rate_unit = str(payload.get("flow_rate_unit") or "mL/min")
                    if filename.lower().endswith((".xlsx", ".xlsm")):
                        result = hybrid.parse_and_import_xlsx(
                            con, filename, str(payload.get("file_base64") or ""),
                            mark_as_holdout=mark_as_holdout, source_group=source_group,
                            source_group_type=source_group_type, concentration_unit=concentration_unit,
                            flow_rate_unit=flow_rate_unit)
                    else:
                        result = hybrid.parse_and_import_csv(
                            con, filename, str(payload.get("csv_text") or ""),
                            mark_as_holdout=mark_as_holdout, source_group=source_group,
                            source_group_type=source_group_type, concentration_unit=concentration_unit,
                            flow_rate_unit=flow_rate_unit)
                    if result.get("rows_inserted", 0) > 0 or result.get("rows_reclassified_to_holdout", 0) > 0:
                        hybrid.invalidate_models(HERE)
                    return self.send_data(200,result)
                if path == "/api/ml/train":
                    result=hybrid.train_models(con, HERE)
                    return self.send_data(200,result)
                if path == "/api/ml/predict":
                    result=hybrid.predict(con, HERE, payload)
                    return self.send_data(200,result)
                if path == "/api/ml/precedents":
                    result=hybrid.recommend_precedents(con, payload)
                    return self.send_data(200,result)
                if path == "/api/assistant":
                    result=hybrid.ask_assistant(con, str(payload.get("question") or ""), _llm_user_key(self))
                    return self.send_data(200,result)
                if path == "/api/design/recommend":
                    result = design_engine.make_recommendation(con, payload)
                    # Attach literature precedents as references, not as ML training labels.
                    precedent_payload = {"drug": payload.get("drug"), "organ": payload.get("organ") or "",
                                         "tissue": payload.get("tissue") or "", "route": payload.get("route") or "Any route",
                                         "vesicle_class": result.get("vesicle"), "limit": 6}
                    try:
                        result["literature_precedents"] = hybrid.recommend_precedents(con, precedent_payload).get("results", [])
                    except Exception:
                        result["literature_precedents"] = []
                    return self.send_data(200, result)
                if path == "/api/design/save-results":
                    result = design_engine.save_lab_results(con, payload)
                    return self.send_data(200, result)
            finally: con.close()
        except json.JSONDecodeError:
            return self.send_data(400,{"error":"Request body is not valid JSON."})
        except ValueError as e:
            return self.send_data(400,{"error":str(e)})
        except FileNotFoundError as e:
            return self.send_data(503,{"error":str(e)})
        except sqlite3.Error as e:
            return self.send_data(500,{"error":f"Database error: {e}"})
        except Exception as e:
            return self.send_data(500,{"error":f"Hybrid workflow failed safely: {e}"})

    def log_message(self, fmt, *args):
        if args and str(args[1]).startswith("5"):
            super().log_message(fmt,*args)


if __name__ == "__main__":
    # Ignore blank or whitespace-only PORT values inherited from Windows shells.
    env_port = os.environ.get("PORT", "").strip()
    auth_error = hosted_password_error(env_port, ACCESS_PASSWORD)
    if auth_error:
        print(auth_error, file=sys.stderr)
        sys.exit(2)
    arg_port = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    port = int(env_port or arg_port or "8000")
    default_host="0.0.0.0" if env_port else "127.0.0.1"
    host=os.environ.get("SMARTNANO_HOST",default_host).strip() or default_host
    with engine.connect(DB) as con:
        ingredient_counts = ingredient_store.initialize(con)
        resolver_counts = ingredient_resolver.initialize(con,HERE)
    cache_path = os.environ.get("SMARTNANO_CACHE_DB", "/tmp/smartnano-source-cache.db").strip()
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    SOURCE_CACHE_CON = sqlite3.connect(cache_path, timeout=20, check_same_thread=False)
    SOURCE_CACHE_CON.execute("PRAGMA journal_mode=DELETE")
    sources.initialize(SOURCE_CACHE_CON)
    SOURCE_ADAPTERS = sources.build_adapters(SOURCE_CACHE_CON)
    print(f"SmartNanoFormulator v5.0.3 running at http://{host}:{port}")
    print(f"Database: {DB}")
    print(f"Ingredient store ready: {ingredient_counts}")
    print(f"Ingredient resolver ready: {resolver_counts}")
    print(f"Source adapters ready: {', '.join(SOURCE_ADAPTERS)}")
    print("Shared access password: enabled" if ACCESS_PASSWORD else "Shared access password: disabled (set APP_ACCESS_PASSWORD for hosted use)")
    ThreadingHTTPServer((host,port),Handler).serve_forever()

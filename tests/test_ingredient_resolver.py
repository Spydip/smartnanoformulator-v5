from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

import catalogue_import
import ingredient_resolver
from sources.base import AdapterResult

ROOT = Path(__file__).resolve().parents[1]
SEED_DB = ROOT / "smartnano.db"
RETRIEVED = "2026-11-17T10:00:00+00:00"


def ar(provider, status, query, records=None, raw=None, error=None):
    return AdapterResult(provider, status, query, records or [], RETRIEVED,
                         f"{provider} test attribution", f"{provider} test licence",
                         f"https://example.test/{provider}/{query.replace(' ', '_')}", False,
                         error, raw if raw is not None else records)


class FakePubChem:
    provider = "pubchem"
    def search_name(self, query):
        if query.lower() == "magnesium glycinate":
            return ar("pubchem", "found", query, [{"cid": 84645, "input_name": query}],
                      {"IdentifierList": {"CID": [84645]}})
        return ar("pubchem", "not_found", query, [], {"Fault": {"Message": "No CID found"}})
    def get_properties(self, cid):
        row={"CID":int(cid),"MolecularFormula":"C4H8MgN2O4","MolecularWeight":"172.42",
             "InChIKey":"ABCD-EFGH-IJKL","InChI":"InChI=1S/C4H8MgN2O4",
             "IUPACName":"bis(glycinato)magnesium"}
        return ar("pubchem", "found", str(cid), [row], {"PropertyTable":{"Properties":[row]}})
    def get_synonyms(self, cid):
        row={"CID":int(cid),"Synonym":["MAGNESIUM GLYCINATE","magnesium bisglycinate","Bis(glycinato-N,O)magnesium"]}
        return ar("pubchem", "found", str(cid), [row], {"InformationList":{"Information":[row]}})


class FakeRxNorm:
    provider="rxnorm"
    def search_name(self, query): return ar("rxnorm","not_found",query,[],{"idGroup":{"name":query}})
    def get_concept_properties(self, rxcui): return ar("rxnorm","not_found",rxcui,[],{})


class FakeOpenFDA:
    provider="openfda"
    def search_ingredient(self, query, **kwargs): return ar("openfda","not_found",query,[],{"results":[]})
    def search_brand(self, query, **kwargs): return ar("openfda","not_found",query,[],{"results":[]})


class FakeDailyMed:
    provider="dailymed"
    def search_ingredients(self, query, **kwargs): return ar("dailymed","not_found",query,[],{"data":[]})
    def search_labels(self, query, **kwargs): return ar("dailymed","not_found",query,[],{"data":[]})


class FakeDSLD:
    provider="dsld"
    def search_labels(self, query): return ar("dsld","not_found",query,[],[])
    def ingredient_group_search(self, query): return ar("dsld","not_found",query,[],[])


class FakePubMed:
    provider="pubmed"
    def search(self, query, retmax=10): return ar("pubmed","found",query,[{"pmid":"123"}],{"esearchresult":{"idlist":["123"]}})
    def summary(self, pmids):
        record={"pmid":"123","title":"Example nanoformulation study","fulljournalname":"Journal of Testing",
                "pubdate":"2024 Jan","authors":[{"name":"Lee A"},{"name":"Patel B"}],
                "articleids":[{"idtype":"doi","value":"10.1000/example"}]}
        return ar("pubmed","found","PMID:123",[record],{"result":{"uids":["123"],"123":record}})
    def fetch_xml(self, pmids):
        record={"pmid":"123","title":"Example nanoformulation study","abstract":"Copyrighted PubMed abstract body."}
        return ar("pubmed","found","PMID:123",[record],"<PubmedArticle>recorded xml</PubmedArticle>")


class FakeEuropePMC:
    provider="europepmc"
    def search_chemical(self, query, page_size=10, result_type="core"):
        return ar("europepmc","found",query,[
            {"pmid":"123","title":"Open study","authorString":"Lee A et al.","journalTitle":"Open Journal",
             "pubYear":"2023","doi":"10.2000/open","abstractText":"Licensed open abstract excerpt.","license":"CC BY","isOpenAccess":"Y"},
            {"pmid":"456","title":"Unlicensed abstract","authorString":"Author B","journalTitle":"Journal B",
             "pubYear":"2022","abstractText":"Do not reproduce this abstract.","license":"","isOpenAccess":"N"}
        ],{"resultList":{"result":[]}})


class BrokenLiteratureProvider:
    def __init__(self,provider): self.provider=provider
    def search(self,*args,**kwargs): raise TimeoutError("provider timed out")
    def search_chemical(self,*args,**kwargs): raise TimeoutError("provider timed out")


class BrokenProvider:
    def __init__(self,provider): self.provider=provider
    def __getattr__(self,name):
        def fail(*args,**kwargs): raise TimeoutError("provider timed out")
        return fail


class RxNormDetailFailure(BrokenProvider):
    def search_name(self,query):
        return ar("rxnorm","found",query,[{"rxcui":"161","name":"Creatine"}],{"idGroup":{"rxnormId":["161"]}})


class IngredientResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = sqlite3.connect(SEED_DB)

    @classmethod
    def tearDownClass(cls):
        cls.seed.close()

    def setUp(self):
        self.con=sqlite3.connect(":memory:")
        self.con.row_factory=sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.seed.backup(self.con)
        ingredient_resolver.initialize(self.con,ROOT)
        self.adapters={"pubchem":FakePubChem(),"rxnorm":FakeRxNorm(),"openfda":FakeOpenFDA(),
                       "dailymed":FakeDailyMed(),"dsld":FakeDSLD()}

    def tearDown(self):
        self.con.close()

    def test_source_listed_paracetamol_apap_synonyms_resolve_to_acetaminophen(self):
        result=ingredient_resolver.suggest(self.con,"paracetamol",{})
        self.assertEqual(result["status"],"synonym_match")
        self.assertEqual(len(result["ingredient_candidates"]),1)
        candidate=result["ingredient_candidates"][0]
        self.assertEqual(candidate["canonical_name"].lower(),"acetaminophen")
        alias=self.con.execute("SELECT source_system,source_id,review_status FROM ingredient_alias WHERE normalized_alias='paracetamol' ORDER BY alias_id DESC LIMIT 1").fetchone()
        self.assertEqual(alias["source_system"],"PubChem")
        self.assertEqual(alias["source_id"],"1983")
        self.assertEqual(alias["review_status"],"auto_retrieved_pending_review")
        selected=ingredient_resolver.select_candidate(self.con,candidate["candidate_key"],self.adapters,"paracetamol")
        self.assertEqual(selected["status"],"selected_pending_review")
        event=self.con.execute("SELECT candidate_key,source_system,status FROM ingredient_selection_event ORDER BY selection_id DESC LIMIT 1").fetchone()
        self.assertEqual(event["candidate_key"],candidate["candidate_key"])
        self.assertEqual(event["source_system"],"PubChem")
        self.assertEqual(event["status"],"selected_pending_review")
        apap=ingredient_resolver.suggest(self.con,"APAP",{})
        self.assertEqual(apap["ingredient_candidates"][0]["canonical_name"].lower(),"acetaminophen")

    def test_typo_returns_a_spelling_suggestion_without_autoselection(self):
        result=ingredient_resolver.suggest(self.con,"acetominophen",{})
        self.assertEqual(result["status"],"spelling_suggestion")
        self.assertTrue(result["requires_selection"])
        self.assertEqual(result["ingredient_candidates"][0]["canonical_name"].lower(),"acetaminophen")
        self.assertGreaterEqual(result["ingredient_candidates"][0]["score"],0.8)

    def test_resveratrol_does_not_claim_formulation_evidence_without_a_batch_link(self):
        result=ingredient_resolver.suggest(self.con,"resveratrol",{})
        self.assertEqual(len(result["ingredient_candidates"]),1)
        self.assertFalse(result["ingredient_candidates"][0]["has_formulation_evidence"])
        self.assertEqual(result["ingredient_candidates"][0]["formulation_evidence_count"],0)
        self.assertEqual(result["status"],"ingredient_found_no_formulation_evidence")

    def test_creatine_has_a_dated_source_property_observation(self):
        result=ingredient_resolver.suggest(self.con,"creatine",{})
        candidate=result["ingredient_candidates"][0]
        mw=next((x for x in candidate["properties"] if x["code"]=="mw"),None)
        self.assertIsNotNone(mw)
        self.assertEqual(mw["unit"],"g/mol")
        self.assertEqual(mw["source"],"PubChem")
        self.assertIsNotNone(mw["retrieved_at"])
        self.assertEqual(mw["review_status"],"auto_retrieved_pending_review")

    def test_tylenol_and_panadol_remain_products_separate_from_active_ingredient(self):
        for brand in ("Tylenol","Panadol"):
            result=ingredient_resolver.suggest(self.con,brand,{})
            self.assertEqual(result["status"],"synonym_match")
            self.assertEqual(result["ingredient_candidates"][0]["canonical_name"].lower(),"acetaminophen")
            self.assertGreater(len(result["product_candidates"]),0)
            self.assertTrue(all(p["brand_name"].lower()==brand.lower() for p in result["product_candidates"]))
            selected=ingredient_resolver.select_candidate(self.con,result["product_candidates"][0]["candidate_key"],self.adapters,brand)
            self.assertEqual(selected["status"],"product_selected_no_ingredient_link")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM product_ingredient").fetchone()[0],0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient_selection_event WHERE status='product_selected_no_ingredient_link'").fetchone()[0],2)

    def test_magnesium_glycinate_requires_source_specific_form_choice_and_never_gets_generic_mw(self):
        before=self.con.execute("SELECT COUNT(*) FROM ingredient WHERE lower(preferred_name) LIKE '%magnesium glycinate%' ").fetchone()[0]
        result=ingredient_resolver.suggest(self.con,"magnesium glycinate",self.adapters,force_external=True)
        self.assertEqual(result["status"],"ambiguous_form_requires_selection")
        candidate=result["external_ingredient_candidates"][0]
        self.assertEqual(candidate["cid"],"84645")
        self.assertTrue(candidate["suppress_molecular_weight_until_selected"])
        self.assertIsNone(result["generic_molecular_weight"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient WHERE lower(preferred_name) LIKE '%magnesium glycinate%' ").fetchone()[0],before)
        selected=ingredient_resolver.select_candidate(self.con,"pubchem:84645",self.adapters,"magnesium glycinate")
        self.assertEqual(selected["status"],"selected_pending_review")
        self.assertIsNone(selected["generic_molecular_weight"])
        self.assertIn("no single molecular weight",selected["message"].lower())
        form=selected["selected"]
        self.assertEqual(form["entity_type"],"form")
        selected_mw=next(x for x in form["properties"] if x["code"]=="mw")
        self.assertEqual(selected_mw["unit"],"g/mol")
        self.assertEqual(float(selected_mw["value"]),172.42)
        parent=self.con.execute("SELECT ingredient_id,molecular_weight_policy FROM ingredient WHERE preferred_name='Magnesium Glycinate (unspecified form)' ").fetchone()
        self.assertIsNotNone(parent)
        self.assertEqual(parent["molecular_weight_policy"],"no_single_molecular_weight")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM property_observation WHERE ingredient_id=? AND form_id IS NULL AND property_code='mw'",(parent["ingredient_id"],)).fetchone()[0],0)
        repeated=ingredient_resolver.suggest(self.con,"magnesium glycinate",self.adapters)
        self.assertEqual(repeated["status"],"ambiguous_form_requires_selection")
        self.assertTrue(repeated["ingredient_candidates"][0]["suppress_molecular_weight_until_selected"])

    def test_unknown_name_is_explicitly_not_found(self):
        result=ingredient_resolver.suggest(self.con,"zzq-no-such-compound-2026",{})
        self.assertEqual(result["status"],"ingredient_not_found")
        self.assertFalse(result["ingredient_candidates"])

    def test_external_failures_preserve_local_property_and_name_failed_sources(self):
        adapters={"pubchem":BrokenProvider("pubchem"),"rxnorm":RxNormDetailFailure("rxnorm"),
                  "openfda":BrokenProvider("openfda"),"dailymed":BrokenProvider("dailymed"),
                  "dsld":BrokenProvider("dsld")}
        result=ingredient_resolver.suggest(self.con,"creatine",adapters,force_external=True)
        self.assertEqual(result["ingredient_candidates"][0]["canonical_name"].lower(),"creatine")
        mw=next(x for x in result["ingredient_candidates"][0]["properties"] if x["code"]=="mw")
        self.assertEqual(mw["source"],"PubChem")
        self.assertIsNotNone(mw["retrieved_at"])
        failures={source["source"] for source in result["source_statuses"] if source["status"]=="error"}
        self.assertTrue({"pubchem","rxnorm","openfda","dailymed","nih dsld"}.issubset({x.lower() for x in failures}))

    def test_literature_search_saves_source_records_and_only_displays_explicitly_licensed_abstracts(self):
        adapters={"pubmed":FakePubMed(),"europepmc":FakeEuropePMC()}
        result=ingredient_resolver.search_literature(self.con,"curcumin ethosome",adapters)
        self.assertEqual(len(result["pubmed"]),1)
        self.assertEqual(result["pubmed"][0]["pmid"],"123")
        self.assertIn("10.1000/example",result["pubmed"][0]["doi"])
        self.assertIsNone(result["pubmed"][0]["abstract"])
        self.assertEqual(len(result["europepmc"]),2)
        self.assertEqual(result["europepmc"][0]["abstract"],"Licensed open abstract excerpt.")
        self.assertIsNone(result["europepmc"][1]["abstract"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient_candidate_result WHERE run_id=?",(result["run_id"],)).fetchone()[0],4)
        systems={r[0] for r in self.con.execute("SELECT DISTINCT source_system FROM source_record WHERE source_system IN ('PubMed','Europe PMC')")}
        self.assertEqual(systems,{"PubMed","Europe PMC"})

    def test_rxnorm_selection_records_user_choice_and_attaches_only_a_unique_exact_entity(self):
        source_id=catalogue_import._source_record(
            self.con,"RxNorm","161","concept_candidate@2026-11-17","https://rxnav.nlm.nih.gov/REST/rxcui/161/properties.json",
            RETRIEVED,"RxNorm test licence","RxNorm test attribution",{"rxcui":"161","tty":"IN","name":"Acetaminophen"})
        self.con.execute("INSERT INTO ingredient_candidate_result(run_id,category,normalized_name,source_system,source_id,source_record_id,retrieved_at,candidate_json,resolution_status,message) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         ("resolver-test-run","interactive_search","acetaminophen","RxNorm","161|concept_candidate",source_id,RETRIEVED,
                          json.dumps({"rxcui":"161","tty":"IN","name":"Acetaminophen"}),"external_candidates_pending_review","Selected only on explicit user action."))
        selected=ingredient_resolver.select_candidate(self.con,"rxnorm:161",self.adapters,"acetaminophen")
        self.assertEqual(selected["status"],"selected_linked_pending_review")
        identifier=self.con.execute("SELECT identifier_system,identifier_type,identifier_value,review_status FROM ingredient_identifier WHERE identifier_value='161'").fetchone()
        self.assertEqual(tuple(identifier[:3]),("RxNorm","RxCUI","161"))
        self.assertEqual(identifier["review_status"],"auto_retrieved_pending_review")
        event=self.con.execute("SELECT query,candidate_key,source_system,source_record_id,entity_type,status FROM ingredient_selection_event ORDER BY selection_id DESC LIMIT 1").fetchone()
        self.assertEqual(event["candidate_key"],"rxnorm:161")
        self.assertEqual(event["source_system"],"RxNorm")
        self.assertEqual(event["source_record_id"],source_id)
        self.assertEqual(event["status"],"selected_linked_pending_review")

    def test_literature_failures_name_each_provider(self):
        result=ingredient_resolver.search_literature(
            self.con,"curcumin",{"pubmed":BrokenLiteratureProvider("pubmed"),"europepmc":BrokenLiteratureProvider("europepmc")})
        failed={s["source"] for s in result["source_statuses"] if s["status"]=="error"}
        self.assertEqual(failed,{"PubMed","Europe PMC"})


if __name__=="__main__":
    unittest.main()

import sqlite3
import unittest
from types import SimpleNamespace

import catalogue_import
import ingredient_store

STAMP="2026-11-17T12:00:00+00:00"


def result(provider, status="found", records=None, data=None):
    return SimpleNamespace(provider=provider,status=status,records=records or [],
                           raw_response=data if data is not None else {"fixture":provider},
                           request_url=f"https://fixture.invalid/{provider}",retrieved_at=STAMP,
                           licence_note="Recorded test fixture; no live source terms implied.",
                           attribution=f"Fixture for {provider}",error=None,cache_hit=False)


class FakePubChem:
    def __init__(self):
        self.search_calls=[]
        self.property_calls=[]
    def search_name(self,name):
        self.search_calls.append(name)
        if name=="Ambiguous Compound":
            return result("PubChem","ambiguous",[{"cid":101},{"cid":102}],{"IdentifierList":{"CID":[101,102]}})
        return result("PubChem","found",[{"cid":9001}],{"IdentifierList":{"CID":[9001]}})
    def get_properties(self,cid):
        self.property_calls.append(str(cid))
        return result("PubChem","found",[{
            "CID":int(cid),"MolecularFormula":"C2H6O","MolecularWeight":"46.07",
            "InChI":"InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3",
            "InChIKey":"LFQSCWFLJHTTHZ-UHFFFAOYSA-N","SMILES":"CCO","ConnectivitySMILES":"CCO",
            "MolecularWeight":"46.07","XLogP":-0.1,"TPSA":20.2,
            "HBondDonorCount":1,"HBondAcceptorCount":1,"RotatableBondCount":0,
        }],{"PropertyTable":{"Properties":[{"CID":int(cid)}]}})


class FakeLabelSource:
    def __init__(self,provider,found=False): self.provider=provider; self.found=found; self.calls=[]
    def search_brand(self,name):
        self.calls.append(name)
        recs=[{"id":"FDA-1","set_id":"FDA-1","brand_name":name,"active_ingredient":["Acetaminophen"]}] if self.found else []
        return result(self.provider,"found" if recs else "not_found",recs)
    def search_labels(self,name):
        self.calls.append(name)
        recs=[{"setid":"LABEL-1","title":name,"drug_name":name}] if self.found else []
        return result(self.provider,"found" if recs else "not_found",recs)


class FakeRxNorm:
    def search_name(self,name):
        return result("RxNorm","found",[{"rxcui":"161","query":name}],{"idGroup":{"rxnormId":["161"]}})
    def get_concept_properties(self,rxcui):
        return result("RxNorm","found",[{"rxcui":rxcui,"name":"acetaminophen","tty":"IN"}],
                      {"properties":{"rxcui":rxcui,"name":"acetaminophen","tty":"IN"}})


class CatalogueImportTests(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(":memory:")
        self.con.row_factory=sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(ingredient_store.SCHEMA_SQL)
        self.con.execute("CREATE TABLE formulations(Formulation_ID TEXT, Drug TEXT)")
        self.con.execute("INSERT INTO formulations VALUES('B1','Test Compound')")
        self.adapters={"pubchem":FakePubChem(),
                       "openfda":FakeLabelSource("openFDA",True),
                       "dailymed":FakeLabelSource("DailyMed",False),
                       "dsld":FakeLabelSource("NIH DSLD",False)}

    def tearDown(self):
        self.con.close()

    def test_source_backed_import_keeps_mixtures_ambiguous_names_and_brands_separate(self):
        targets=[
            {"category":"other","name":"Test Compound","material_type":"single_compound","scope_note":""},
            {"category":"other","name":"Ambiguous Compound","material_type":"single_compound","scope_note":""},
            {"category":"other","name":"omega-3","material_type":"mixture","scope_note":"No single molecular weight."},
            {"category":"other","name":"Tylenol","material_type":"product","scope_note":"Brand only."},
        ]
        report=catalogue_import.run_import(self.con,targets,self.adapters)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient").fetchone()[0],1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM product").fetchone()[0],1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM product_ingredient").fetchone()[0],0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient_review_queue").fetchone()[0],3)
        obs=self.con.execute("SELECT property_value_json,source_system,source_id,retrieved_at,status FROM property_observation WHERE property_code='mw'").fetchone()
        self.assertEqual(obs[0],'"46.07"')
        self.assertEqual(tuple(obs[1:4]),("PubChem","9001",STAMP))
        self.assertEqual(obs[4],"auto_retrieved_pending_review")
        self.assertTrue(self.con.execute("SELECT 1 FROM ingredient_identifier WHERE identifier_type='InChI' AND source_id='9001'".replace(" AND source_id='9001'", "")).fetchone())
        self.assertEqual(self.con.execute("SELECT property_value_json FROM property_observation WHERE property_code='smiles'").fetchone()[0],'"CCO"')
        self.assertEqual(self.con.execute("SELECT property_value_json FROM property_observation WHERE property_code='connectivity_smiles'").fetchone()[0],'"CCO"')
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient_identifier WHERE identifier_system='PubChem' AND identifier_type IN ('SMILES','ConnectivitySMILES')").fetchone()[0],0)
        self.assertEqual(report["formulation_batches_added"],0)
        self.assertEqual(report["ingredient_records_total"],1)
        self.assertEqual(report["verified_share"],0.0)
        self.assertEqual(report["categories"]["other"]["verified_share"],0.0)
        self.assertEqual(self.adapters["pubchem"].search_calls,["Test Compound","Ambiguous Compound"])

    def test_repeat_import_is_idempotent_and_reuses_source_complete_local_record(self):
        targets=[{"category":"other","name":"Test Compound","material_type":"single_compound","scope_note":""}]
        first=catalogue_import.run_import(self.con,targets,self.adapters)
        observations=self.con.execute("SELECT COUNT(*) FROM property_observation").fetchone()[0]
        sources=self.con.execute("SELECT COUNT(*) FROM source_record").fetchone()[0]
        calls=len(self.adapters["pubchem"].property_calls)
        second=catalogue_import.run_import(self.con,targets,self.adapters)
        self.assertEqual(first["run_id"],second["run_id"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM property_observation").fetchone()[0],observations)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM source_record").fetchone()[0],sources)
        self.assertEqual(len(self.adapters["pubchem"].property_calls),calls)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient").fetchone()[0],1)

    def test_rxnorm_identifier_requires_single_ingredient_tty(self):
        self.adapters["rxnorm"]=FakeRxNorm()
        target={"category":"catalogue_batch","name":"Test Compound","material_type":"single_compound","scope_note":""}
        catalogue_import.run_import(self.con,[target],self.adapters)
        row=self.con.execute("SELECT identifier_value,review_status,retrieved_at FROM ingredient_identifier WHERE identifier_system='RxNorm' AND identifier_type='RxCUI'").fetchone()
        self.assertEqual(tuple(row),("161","auto_retrieved_pending_review",STAMP))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient_candidate_result WHERE source_system='RxNorm'").fetchone()[0],0)

    def test_shared_connectivity_smiles_does_not_merge_stereospecific_entities(self):
        class StereoPubChem:
            def search_name(self,name):
                cid=11001 if name=="L-form" else 11002
                return result("PubChem","found",[{"cid":cid}],{"IdentifierList":{"CID":[cid]}})
            def get_properties(self,cid):
                cid=int(cid); suffix="L" if cid==11001 else "D"
                row={"CID":cid,"MolecularFormula":"C3H7NO2","MolecularWeight":"89.09",
                     "InChI":f"InChI=1S/C3H7NO2/{suffix}","InChIKey":f"STEREO-{suffix}-UNIQUE",
                     "SMILES":"CC(C(=O)O)N","ConnectivitySMILES":"CC(C(=O)O)N",
                     "IsomericSMILES":f"N[C@@H](C)C(O)=O-{suffix}"}
                return result("PubChem","found",[row],{"PropertyTable":{"Properties":[row]}})
        self.adapters["pubchem"]=StereoPubChem()
        targets=[{"category":"other","name":"L-form","material_type":"single_compound","scope_note":""},
                 {"category":"other","name":"D-form","material_type":"single_compound","scope_note":""}]
        report=catalogue_import.run_import(self.con,targets,self.adapters)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient").fetchone()[0],2)
        self.assertEqual(report["duplicate_flags"],0)
        self.assertEqual(self.con.execute("SELECT COUNT(DISTINCT property_value_json) FROM property_observation WHERE property_code='isomeric_smiles'").fetchone()[0],2)

    def test_parallel_prefetch_is_consumed_without_duplicate_provider_calls(self):
        targets=[{"category":"other","name":"Test Compound","material_type":"single_compound","scope_note":""}]
        pubchem,rxnorm=catalogue_import.prefetch_source_results(self.con,targets,self.adapters,workers=2)
        self.assertIn(("other","test compound"),pubchem)
        catalogue_import.run_import(self.con,targets,self.adapters,prefetched_pubchem=pubchem,prefetched_rxnorm=rxnorm)
        self.assertEqual(self.adapters["pubchem"].search_calls,["Test Compound"])
        self.assertEqual(self.adapters["pubchem"].property_calls,["9001"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM ingredient").fetchone()[0],1)


if __name__=="__main__":
    unittest.main()

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine
import report
import ingredient_store
norm_exact = engine.norm_exact


class SmartNanoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = engine.connect(os.path.join(ROOT, "smartnano.db"))

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def analyze(self, **kwargs):
        selected_name=kwargs.get("drug_name","Ibuprofen")
        identity=self.con.execute("SELECT ingredient_id FROM ingredient WHERE normalized_name=? LIMIT 1",
                                  (ingredient_store.normalize_name(selected_name),)).fetchone()
        data = {
            "drug_name": selected_name,
            "ingredient_id": identity[0] if identity else None,
            "form_id": None,
            "target_organ": "Skin",
            "target_tissue": "Not specified",
            "route": "Topical",
            "release_profile": "Sustained",
            "carriers": engine.CARRIERS,
        }
        data.update(kwargs)
        return engine.analyze(self.con, data)

    def test_actual_dataset_counts(self):
        stats = engine.database_stats(self.con)
        self.assertEqual(stats["formulations"], 1087)
        self.assertEqual(stats["papers"], 118)
        self.assertEqual(stats["ingredient_records"], 5453)
        self.assertEqual(stats["drug_profiles"], 64)
        self.assertEqual(stats["excipient_entries"], 131)

    def test_salts_are_not_normalized_to_parent_drug(self):
        self.assertNotEqual(norm_exact("Diclofenac sodium"), norm_exact("Diclofenac"))
        self.assertEqual(norm_exact("Acetaminophen (APAP)"), norm_exact("Acetaminophen"))

    def test_unknown_drug_abstains(self):
        r = self.analyze(drug_name="Madeupmycin")
        self.assertEqual(r["decision_status"], "insufficient_evidence")
        self.assertIn("not proof that no literature exists", r["decision"])
        self.assertEqual(r["references"], [])
        self.assertTrue(all(not c["has_matched_evidence"] for c in r["carrier_results"]))

    def test_direct_evidence_is_separate_from_analogue_evidence(self):
        r = self.analyze(drug_name="Carvedilol")
        transfer = next(c for c in r["carrier_results"] if c["carrier"] == "Transfersome")
        self.assertGreater(transfer["direct_batches"], 0)
        self.assertEqual(transfer["analogue_batches"], 0)
        self.assertIn("Direct drug", transfer["status"])

    def test_free_text_without_selected_identity_never_creates_direct_batch_match(self):
        data={"drug_name":"Carvedilol","target_organ":"Skin","route":"Topical","carriers":["Transfersome"]}
        r=engine.analyze(self.con,data)
        transfer=r["carrier_results"][0]
        self.assertEqual(transfer["direct_batches"],0)
        self.assertEqual(transfer["direct_papers"],0)

    def test_form_molecular_weight_is_not_inherited_from_parent(self):
        parent=self.con.execute("SELECT ingredient_id FROM ingredient WHERE preferred_name='Diclofenac'").fetchone()[0]
        form=self.con.execute("SELECT form_id,form_name FROM ingredient_form WHERE ingredient_id=? AND form_name='Diclofenac sodium'",(parent,)).fetchone()
        r=self.analyze(drug_name=form["form_name"],ingredient_id=parent,form_id=form["form_id"])
        self.assertIsNone(r["values_used"]["mw"])
        self.assertIn("not inherited",r["provenance"]["mw"])

    def test_selected_pubchem_property_retains_source_date_and_review_status(self):
        r=self.analyze(drug_name="Creatine")
        self.assertIsNotNone(r["values_used"]["mw"])
        self.assertIn("PubChem",r["provenance"]["mw"])
        self.assertIn("auto_retrieved_pending_review",r["provenance"]["mw"])
        self.assertEqual(r["property_sources"]["mw"]["source_system"],"PubChem")
        self.assertIsNotNone(r["property_sources"]["mw"]["retrieved_at"])

    def test_analogue_evidence_is_labelled_indirect(self):
        r = self.analyze(drug_name="Ibuprofen")
        ethosome = next(c for c in r["carrier_results"] if c["carrier"] == "Ethosome")
        self.assertEqual(ethosome["direct_batches"], 0)
        self.assertGreater(ethosome["analogue_batches"], 0)
        self.assertIn("Same-class", ethosome["status"])
        self.assertEqual(r["decision_status"], "analogue_evidence")

    def test_combined_topical_transdermal_is_not_mislabelled_exact(self):
        r = self.analyze(drug_name="Carvedilol")
        c = next(x for x in r["carrier_results"] if x["carrier"] == "Transfersome")
        self.assertIn("combines topical and transdermal", c["status"])

    def test_route_mismatch_does_not_become_route_matched(self):
        r = self.analyze(drug_name="Carvedilol", route="Oral")
        c = next(x for x in r["carrier_results"] if x["carrier"] == "Transfersome")
        self.assertEqual(c["direct_batches"], 0)
        self.assertGreater(c["direct_other_route_batches"], 0)
        self.assertFalse(c["has_matched_evidence"])

    def test_target_organ_is_applied(self):
        skin = self.analyze(drug_name="Carvedilol", target_organ="Skin")
        brain = self.analyze(drug_name="Carvedilol", target_organ="Brain")
        s = next(x for x in skin["carrier_results"] if x["carrier"] == "Transfersome")
        b = next(x for x in brain["carrier_results"] if x["carrier"] == "Transfersome")
        self.assertGreater(s["direct_batches"], 0)
        self.assertEqual(b["direct_batches"], 0)

    def test_no_live_ml_prediction_is_claimed(self):
        r = self.analyze(release_target_24h=65, release_goal="65% at 24 hours")
        self.assertFalse(r["ml"]["available"])
        self.assertIn("Withheld", r["ml"]["status"])
        self.assertIn("Withheld", r["endpoint_results"][0]["ml_status"])
        self.assertTrue(any("user-defined objective" in g for g in r["gaps"]))

    def test_ratio_summaries_keep_units_separate(self):
        r = self.analyze(drug_name="Carvedilol")
        c = next(x for x in r["carrier_results"] if x["carrier"] == "Transfersome")
        keys = [(x["ingredient"], x["unit"], x["unit_class"]) for x in c["ratios"]]
        self.assertEqual(len(keys), len(set(keys)))
        for row in c["ratios"]:
            self.assertLessEqual(row["min"], row["median"])
            self.assertLessEqual(row["median"], row["max"])
            self.assertIn("not an optimized", row["note"].lower())

    def test_user_inputs_are_labelled_and_never_silently_replaced(self):
        r = self.analyze(drug_name="Ibuprofen", mw=210.0, logp=3.2, pka=4.9)
        self.assertEqual(r["values_used"]["mw"], 210.0)
        self.assertIn("User-provided", r["provenance"]["mw"])
        self.assertEqual(r["provenance"]["pka"], "User-provided; context only, not used for prediction")

    def test_validation_rejects_invalid_inputs(self):
        for bad in ({"mw": -3}, {"logp": "abc"}, {"drug_name": ""}, {"carriers": []}):
            with self.assertRaises(ValueError):
                self.analyze(**bad)

    def test_source_text_properties_are_parsed_for_design_inputs(self):
        self.assertEqual(engine._parse_property_number("pKa = 9.38", "pka_experimental"), 9.38)
        self.assertEqual(engine._parse_property_number("14,000 mg/L at 25 °C", "solubility_experimental"), 14.0)

    def test_custom_organ_and_route_are_allowed_for_exploratory_design(self):
        parsed, errors = engine.validate({"drug_name": "Example compound", "target_organ": "Novel tissue", "route": "Experimental local route", "carriers": ["Liposome"]}, self.con)
        self.assertEqual(errors, [])
        self.assertEqual(parsed["target_organ"], "Novel tissue")
        self.assertEqual(parsed["route"], "Experimental local route")

    def test_unknown_drug_has_no_excipients_or_ratios(self):
        r = self.analyze(drug_name="Madeupmycin")
        self.assertTrue(all(c["excipients"] == [] and c["ratios"] == [] for c in r["carrier_results"]))

    def test_pdf_generation(self):
        populated = self.analyze(drug_name="Carvedilol")
        empty = self.analyze(drug_name="Madeupmycin")
        self.assertTrue(report.pdf(populated, 1, "2026-11-15").startswith(b"%PDF"))
        self.assertTrue(report.pdf(empty, 2, "2026-11-15").startswith(b"%PDF"))

    def test_organ_workbook_counts_are_imported(self):
        stats = engine.database_stats(self.con)
        self.assertEqual(stats["organ_records"], 135)
        self.assertEqual(stats["knowledge_statements"], 323)
        self.assertEqual(stats["modification_records"], 30)
        self.assertEqual(stats["organ_evidence_rows"], 66)
        self.assertEqual(stats["organ_targeting_examples"], 315)
        self.assertEqual(stats["organ_reference_records"], 196)

    def test_organ_context_is_separate_from_batch_matching(self):
        r = self.analyze(drug_name="Madeupmycin", target_organ="Brain", route="Any route")
        self.assertEqual(r["decision_status"], "insufficient_evidence")
        self.assertTrue(r["organ_context"]["available"])
        self.assertGreater(len(r["organ_context"]["biology_records"]), 0)
        self.assertGreater(len(r["organ_context"]["organ_evidence_matrix"]), 0)
        self.assertTrue(all(x["direct_examples"] == 0 and x["same_class_examples"] == 0
                            for x in r["organ_context"]["targeting_examples_by_carrier"]))

    def test_knee_joint_alias_is_bridged_without_losing_source_name(self):
        r = self.analyze(drug_name="Madeupmycin", target_organ="Knee joint", route="Any route")
        self.assertEqual(r["organ_context"]["organ"], "Knee joint")
        self.assertIn("Joints (knee)", r["organ_context"]["organ_aliases"])
        self.assertGreater(len(r["organ_context"]["organ_evidence_matrix"]), 0)

    def test_curcumin_brain_niosomal_example_is_drug_matched_and_cited(self):
        r = self.analyze(drug_name="Curcumin", target_organ="Brain", route="Intravenous / parenteral", carriers=["Niosome"])
        n = r["organ_context"]["targeting_examples_by_carrier"][0]
        self.assertGreaterEqual(n["direct_examples"], 1)
        self.assertEqual(n["same_class_examples"], 0)
        self.assertTrue(any(x.get("doi") == "10.1038/s41598-025-95348-5" for x in n["direct"]))

    def test_curcumin_skin_ethosome_direct_counts_and_id_explanation(self):
        r=self.analyze(drug_name="Curcumin",target_organ="Skin",route="Topical",carriers=["Ethosome"])
        carrier=next(x for x in r["carrier_results"] if x["carrier"]=="Ethosome")
        self.assertEqual(carrier["direct_batches"],1)
        self.assertEqual(carrier["direct_papers"],1)
        self.assertTrue(carrier["candidates"][0]["why_this_matched"].startswith("Direct match:"))
        self.assertIn(str(r["input"]["ingredient_id"]),carrier["candidates"][0]["why_this_matched"])
        self.assertTrue(carrier["catalogue_updated_at"])

    def test_organ_example_direct_count_requires_selected_identity_id(self):
        raw={"drug_name":"Curcumin","target_organ":"Brain","target_tissue":"Not specified",
             "route":"Intravenous / parenteral","release_profile":"Not specified","carriers":["Niosome"]}
        unselected=engine.analyze(self.con,raw)
        self.assertEqual(unselected["organ_context"]["targeting_examples_by_carrier"][0]["direct_examples"],0)
        selected=self.analyze(**{k:v for k,v in raw.items() if k not in ("ingredient_id","form_id")})
        self.assertGreaterEqual(selected["organ_context"]["targeting_examples_by_carrier"][0]["direct_examples"],1)

    def test_knowledge_table_is_searchable(self):
        rows = self.con.execute("SELECT COUNT(*) FROM nanovesicle_knowledge WHERE nanovesicle_system='Liposome' AND statement LIKE '%bilayer%'").fetchone()[0]
        self.assertGreater(rows, 0)

    def test_history_save(self):
        r = self.analyze(drug_name="Madeupmycin")
        new_id = engine.save(self.con, r)
        saved = self.con.execute("SELECT result FROM analyses WHERE id=?", (new_id,)).fetchone()
        self.assertIsNotNone(saved)
        loaded = json.loads(saved["result"])
        self.assertEqual(loaded["decision_status"], "insufficient_evidence")
        self.con.execute("DELETE FROM analyses WHERE id=?", (new_id,))
        self.con.commit()


if __name__ == "__main__":
    unittest.main()

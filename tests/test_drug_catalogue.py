import os
import unittest
from unittest.mock import patch

import engine
import drug_catalogue
import design_fallback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DrugCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = engine.connect(os.path.join(ROOT, "smartnano.db"))

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_panadol_paracetamol_and_common_typo_resolve_to_same_active_ingredient(self):
        for query in ("Panadol", "paracetamol", "acetominophen", "acetaminophen"):
            found = drug_catalogue.suggest(self.con, query)["results"]
            self.assertTrue(found, query)
            self.assertEqual(found[0]["canonical_name"], "Acetaminophen")
            self.assertEqual(found[0]["ingredient_id"], 2)
        self.assertTrue(drug_catalogue.suggest(self.con, "Panadol")["results"][0]["brand_warning"])

    def test_common_supplement_synonyms_resolve_when_catalogue_identity_exists(self):
        for query, canonical in (("Vitamin C", "L-ascorbic acid"), ("Vitamin D3", "cholecalciferol"), ("B12", "cyanocobalamin"), ("CoQ10", "coenzyme Q10"), ("Creatine", "creatine")):
            found = drug_catalogue.suggest(self.con, query)["results"]
            self.assertTrue(found, query)
            self.assertEqual(found[0]["canonical_name"].casefold(), canonical.casefold())

    def test_no_match_does_not_offer_weak_random_fuzzy_matches(self):
        found = drug_catalogue.suggest(self.con, "Vitamin C-999")
        self.assertEqual(found["results"], [])

    def test_topical_candidates_are_rule_ranked_with_a_recommended_start_and_uncertainty(self):
        payload = {"drug_name": "Acetaminophen", "target_organ": "Skin", "target_tissue": "Epidermis",
                   "route": "Topical", "release_profile": "Sustained",
                   "carriers": ["Liposome", "Niosome", "Transfersome", "Ethosome", "Phytosome"]}
        result = engine.analyze(self.con, payload)
        hypotheses = design_fallback.build_hypotheses(self.con, payload, result, "test-local")
        candidates = hypotheses["candidates"]
        self.assertEqual(len(candidates), 5)
        self.assertEqual(sum(bool(c.get("recommended_start")) for c in candidates), 1)
        self.assertTrue(candidates[0]["recommended_start"])
        self.assertEqual(candidates[0]["carrier"], hypotheses["recommended_start"])
        scores = [c["rule_screening_score"] for c in candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for candidate in candidates:
            self.assertEqual(candidate["engine_mode"], "prior_only")
            self.assertEqual(len(candidate["proposals"]), 3)
            self.assertTrue(candidate["recipe"]["note"])
            self.assertIn("size_nm_low95", candidate["proposals"][0])

    def test_missing_drug_and_organ_evidence_still_returns_five_hypotheses(self):
        payload = {"drug_name": "New research compound", "target_organ": "Retina", "target_tissue": "Photoreceptors",
                   "route": "Ocular", "release_profile": "Sustained",
                   "carriers": ["Liposome", "Niosome", "Transfersome", "Ethosome", "Phytosome"]}
        result = engine.analyze(self.con, payload)
        hypotheses = design_fallback.build_hypotheses(self.con, payload, result, "test-local")
        self.assertEqual(len(hypotheses["candidates"]), 5)
        self.assertTrue(all(c["status"] == "requires_experimental_validation" for c in hypotheses["candidates"]))
        self.assertFalse(any(c.get("numerical_design_available") for c in hypotheses["candidates"]))

    def test_no_evidence_design_still_calls_ai_hypothesis_mode(self):
        payload = {"drug_name": "New research compound", "target_organ": "Retina", "target_tissue": "Photoreceptors",
                   "route": "Ocular", "release_profile": "Sustained", "carriers": ["Liposome", "Niosome"]}
        result = engine.analyze(self.con, payload)
        fake = {"answer": "Exploratory hypothesis: compare two carrier concepts and characterize them.",
                "status": "success", "model": "qwen3:4b", "sources": [], "usage": {"estimated_cost_usd": 0},
                "citation_status": "hypothesis_only_no_literature_evidence"}
        with patch("design_fallback.hybrid.retrieve_evidence", return_value=[]), \
             patch("design_fallback._internal_knowledge", return_value=[]), \
             patch("design_fallback.llm_explainer.generate", return_value=fake) as generate:
            hypotheses = design_fallback.build_hypotheses(self.con, payload, result, "test-local")
        self.assertEqual(hypotheses["llm_answer"], fake["answer"])
        self.assertTrue(hypotheses["mode"].startswith("Local AI hypothesis generation"))
        self.assertTrue(generate.call_args.kwargs["allow_hypothesis_without_evidence"])
        self.assertEqual(generate.call_args.kwargs["evidence"], [])



if __name__ == "__main__":
    unittest.main()

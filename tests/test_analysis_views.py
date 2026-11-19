import unittest

import analysis_views


class AnalysisViewsTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "analysis_id": 7,
            "created": "2026-11-17T00:00:00Z",
            "input": {"drug_name": "Curcumin", "ingredient_id": 17, "form_id": None},
            "decision": "Direct ingredient-linked batches were found.",
            "decision_status": "direct_evidence",
            "carrier_results": [
                {
                    "carrier": "Liposome", "status": "Direct match", "direct_batches": 2,
                    "direct_papers": 1, "analogue_batches": 3, "analogue_papers": 2,
                    "other_context_batches": 4, "other_context_papers": 3,
                    "catalogue_context_batches": 100, "catalogue_updated_at": "2026-11-16",
                    "candidates": [{"Formulation_ID": "F1"}], "excipients": [{"ingredient": "X"}],
                },
            ],
            "organ_context": {
                "available": True, "organ": "Skin", "note": "Separate branch.",
                "targeting_examples_by_carrier": [
                    {"direct_examples": 5, "same_class_examples": 6,
                     "other_drug_context_examples": 7, "route_mismatch_examples": 8,
                     "direct": [{"paper_id": "O1"}]},
                ],
                "biology_records": [{"record_id": "B1"}],
            },
            "references": [{"doi": "10.1234/example"}],
            "values_used": {"mw": 368.38},
            "property_sources": {"mw": {"source_system": "PubChem"}},
            "endpoint_results": [{"endpoint": "PDI"}],
            "design_hypotheses": {"mode": "Bayesian / rule-based", "candidates": [{"carrier": "Liposome"}]},
        }

    def test_summary_is_compact_and_keeps_three_batch_branches_separate(self):
        result = analysis_views.summary(self.result)
        self.assertEqual(result["direct_batch_count"], 2)
        self.assertEqual(result["related_batch_count"], 3)
        self.assertEqual(result["other_context_batch_count"], 4)
        self.assertEqual(result["organ_context"]["direct_examples"], 5)
        self.assertEqual(result["organ_context"]["same_class_examples"], 6)
        self.assertEqual(result["organ_context"]["other_ingredient_context_examples"], 7)
        self.assertEqual(result["references_count"], 1)
        self.assertNotIn("candidates", result["carrier_results"][0])
        self.assertNotIn("excipients", result["carrier_results"][0])
        self.assertNotIn("biology_records", result["organ_context"])
        self.assertTrue(result["design_hypotheses_available"])
        self.assertEqual(result["design_candidate_count"], 1)
        self.assertEqual(result["analysis_id"], 7)

    def test_each_detail_section_is_independently_available(self):
        carriers = analysis_views.detail(self.result, "carriers")
        organ = analysis_views.detail(self.result, "organ")
        profile = analysis_views.detail(self.result, "profile")
        design = analysis_views.detail(self.result, "design")
        self.assertEqual(carriers["carrier_results"][0]["candidates"][0]["Formulation_ID"], "F1")
        self.assertEqual(organ["organ_context"]["biology_records"][0]["record_id"], "B1")
        self.assertEqual(profile["property_sources"]["mw"]["source_system"], "PubChem")
        self.assertEqual(design["design_hypotheses"]["candidates"][0]["carrier"], "Liposome")

    def test_invalid_detail_section_is_rejected(self):
        with self.assertRaises(ValueError):
            analysis_views.detail(self.result, "everything")


if __name__ == "__main__":
    unittest.main()

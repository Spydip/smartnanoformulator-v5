import subprocess
import tempfile
import unittest
from pathlib import Path

import report


class TraceableReportTests(unittest.TestCase):
    def test_pdf_has_all_sections_page_count_and_unicode_glyphs(self):
        payload = report.pdf({
            "input": {"drug_name": "Curcumin", "target_organ": "Skin", "target_tissue": "Not specified", "route": "Topical"},
            "decision": "Direct evidence is not a recommendation.",
            "carrier_results": [],
            "organ_context": {"available": False, "note": "No context retrieved."},
            "llm_explanation": "The retrieved source reports an observation [E1].",
            "llm_model": "gpt-5.4-nano",
            "llm_generated_at": "2026-11-17T18:00:00+00:00",
            "llm_citation_status": "validated_supplied_record_ids",
            "llm_sources": [{"id": "E1", "source_type": "direct", "record_id": "FORM-1",
                             "source_id": "PAPER-1", "title": "Curcumin source batch", "doi": "10.1234/source"}],
        }, analysis_id=9, created="2026-11-17")
        self.assertTrue(payload.startswith(b"%PDF"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.pdf"
            path.write_bytes(payload)
            text = subprocess.check_output(["pdftotext", str(path), "-"], text=True)
        for heading in (
            "01 · Cover", "02 · Executive summary", "03 · Ingredient profile",
            "04 · Search and matching", "05 · Evidence overview", "06 · Carrier comparison",
            "07 · Formulation batches", "08 · Experimental results", "09 · Biological evaluation",
            "10 · Release kinetics", "11 · References", "12 · AI interpretation",
            "13 · Limitations", "14 · Appendix and glossary",
        ):
            self.assertIn(heading, text)
        self.assertRegex(text, r"Page \d+ of \d+")
        for glyph in ("±", "µg/mL", "ζ-potential", "α", "β", "γ", "CO₂", "H₂O"):
            self.assertIn(glyph, text)
        self.assertIn("No release time-series recorded.", text)
        self.assertIn("Citation ID crosswalk", text)
        self.assertIn("FORM-1", text)
        self.assertIn("PAPER-1", text)
        self.assertIn("10.1234/source", text)

    def test_long_property_observation_is_excerpted_without_pdf_layout_failure(self):
        payload = report.pdf({
            "input": {"drug_name": "Acetaminophen", "ingredient_id": 2, "target_organ": "Skin", "route": "Topical"},
            "values_used": {},
            "provenance": {},
            "property_sources": {"solubility_mg_ml": {
                "value": "unbroken-observation-" * 500,
                "unit": None,
                "observation_kind": "reported_text",
                "source_system": "PubChem",
                "source_id": "CID 1983",
                "source_record_id": 122,
                "source_text": "long source note " * 500,
                "status": "auto_retrieved_pending_review",
            }},
            "carrier_results": [],
            "organ_context": {"available": False, "note": "No context retrieved."},
        }, analysis_id=10, created="2026-11-18")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "long-source.pdf"
            path.write_bytes(payload)
            text = subprocess.check_output(["pdftotext", str(path), "-"], text=True)
        searchable = " ".join(text.split())
        self.assertIn("excerpt; see source record ID", searchable)
        self.assertIn("CID 1983", searchable)
        self.assertIn("122", searchable)

    def test_report_references_bundled_true_type_unicode_font(self):
        self.assertTrue(report.FONT_REGULAR.is_file())
        self.assertTrue(report.FONT_BOLD.is_file())


if __name__ == "__main__":
    unittest.main()

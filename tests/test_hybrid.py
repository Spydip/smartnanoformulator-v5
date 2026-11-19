import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import hybrid


class HybridWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(str(Path(self.tmp.name) / "test.db"))
        self.db.row_factory = sqlite3.Row
        self.root = Path(self.tmp.name) / "app"
        self.root.mkdir()
        hybrid.ensure_tables(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_import_normalizes_microfluidic_csv(self):
        csv_text = """run_id,study_id,lab,chip,aqueous,ESM (mg/mL),HSPC (mg/mL),CHOL (mg/mL),PEG (mg/mL),TFR (mL/min),FRR,Size (nm),PDI,is_synthetic
r1,doi:paper1,A,Micromixer,MQ,1,2,3,0.1,5,3,120,0.12,false
r2,doi:paper1,A,Micromixer,MQ,1.2,2,3,0.1,5,3,125,0.13,false
r3,doi:paper2,B,Micromixer,PBS,1.4,2,3,0.1,7,5,130,0.14,false
"""
        result = hybrid.parse_and_import_csv(self.db, "runs.csv", csv_text)
        self.assertEqual(result["rows_seen"], 3)
        self.assertEqual(result["rows_inserted"], 3)
        self.assertEqual(result["study_groups"], 2)
        r = self.db.execute("SELECT size_nm,pdi,esm_concentration,flow_rate_ratio,concentration_unit,flow_rate_unit FROM microfluidic_runs WHERE run_id='r1'").fetchone()
        self.assertEqual(tuple(r), (120.0, 0.12, 1.0, 3.0, "mg/mL", "mL/min"))

    def test_duplicate_rows_are_not_double_counted(self):
        csv_text = "run_id,study_id,ESM,TFR,SIZE\nr1,s1,1,5,100\n"
        a = hybrid.parse_and_import_csv(self.db, "runs.csv", csv_text)
        b = hybrid.parse_and_import_csv(self.db, "runs.csv", csv_text)
        self.assertEqual(a["rows_inserted"], 1)
        self.assertEqual(b["rows_inserted"], 0)
        self.assertEqual(b["duplicates"], 1)

    def test_status_reflects_empty_dataset_and_reported_benchmark(self):
        st = hybrid.status(self.db, self.root)
        self.assertEqual(st["dataset"]["rows"], 0)
        self.assertFalse(st["dataset"]["training_ready"])
        self.assertEqual(st["reported_benchmark"]["rows"], 928)
        self.assertIn("not yet", st["reported_benchmark"]["status"])

    def test_retrieval_returns_curcumin_niosomal_brain_record(self):
        # Use the real project DB for this separate evidence retrieval check.
        from pathlib import Path
        project_db = Path(__file__).resolve().parents[1] / "smartnano.db"
        con = sqlite3.connect(str(project_db))
        con.row_factory = sqlite3.Row
        try:
            results = hybrid.retrieve_evidence(con, "Curcumin niosome brain", limit=12)
            self.assertTrue(results)
            corpus = " ".join(str(x.get("data")) for x in results)
            self.assertIn("Curcumin", corpus)
            self.assertTrue(any(x["source_type"] in {"Organ-targeting example", "Formulation batch"} for x in results))
        finally:
            con.close()

    def test_training_refuses_insufficient_rows(self):
        with self.assertRaisesRegex(ValueError, "At least 50 original rows"):
            hybrid.train_models(self.db, self.root)

    def test_training_can_run_grouped_cv_in_isolated_test_db(self):
        import random
        random.seed(10)
        hybrid.ensure_tables(self.db)
        for i in range(90):
            study = f"study-{i // 30}"
            esm = 0.8 + (i % 30) * 0.05
            hspc = 1.0 + (i % 13) * 0.08
            size = 70 + 15 * esm + 7 * hspc + (i % 5)
            pdi = 0.08 + 0.01 * esm + 0.001 * (i % 4)
            raw = f"{{\"row\":{i}}}"
            fp = f"test-fingerprint-{i}"
            self.db.execute("""INSERT INTO microfluidic_runs
                (dataset_version,run_id,study_id,chip_type,aqueous_medium,esm_concentration,hspc_concentration,chol_concentration,peg_concentration,
                 concentration_unit,total_flow_rate,flow_rate_unit,flow_rate_ratio,size_nm,pdi,is_synthetic,raw_json,row_fingerprint,imported_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("test", f"run-{i}", study, "Micromixer", "MQ", esm, hspc, 3.0, 0.1, "mg/mL", 5.0+(i%3), "mL/min", 3.0+(i%2),
                 size, pdi, 0, raw, fp, "2026-11-16T00:00:00+00:00"))
        self.db.commit()
        cards = hybrid.train_models(self.db, self.root)
        self.assertEqual(cards["dataset_rows_original"], 90)
        self.assertEqual(cards["study_groups"], 3)
        self.assertEqual(cards["grouping_column"], "study_id")
        self.assertIn("size_nm", cards["models"])
        self.assertTrue((self.root / cards["models"]["size_nm"]["artifact"]).exists())

    def test_holdout_rows_are_stored_but_excluded_from_training(self):
        training = ["run_id,study_id,lab_id,chip,aqueous,ESM,HSPC,CHOL,PEG,TFR,FRR,SIZE,PDI"]
        for i in range(90):
            lab = f"lab-{i // 30}"
            esm = 0.8 + (i % 30) * 0.05
            hspc = 1.0 + (i % 13) * 0.08
            size = 70 + 15 * esm + 7 * hspc + (i % 5)
            pdi = 0.08 + 0.01 * esm + 0.001 * (i % 4)
            training.append(f"train-{i},same-doi,{lab},Micromixer,MQ,{esm},{hspc},3,0.1,5,3,{size},{pdi}")
        imported = hybrid.parse_and_import_csv(self.db, "training.csv", "\n".join(training))
        self.assertEqual(imported["rows_inserted"], 90)
        holdout = ["run_id,study_id,lab_id,chip,aqueous,ESM,HSPC,CHOL,PEG,TFR,FRR,SIZE,PDI"]
        for i in range(5):
            holdout.append(f"hold-{i},same-doi,lab-0,Micromixer,MQ,1.2,1.4,3,0.1,5,3,95,0.10")
        result = hybrid.parse_and_import_csv(self.db, "wet_lab_validation.csv", "\n".join(holdout), mark_as_holdout=True)
        self.assertEqual(result["rows_inserted"], 5)
        cards = hybrid.train_models(self.db, self.root)
        self.assertEqual(cards["grouping_column"], "lab_id")
        self.assertEqual(cards["dataset_rows_original"], 90)
        self.assertEqual(cards["holdout_rows"], 5)
        ext = cards["models"]["size_nm"]["external_validation"]
        self.assertEqual(ext["rows"], 5)
        self.assertEqual(ext["in_domain_rows"], 5)
        self.assertIsNotNone(ext["mae"])


    def test_upload_level_lab_group_supports_single_doi_dataset_split_by_lab(self):
        # The public microfluidics source merges multiple laboratories. When a
        # file is exported per lab and lacks row-level provenance, users can
        # explicitly assign the real source group at import time.
        csv_a = "run_id,study_id,ESM,HSPC,TFR,FRR,SIZE,PDI\na1,one-doi,1,2,5,3,100,0.1\na2,one-doi,1.1,2.1,5,3,101,0.11"
        csv_b = "run_id,study_id,ESM,HSPC,TFR,FRR,SIZE,PDI\nb1,one-doi,1.2,2.2,5,3,102,0.12\nb2,one-doi,1.3,2.3,5,3,103,0.13"
        a = hybrid.parse_and_import_csv(self.db, "lab_a.csv", csv_a, source_group="Lab_A")
        b = hybrid.parse_and_import_csv(self.db, "lab_b.csv", csv_b, source_group="Lab_B")
        self.assertEqual(a["grouping_column"], "lab_id")
        self.assertEqual(b["grouping_column"], "lab_id")
        st = hybrid.status(self.db, self.root)["dataset"]
        self.assertEqual(st["grouping_column"], "lab_id")
        self.assertEqual(st["study_groups"], 2)
        labels = {r[0] for r in self.db.execute("SELECT DISTINCT lab_id FROM microfluidic_runs")}
        self.assertEqual(labels, {"Lab_A", "Lab_B"})

    def test_same_raw_row_from_distinct_upload_level_labs_is_not_collapsed(self):
        row = "run_id,study_id,ESM,HSPC,TFR,FRR,SIZE,PDI\nlocal-1,same-doi,1,2,5,3,100,0.1"
        a = hybrid.parse_and_import_csv(self.db, "lab_a.csv", row, source_group="Lab_A")
        b = hybrid.parse_and_import_csv(self.db, "lab_b.csv", row, source_group="Lab_B")
        self.assertEqual(a["rows_inserted"], 1)
        self.assertEqual(b["rows_inserted"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM microfluidic_runs").fetchone()[0], 2)
        labs = {x[0] for x in self.db.execute("SELECT DISTINCT lab_id FROM microfluidic_runs")}
        self.assertEqual(labs, {"Lab_A", "Lab_B"})

    def test_marking_duplicate_upload_as_holdout_reclassifies_existing_rows(self):
        csv_text = "run_id,study_id,lab_id,chip,aqueous,ESM,HSPC,SIZE,PDI\nr1,s1,l1,Micromixer,MQ,1,2,100,0.1\n"
        first = hybrid.parse_and_import_csv(self.db, "runs.csv", csv_text)
        self.assertEqual(first["rows_inserted"], 1)
        second = hybrid.parse_and_import_csv(self.db, "wet_lab_validation.csv", csv_text, mark_as_holdout=True)
        self.assertEqual(second["rows_inserted"], 0)
        self.assertEqual(second["rows_reclassified_to_holdout"], 1)
        stored = self.db.execute("SELECT is_holdout FROM microfluidic_runs WHERE run_id='r1'").fetchone()[0]
        self.assertEqual(stored, 1)

    def test_import_rejects_nonphysical_particle_values(self):
        csv_text = "run_id,study_id,ESM,HSPC,SIZE,PDI\nr1,s1,1,2,100,1.2\n"
        with self.assertRaisesRegex(ValueError, "PDI must be between 0 and 1"):
            hybrid.parse_and_import_csv(self.db, "bad.csv", csv_text)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM microfluidic_runs").fetchone()[0], 0)

    def test_precedent_ranker_returns_real_catalogue_batches(self):
        project_db = Path(__file__).resolve().parents[1] / "smartnano.db"
        con = sqlite3.connect(str(project_db)); con.row_factory = sqlite3.Row
        try:
            result = hybrid.recommend_precedents(con, {"drug": "Curcumin", "vesicle_class": "Niosome", "limit": 5})
            self.assertGreater(result["total_matches"], 0)
            self.assertTrue(all(x.get("Formulation_ID") for x in result["results"]))
            self.assertIn("not a trained ML prediction", result["method"])
        finally:
            con.close()

    def test_explicit_header_units_must_match_selected_units(self):
        csv_text = "run_id,ESM (mg/mL),HSPC (mg/mL),TFR (mL/min),SIZE,PDI\nr1,1,2,5,100,0.1"
        with self.assertRaisesRegex(ValueError, "headers specify mg/mL"):
            hybrid.parse_and_import_csv(self.db, "runs.csv", csv_text, concentration_unit="mM")

    def test_status_marks_missing_input_units_as_not_ready(self):
        self.db.execute("""INSERT INTO microfluidic_runs
            (dataset_version,run_id,study_id,esm_concentration,total_flow_rate,raw_json,row_fingerprint,imported_at)
            VALUES ('legacy-missing','r1','study-1',1.0,5.0,'{}','missing-unit-fp','2026-11-16T00:00:00+00:00')""")
        self.db.commit()
        ds = hybrid.status(self.db, self.root)["dataset"]
        self.assertTrue(ds["unit_conflict"])
        self.assertFalse(ds["training_ready"])
        self.assertIsNone(ds["input_units"])

    def test_training_refuses_mixed_input_units(self):
        rows = ["run_id,study_id,ESM,HSPC,TFR,FRR,SIZE,PDI"]
        for i in range(60):
            rows.append(f"r{i},s{i//20},{1+i/100},{2+i/100},5,3,{100+i},0.1")
        hybrid.parse_and_import_csv(self.db, "mgml.csv", "\n".join(rows), concentration_unit="mg/mL", flow_rate_unit="mL/min")
        # A second upload with the same study groups but different units must not be pooled.
        rows2 = ["run_id,study_id,ESM,HSPC,TFR,FRR,SIZE,PDI"]
        for i in range(60):
            rows2.append(f"x{i},s{i//20},{1+i/100},{2+i/100},5,3,{110+i},0.11")
        hybrid.parse_and_import_csv(self.db, "molar.csv", "\n".join(rows2), concentration_unit="mM", flow_rate_unit="mL/h")
        with self.assertRaisesRegex(ValueError, "mixed input units"):
            hybrid.train_models(self.db, self.root)

    def test_legacy_v31_values_are_migrated_with_legacy_units(self):
        self.db.execute("""INSERT INTO microfluidic_runs
            (dataset_version,run_id,esm_mM,total_flow_rate_ml_h,raw_json,row_fingerprint,imported_at)
            VALUES ('legacy','old-run',1.2,6.0,'{}','legacy-fp','2026-11-16T00:00:00+00:00')""")
        self.db.commit()
        hybrid.ensure_tables(self.db)
        row = self.db.execute("SELECT esm_concentration,total_flow_rate,concentration_unit,flow_rate_unit FROM microfluidic_runs WHERE run_id='old-run'").fetchone()
        self.assertEqual(tuple(row), (1.2, 6.0, "mM", "mL/h"))

    def test_import_invalidation_removes_stale_model_cards(self):
        artifact_dir = self.root / "ml_models" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "size_nm.joblib").write_bytes(b"stale")
        (artifact_dir / "model_cards.json").write_text("{}", encoding="utf-8")
        hybrid.invalidate_models(self.root)
        self.assertFalse((artifact_dir / "size_nm.joblib").exists())
        self.assertFalse((artifact_dir / "model_cards.json").exists())

    def test_research_assistant_can_generate_hypothesis_when_local_retrieval_is_empty(self):
        import hybrid
        con = sqlite3.connect(":memory:")
        try:
            fake = {"answer": "Exploratory hypothesis: compare carrier designs in a controlled screen.",
                    "status": "success", "model": "qwen3:4b", "sources": [], "usage": {"estimated_cost_usd": 0}}
            with patch("hybrid.retrieve_evidence", return_value=[]), \
                 patch("hybrid.llm_explainer.generate", return_value=fake) as generate:
                result = hybrid.ask_assistant(con, "Suggest research directions for retinal nanovesicles")
            self.assertEqual(result["answer"], fake["answer"])
            self.assertIn("no local literature retrieved", result["mode"].lower())
            self.assertTrue(generate.call_args.kwargs["allow_hypothesis_without_evidence"])
        finally:
            con.close()



if __name__ == "__main__":
    unittest.main()
